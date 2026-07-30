# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`midea_refresh_tokens.py --only-if-due`: a token refresh that may run at every
  start without running against the units every time.** The weekly cron job
  (`0 3 * * 0`) only helps if the machine happens to be on at that moment; a host
  switched off on Sunday night skips the refresh **silently**, and expired tokens
  are the most common way this tool stops working in the field. The new flag makes
  an `@reboot` catch-up line safe to add:

  ```cron
  @reboot sleep 120 && cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all --only-if-due >> refresh.log 2>&1
  ```

  A new file `refresh_state.json` next to `devices.json` records when a full
  refresh last **started** and last **succeeded**; every `--all` run keeps it up to
  date, including the one the installer performs during setup. `--only-if-due`
  refreshes only when the last complete refresh is at least 6 days old **and** the
  last attempt at least 24 hours old — otherwise it prints one line and exits 0
  without reading `devices.json`, importing `msmart-ng` or touching the network.

  Two guards rather than one, because neither covers the other's case: `@reboot`
  fires whenever the *cron daemon* starts — a package upgrade of cron does it too —
  and the success timestamp alone would not stop the repetition, because it stays
  old for exactly as long as no run succeeds. That is the situation in which
  repeated attempts do the most harm to a unit that tolerates a single local
  connection.

  Suggested by @muibusan in issue #4, which asked for systemd timer units. The
  timer part was declined for now — cron has to stay for Alpine and macOS, so it
  would mean a second scheduling backend permanently — but `Persistent=true` was
  the one genuinely functional advantage in that request, and this delivers that
  advantage without the second backend.

  Details of the state file: timestamps only, no secrets, yet written `chmod 600`
  through the same atomic path as `devices.json`; a failure to write it is reported
  and never changes the exit code of the refresh itself; anything missing,
  unreadable or implausible in it counts as "due". A timestamp in the future — a
  machine without an RTC, whose clock is only right after NTP — is resolved in
  opposite directions per field: the success field counts as unusable (refresh),
  and the attempt field blocks only within a symmetric window (up to one retry
  interval ahead, a clock nudged back after a real run) but not beyond it (an
  implausibly far-future stamp, which must not silence the catch-up on the very
  host it is for). `--only-if-due` requires `--all` and exits 1 with `--name`,
  because the recorded state is a statement about the whole fleet.

- **`install.sh` offers the catch-up line as a fourth managed cron line.** A new
  installation gets it together with the other three; an installation that already
  carries the `# midea-ieco-managed` marker is left untouched as before, so an
  existing setup adds the line by hand from the block the installer prints.

  Adding a line that calls `midea_refresh_tokens.py` a second time required both
  crontab scanners to learn the difference, and in opposite directions:

  * the **language notice** would have shown the *weekly* line as the correction
    for a catch-up line that lacks `MIDEA_IECO_LANG` — and following that advice
    would replace the catch-up with a second weekly run;
  * the **inactive-job notice** would have counted the catch-up line as the weekly
    refresh and then stayed silent about a weekly line that had been deleted —
    exactly the silent non-execution that notice exists to report.

  Both now defer to one shared detector, `_cron_line_is_catchup`, which does exactly
  what `cron_scan_tools` already does to find tool names — tokenize, skip the cd
  operand and redirection targets, subsplit each token on whitespace and
  metacharacters (behind the same empty-parts guard) — and matches a *part* that
  equals `--only-if-due` exactly (not its basename). Each piece pulls its weight: the
  subsplit exposes the flag inside a quoted composite (`sh -c '… --only-if-due'`, the
  form the subsplit exists for); the cd-operand skip keeps a directory such as
  `cd '/opt/x --only-if-due y'` from counting; and the exact part-match (not
  basename) keeps a token like `/opt/--only-if-due` from counting. A plain whitespace
  or substring heuristic could do none of it. The inactive-job notice suppresses only
  the refresh half of a catch-up line, so a line that also runs iECO keeps counting
  for it.

  The catch-up line is deliberately **not** treated as an expected job: demanding it
  would report a missing job to every installation that predates it
  (`tests/KNOWN_GAPS.md`).

  Verified: 11 new assertions in `tests/test_install.sh` (267 → 278) and seven
  mutations of this logic — including "write the line twice" and "write it not at
  all" — confirmed to turn the suite red. One of those assertions closes a gap that
  had nothing to do with this change: nothing pinned *how many* managed lines the
  installer writes, so the fourth line initially slipped in without a single
  assertion noticing.

### Fixed
- **Cron logs no longer show cause and effect in the wrong order.** Both cron
  lines merge a run's stdout and stderr into one file (`… >> ieco.log 2>&1`, and
  the same for `refresh.log`). Redirected to a file, Python's `sys.stdout` is
  block-buffered while `sys.stderr` is line-buffered (the latter since CPython 3.9,
  guaranteed here by the enforced Python 3.11 floor). The tools print their own
  per-device status with `print()` (stdout); the `msmart-ng` diagnostics travel
  over `logging` (stderr, via `RedactingStreamHandler`). A typical run stays under
  the buffer size, so the **entire** stdout block was written only at process exit
  — after every library warning. In `ieco.log` a unit's `Failed to query
  capabilities …` therefore stood *above* the `print()` block it belongs to, and
  across an `all` run over several units the "which warning belongs to which
  device" correlation was pulled apart, because the per-device blocks arrived
  collected at the end. That devalued exactly the artifact the README points at
  for troubleshooting.

  Fix: a new `install_line_buffered_stdout()` in `midea_i18n.py`, called first in
  both tools' `main()` (next to the existing redaction guards), reconfigures
  `sys.stdout` to line buffering. Each `print()` line is then flushed on its
  newline and interleaves with the line-buffered stderr in true chronological
  order — verified by a subprocess test that merges both streams into one file and
  asserts the program order survives. Chosen over `-u`/`PYTHONUNBUFFERED` in the
  cron line because an in-code fix also reaches **existing** installs through the
  normal code update (the crontab is not rewritten on update) and works in every
  redirect/pipe context, not just under cron. Only `stdout` is touched; `stderr`
  is already line-buffered. As a side benefit, the `ts`-timestamp logging recipe
  suggested in the README now produces truthful timestamps instead of stamping
  the whole stdout block at process-exit time.
- **A single hand-typed `id`/`port` no longer aborts the whole
  `midea_refresh_tokens.py --all` run and discards the other devices' freshly
  refreshed tokens.** `update_device` read the device id and port with a bare
  `int()` (`int(device_id_str)` and `int(dev_conf.get("port", 6444))`), outside any
  guard. A non-numeric value — a 15-digit id mistyped `o`-for-`0`, or a `null`/typo'd
  port — therefore raised an **uncaught** `ValueError`/`TypeError`. Because
  `save_config` runs only *after* the whole device loop, that exception at device *N*
  threw away everything already refreshed in memory for devices *1…N-1* **and** never
  reached the devices after *N*; the weekly cron logged a raw traceback and repeated
  it every week until the typo was fixed by hand. (`"port": null` is especially
  sneaky: `dict.get("port", 6444)` returns the default only when the key is *absent*,
  not when it is present and `null`, so `int(None)` fired; and because Python's `json`
  parses the bare literals `Infinity`/`-Infinity`, a hand-edited `"port": Infinity`
  reaches `int(float('inf'))`, which raises `OverflowError`, not `ValueError`.) Both
  conversions are now guarded — the port against `TypeError`, `ValueError` and
  `OverflowError` — and a mistyped entry is reported per device and counts as a failed
  device
  (exit 2) — the run keeps going, refreshes and **saves** every other device, and
  leaves the offending entry untouched. This mirrors the sister module
  `midea_ieco_ensure.py`, whose `_device_config_problem` validates the same fields
  before its own device loop — and which is hardened here too: its two `int()` checks
  caught only `TypeError`/`ValueError`, so a hand-edited `"id"`/`"port": Infinity`
  (which Python's `json` reads as `float('inf')`) raised the same uncaught
  `OverflowError` and aborted the *ensure* run. Both tools now reject a non-numeric
  id/port identically. (The refresh tool's variant deliberately still allows an
  *empty* id, so discovery can fill it.) The candidate success line no longer claims
  **"and saved"**
  at a point where nothing has been written yet — the file is written once at the end,
  and the closing "updated" line is the real confirmation. New tests cover a
  non-numeric id, a `null` id, a `null` port, a typo'd port and an `Infinity` port each
  returning a clean failure, plus an `--all` fleet where a malformed middle entry still
  persists the good devices before *and* after it and exits 2.
- **`install.sh --reconfigure` no longer discards working `token`/`key` values —
  and a second run can no longer destroy the only remaining backup.** Section 8
  rewrote `devices.json` with empty `token`/`key` for every device *before* the
  cloud refresh (section 9) ran; if that refresh then failed (a temporarily
  unreachable or single-connection-blocked device, no network, or a cloud
  `getToken` endpoint that Midea has disabled), the only local copy of the
  credentials was gone. A second `--reconfigure` compounded it: the unconditional
  `cp -p devices.json devices.json.bak` overwrote the still-good backup with the
  now-empty file — defeating the very guarantee `midea_refresh_tokens.update_device`
  exists to provide (overwrite stored values *only* after a verified refresh).
  Two changes close it: onboarding now **carries over an existing `token`/`key`
  per device id** instead of blanking it (a stale carried value is harmless — both
  `connect_and_refresh` and `update_device` verify before use; a non-default
  `port` is carried over the same way — only a valid 1..65535 value — so the
  installer no longer resets a deliberately configured port), and the
  pre-reconfigure backup **never replaces a token-bearing `devices.json.bak` with a
  credential-less `devices.json`** (a visible notice is printed when it is skipped).
  New regression tests cover the id carry-over, the no-false-carry case, the backup
  guard, and the full two-run loss sequence.
- **A fresh-eyes review of the still-unreleased catch-up hardened it before
  release.** Four things it turned up, all fixed here:
  - the **test suite wrote a real `refresh_state.json` into the module directory**.
    Since an `--all` run stamps that file, every test calling `main()` with `--all`
    went through a mixin that pinned `CONFIG_PATH` but not `STATE_PATH`. On a
    git-based install the module directory is the repo, so `bash tests/run_all.sh`
    there recorded a full refresh for "now" and would have suppressed the next due
    catch-up — the very silent non-execution the feature prevents. The mixin now
    pins both, and a module-level guard fails the run if any test touches the real
    file;
  - **an oversized integer stamp crashed the decision.** `math.isfinite` converts
    to float first, and `json.load` reads arbitrarily long integer literals, so a
    400-digit stamp raised `OverflowError` on the `--only-if-due` path — at every
    start. Such a value is now treated as unusable (i.e. "due"), the safe direction;
  - **both crontab scanners could misjudge the catch-up line** (see the unified
    detector under *Added*), one of them in the direction that advises adding a
    second running line;
  - **a far-future attempt stamp blocked the catch-up indefinitely.** On a machine
    without an RTC, whose clock reads a past date at boot, a stamp written earlier
    with the correct time sits in the future and silenced the catch-up on every
    boot. The block is now bounded to a symmetric window, so an implausibly
    far-future stamp lets the run proceed instead.
- **A lost capability roundtrip was reported as "this unit cannot do iECO".**
  `get_capabilities()` does not raise when the unit stays quiet — it logs
  `Failed to query capabilities` and returns (`msmart/device/AC/device.py:602-611`).
  Because `Capability.DEFAULT` carries no `IECO`, a fresh `AC` object then reports
  `supports_ieco == False`, which is indistinguishable from a genuine "no". The
  `refresh()` that follows reconnects and re-authenticates transparently
  (`msmart/lan.py:589-599`) and resets `online` to `True`, so nothing was left to
  show the exchange had failed: msmart-ng heals the connection exactly one step
  *after* the verdict was formed. A running, healthy unit in COOL was therefore
  told it cannot do iECO, with exit 2 in the 20-minute cron.

  `ensure_ieco` now captures `device.online` **between** `get_capabilities()` and
  `refresh()` and only blames the unit when that query was answered; otherwise it
  reports that the query produced no usable answer and calls the iECO support
  unknown. The wording is deliberately not "the unit did not answer": the query can
  span two blocks, and if only the second is lost the unit *did* answer — the result
  is unusable either way, and the message may only claim what holds in both cases.
  The status line is suppressed in exactly that branch — it used to print
  `ieco=False` one line above the wrong error, a value the unit had never sent — but
  is kept everywhere else, including for a unit that genuinely reports no iECO:
  there `power`, `mode` and `eco` are measured values worth having. Exit codes are
  unchanged: a run that could not do its job still ends with exit 2.

  The obvious fix — checking `online` right after `get_capabilities()` — was
  measured and rejected: it turns a healthy run into a failure whenever only the
  optional *additional* capabilities request is lost, although the needed answer had
  already arrived in the first block.

- **The same cause made the verification claim iECO had not been applied.** The
  verification reconnect queried capabilities a second time; if *that* exchange was
  lost, `refresh()` never polled the IECO property and the run reported
  `iECO is still disabled` — about a setting the unit had just accepted. The
  `online` guard could not see it, because the subsequent `refresh()` had answered.

  The verification no longer asks a second time. It carries the capability the unit
  already reported in this run into the fresh object via msmart-ng's own
  `override_capabilities({"additional_capabilities": ["IECO"]}, merge=True)`, which
  triggers `_update_supported_properties()` and arms the same property poll. The
  failure mode disappears structurally instead of being detected, and a healthy run
  drops from 15 to 11 roundtrips — measured end-to-end against the real msmart-ng
  2026.7.0 — which matters on a unit that tolerates exactly one local connection.
  `prime_ieco_property()` verifies its own *effect* rather than its call, and a
  dedicated guard reports an unclear verification instead of falling back into the
  old false claim. The carry-over is legitimate only downstream of the guard that
  established support in the same run; the initial read still asks the unit.

  What remains uncovered is recorded in `tests/KNOWN_GAPS.md`: a verification that
  answers the state query but loses only the property read.

  With the verification switched over, `connect_and_refresh`'s `with_capabilities`
  parameter lost its last caller and was removed rather than left behind — an
  unreachable branch that a test keeps green is the exact pattern this project has
  already paid for twice. The msmart fact it asserted (capabilities must precede
  `refresh()`) is still pinned, by `test_already_on_and_ieco_short_circuits`, at the
  one place that behaviour now lives: the initial read in `ensure_ieco`. Verified by
  mutating the order there and watching that test turn red.

### Tests
- The device fakes modelled a capability failure as an **exception**, which
  msmart-ng never raises on packet loss — richer than reality, the same inversion as
  the invented `close()`. `FakeDevice` gained `caps_lost` (no throw, `online` false,
  capability unset) and, just as important, now reproduces the *healing* half:
  `refresh()` recomputes `online` from its own batch, exactly as
  `_send_commands_get_responses` does. Without that second half the fake was
  **poorer** than reality in the one dimension the fix argues about, and
  `caps_answered` could be moved one line down — reintroducing the false statement
  against the real library — with all tests still green. Measured, then closed.
- `_RecordingAC` started life with `supports_ieco = True`, where a fresh
  `AirConditioner` reports `False`. Harmless until this change made that attribute
  the evidence `prime_ieco_property` reads; a mutation that skipped the carry-over
  for every real device passed unnoticed. Now `False`, as reality has it.
- Sixteen mutations are measured before and after, split into *value* and
  **position** mutations — the earlier round only varied values, which is why the
  placement of `caps_answered`, the per-attempt carry-over, an escalation at the call
  site and a locally copied `PINNED_MSMART_VERSION` all survived it. Each now has a
  test that fails on it. `tests/test_conn.py` additionally pins four capability
  contracts against the **real** library in the `install-smoke` job, including the
  premise of the whole bug: that `Capability.DEFAULT` does not contain `IECO`.
- Two residuals of the same class that this change does *not* close — a capability
  answer that arrives but cannot be decoded, and the mirror of the batch gap that
  fabricates the status line of a *successful* verification — are written up in
  `tests/KNOWN_GAPS.md` rather than left to be rediscovered.

### Documentation
- **Five statements in the documentation claimed more than what was measured.**
  Each was checked against the code or a run before being rewritten, and each
  correction is recorded in `tests/KNOWN_GAPS.md` so the wrong version does not get
  re-derived from the history:
  - the equivalent-mutants heading in `KNOWN_GAPS.md` said there was "nothing to
    observe". True from the project's floor (Python 3.11) upwards; on 3.9.6 the
    first entry's mutation turns two *existing* tests red, so the heading now names
    the interpreter range it holds for;
  - both READMEs presented the three crontab notices as reachable from a plain
    re-run. Only two are — the "read it twice, got different results" notice sits
    behind the cron question, and the re-run branch prints its two notices and
    exits before reaching it;
  - `tests/README.md` stated "nothing leaves the sandbox" as a standing property,
    where the suite treats it as a checkpoint to re-run (gap 7 says so explicitly);
  - `tests/README.md` attributed 55 `pip` calls to the two end-to-end sandboxes.
    Measured with instrumented stubs: 48 of them are (44 onboarding, 4 `--update`),
    and 55 is the whole-run figure including the three snippet tests;
  - the language notice's blindness to the wrapper commands was written up as a
    bin-wrapper matter. It is structural — the check matches the two script names
    literally, so `midea_ieco_ensure.sh` and the refresh wrapper are equally
    invisible, on both tool sides.
- **Three gaps gained what a reader needed in order to act on them.** The
  inactive-job notice has three further blind spots that fire on the *default*
  install directory rather than on exotica — a `cd` inside a quoted sub-command,
  `cd` with an option, and a marked environment-assignment line — all of which
  silence a half of the notice; the duplicate-job gap now records that no notice
  catches an unmarked pre-existing line at either call site, and that the same root
  cause produces 262 of 2200 fuzz cases where the notice advises adding a line for a
  job that is already running; and the length guard's entry notes that `${#line}`
  counts characters under UTF-8 but bytes under `LC_ALL=C` (measured on the same
  65736-byte line, tokenized under one and skipped under the other), that its
  formula describes the longest of the four managed lines, and that assertions
  cover it only up to 8146 characters.

### Fixed
- **The tools never actually closed a device connection, and could therefore
  cause the silence they reported.** Reported by @wihelde in issue #2 while
  chasing a different problem, then measured here. Both tools released their
  connection through a helper that probed the device object for `close`,
  `disconnect` and `stop`. `msmart-ng`'s `AirConditioner` has none of the three —
  all three `getattr` calls return `None` — so the helper fell straight through
  and did nothing. It looked defensive and was a complete no-op.

  What that cost, measured against a counting loopback server: after the
  supposed close the connection was still open, and a second one to the same
  peer then stood **concurrently** — the shape of every close-then-reconnect in
  these tools. Midea units hold exactly one local session,
  so the second one runs into nothing and the unit stays quiet for a while.
  `midea_refresh_tokens.py` tests its candidates one after another against the
  same unit, and `midea_ieco_ensure.py` deliberately closes and reconnects to
  verify after `apply()` — both are precisely the sequences that suffer. A run
  could produce the very "device does not answer" it then reported.

  The teardown now goes through `device._lan._disconnect()`, isolated in the new
  `midea_conn.py`. Four points worth naming:
  - it is **synchronous**, because `msmart-ng`'s is (`await` on it raises
    `TypeError`). That is an advantage rather than a compromise: without an
    `await` there is no cancellation point, so the cleanup can neither be
    interrupted nor hang — not even inside a `finally` while the surrounding
    coroutine is being cancelled;
  - it **returns whether it ran**. Without that, "did it really close?" is
    unanswerable from outside — which is why the predecessor's uselessness went
    unnoticed for months;
  - a broken chain is **logged, never swallowed**. The old helper's silent
    fallthrough is what hid the defect;
  - closing now happens **before** the pause, not after it. `SETTLE_DELAY` and
    `RETRY_DELAY` thereby become genuine quiet windows between two sessions
    instead of a wait held on an open one.

- **A device that went quiet after the handshake was reported as verified, and
  its token was saved.** This is the one with real consequences, and it defeats
  the single promise `verify_credentials` exists to keep.

  `msmart-ng` does not report network errors out of `refresh()`.
  `Device._send_command` catches `ProtocolError` and `TimeoutError`, logs them,
  and returns an empty response list; `refresh()` then returns entirely
  normally. So a unit that completed the V3 handshake and then stopped
  answering raised nothing at all — `verify_credentials` saw two successful
  awaits and returned `(True, "", "")`. The candidate counted as verified and
  its token/key were written into `devices.json`. Measured against the real
  library, this needs no timeout and no exotic timing: roughly six seconds, the
  length of `msmart-ng`'s own read retries.

  There is exactly one observable counter-signal, and it is now checked:
  `msmart-ng` sets `_online = len(responses) > 0` in
  `_send_commands_get_responses`, the single funnel every command passes
  through. If nothing came back, `device.online` is `False`. A candidate whose
  state read-back produced nothing is now rejected as `silent` — "handshake
  succeeded, but the device sent no state back" — instead of being saved.

  **This makes `devices.json` be written less often, not more.** A run that
  previously ended with a stored token can now end in a failure. That is the
  point: the stored token was never verified, only assumed.

  The same root cause had a milder second face in `midea_ieco_ensure.py`. Its
  verification round-trip after `apply()` read `device.ieco` without checking
  whether the unit had answered at all, so a silent read-back was reported as
  "iECO is still disabled according to the device" — a statement about a device
  that had said nothing. It now says that no state came back.

- **The verification cap promised 15 s and could take 30.** Each candidate ran
  under two independent `wait_for` budgets, one around `authenticate()` and one
  around `refresh()`, so the real worst case was their sum — a number that
  appeared nowhere while the diagnostic text said "15s". Both are now spanned by
  a single `asyncio.timeout` (the recommended form since Python 3.11, which this
  project requires anyway), and `VERIFY_TIMEOUT` states that former total
  outright. The per-step cap is larger than it was and the total is unchanged,
  so the only runs that could newly fail are those where each step stayed under
  15 s while the sum reached 30 — unreachable with the pinned library, whose own
  worst case is about 12 s plus 6 s.

- **Error messages could put a device token into the log file.** The cron lines
  redirect with `2>&1`, and the logs are created with the user's normal umask —
  usually world-readable, while `devices.json` is `chmod 600`. Two quoted texts
  can carry credentials: the raw `--debug` output of the discover call, which by
  design contains the cloud's `tokenlist`, and the unaltered message of a library
  error raised right after `authenticate(token, key)`. Every argument that reaches
  a message catalogue now passes through `redact_hex`, which replaces any run of
  32 or more hex characters with `[hex:<length>]`.

  Three decisions worth naming. The filter sits in `make_translator`, not at the
  call sites that print foreign text: a list of places to remember is the kind
  of guard the next new message forgets — and this change began by nearly
  missing an entire tool, since `midea_ieco_ensure.py` prints library errors
  every 20 minutes while the token refresh runs weekly. It is deliberately **not**
  tied to the `tokenlist` regex, because the more expensive of the two paths is
  the one where that regex found nothing (a format drift) and therefore wrote the
  raw text out; a redaction sharing the same assumption would fail in exactly that
  case — measured against a renamed field. And the **length** stays visible:
  `[hex:128]` in a "no tokenlist entry found" message says precisely the useful
  thing, namely that the cloud did return something token-shaped and only the
  extraction missed it.

  No catalogue call site changed — the arity of every `t()` call is identical to
  the previous commit (102 calls across 98 message keys, compared per key), so
  the 60-pair argument-order survey stays valid. What the tools did gain is the
  wiring described below.

  The catalogue turned out to be one of three ways into that file that carry a
  guard. An independent review of the first version of this change found the
  other two, and both are now closed as well.

  **The `logging` channel.** Whatever a library writes through Python's
  `logging` goes to stderr without touching the catalogue, and both tools left
  that path open — one via `basicConfig`, the other by configuring nothing at
  all, which hands the records to `logging.lastResort`. Both now install a
  `RedactingStreamHandler` instead. This is not theoretical: every
  `warning`/`error`/`critical`/`exception` call in the pinned `msmart-ng`
  2026.7.0 was extracted with an AST walk (a `grep` is not enough — the
  interesting ones are multi-line and show only `_LOGGER.warning(` on their
  first line). There are 65, all of them `warning` (35) or `error` (30) — so all
  of them are active at `WARNING`, the level these tools configure. None names a
  token or key, but four dump a raw receive buffer from the device connection.
  At `INFO` the same module logs the device's local key outright.

  The handler overrides `format()`, and the two obvious alternatives were
  measured and rejected: a `logging.Filter` on the **root logger** never sees a
  child logger's records — they reach the root's handlers without passing its
  filters, and a child logger is exactly where a library writes. A filter
  calling `record.getMessage()` raises on a malformed library log call, and
  `Handler.filter()` sits outside `emit()`'s `try`, so that version turns a
  library bug into a cron run that exits 1. `format()` runs inside `emit()`,
  leaves the shared record's `msg` and `args` alone — what other handlers read —
  and renders `exc_info`, which is what also covers asyncio's "Task exception
  was never retrieved". `handleError` is overridden too, because the stock one
  prints `record.args` raw.

  **The traceback channel.** A second exception raised inside an `except` block
  makes Python print the whole chain, including the original library text the
  catalogue had just redacted. Both tools now install a redacting `excepthook`.
  If the redaction itself fails, the previous hook runs: a traceback must never
  disappear entirely.

  Both guards are armed in `main()`, not at import time. That distinction was
  measured, not assumed: installing the log handler at import cost an embedding
  application its own configuration — root level clamped from `DEBUG` to
  `WARNING` and every line doubled — where the `logging.basicConfig()` it
  replaced had deliberately done nothing at all once handlers existed.

  **What is still open**, stated plainly. The redaction matches *contiguous*
  hex: the same value spaced, grouped or rendered as `repr(bytes)` is not
  recognised, and widening the pattern would hide device ids and checksums too.
  It is a format filter, not a secret filter — a credential that was never
  hexadecimal (a JWT, a dashed session id) passes untouched. `warnings.warn()`
  writes to stderr without passing any guard, and code that calls
  `logging.basicConfig(force=True)` or clears the root handlers removes the log
  guard again. `KNOWN_GAPS` gap 11 has the full channel table, which guard
  covers what, and what is left. Verified by running both tools as separate
  processes writing into a file with `2>&1` — the way cron does it — which is
  also how the two wiring tests assert it.
- **A cron job wrapped in parentheses counted as missing.** The inactive-job
  notice tokenizes the command field the way `/bin/sh` reads it, but its list of
  separators left out `(` and `)`. Anyone who groups the call to redirect it as a
  whole — `*/20 * * * * (midea-ieco all) >> ieco.log 2>&1` — produced the token
  `(midea-ieco`, which no comparison recognises, so a **running** job was reported
  as not active and the installer offered its own line to add. Following that
  advice leaves two jobs against units that tolerate a single local connection.
  The form is exactly the one both READMEs recommend: the bin wrapper without a
  path, which is what makes the missing basename fatal here — with a path in front
  the basename step cuts the parenthesis off and the case stays invisible.

  Measured against an execution oracle (each line run through `/bin/sh` in a stub
  sandbox, observing which tool really ran) over 2200 generated lines: 60 such
  cases before, none after, and **no new case in the other direction**. Eight
  false "present" verdicts disappear as well, because `(cd /opt/local/midea-ieco`
  used to defeat the `cd`-operand skip and let the install directory count as a
  call. `{` and `}` are deliberately left out — they are reserved words rather
  than metacharacters, so whitespace already separates them.

  One consequence is named rather than buried, and it is the cheap direction: an
  install directory whose name wraps the tool name — literally
  `/opt/(midea-ieco)/inst` — now lets the logrotate line count as the iECO job, so
  the notice stays silent about a job that is not there. `tests/KNOWN_GAPS.md`
  gap 8 records it.

  A detail worth keeping for whoever edits that code next: in the sub-split's
  character class the backslashes in `[;&|<>\(\)]` are load-bearing. Directly
  after `<` or `>`, bash reads `<(` and `>(` as the start of a process
  substitution, the class falls apart silently, and the replacement then does
  nothing at all — no syntax error points at it. Measured on bash 3.2, where
  `[;&|<>()]` returns `(midea-ieco all)` unchanged.

### Added
- **A test that can see whether a connection actually closed — and a guard so a
  test double can never again be more capable than reality.** The dead teardown
  above survived for months *because the suite was green*: every device fake in
  the suite defined a `close()` that `msmart-ng`'s `AirConditioner` does not
  have, so the assertion "the device is closed" exercised a branch that could
  never run in production. A fake may be poorer than the real object; it must
  never be richer.

  Three layers now, because none of them alone would have caught it:
  - `tests/test_conn.py` opens a real connection to a loopback server that counts
    accepts and disconnects, so closing is *observed* rather than believed. It
    carries the previous implementation verbatim as a mutation clamp — that clamp
    must fail the same assertion, otherwise the test above has stopped measuring
    what it claims;
  - a fidelity check over every device fake in the suite, which fails if one
    offers a teardown name the real object lacks. All five failed it when first
    written;
  - a contract test pinning the private path against the real library: it exists,
    is synchronous, takes no arguments. It runs as a **subprocess** because this
    suite registers an `msmart` stub — an in-process check would have quietly
    verified the stub. In CI it runs in the `install-smoke` job, the only place
    with the real dependency installed; without `msmart-ng` it skips visibly.
    Its inverse guard fails once upstream ships a public `close()`, asking for
    this workaround to be removed rather than left in place forever.

    A guard that can silently disable itself is worse than none, and this one
    nearly could: `unittest` exits 0 on an all-skipped run, and every failure of
    the probe — including a changed constructor signature, precisely the
    upstream break it exists to catch — was turned into a skip. Two rules now
    close that. A **crashed probe is a failure**, never a skip. And the CI step
    sets `MIDEA_IECO_REQUIRE_MSMART`, under which **any** skip becomes a
    failure, because a missing dependency is itself a finding there.

  `close_connection` also refuses a teardown that has become a coroutine. Left
  unchecked, calling it would return an un-awaited coroutine object: nothing
  closed, success reported, and the only trace a `RuntimeWarning` outside the
  project's logging and redaction — the same silent uselessness this module was
  written to end.

  Also: the `tools/` probes are now covered by a test that starts them the way a
  user does — as a subprocess from a foreign working directory. `py_compile`,
  which is what the suite ran over them before, checks syntax only and would not
  notice an import that fails to resolve.

- **The installer now says so when a managed cron job is not active.** A crontab
  carrying our marker counts as "already set up", so nothing more is written — if
  the iECO line had since been deleted or commented out, the product silently did
  not run at all and nobody mentioned it. The notice lists exactly the missing
  lines and writes nothing: the messages next to it promise the crontab is left
  untouched, and printing keeps that literally true. A job run through the
  documented `midea_ieco_ensure.sh` wrapper or through the `midea-ieco` /
  `midea-ieco-refresh-tokens` bin wrappers counts as present.

  Two corrections to how this was first described. The notice was said to make
  "a duplicate cron job structurally impossible" — true of the notice, which only
  prints, and **not** true of the installer: with an *unmarked* pre-existing
  `midea_ieco_ensure.py` cron line, a fresh onboarding run finds no marker and
  appends its own marked line. Measured end to end: one job in, two out, with
  "[OK] cron jobs installed". That is now recorded as its own gap in
  `tests/KNOWN_GAPS.md`. And the tool detection first matched only the two script
  names, so a crontab driving the **bin wrappers** — the commands this installer
  creates and both READMEs document — was reported as having no jobs at all, and
  the notice offered the installer's lines to add. Following that advice produced
  the very duplicate the entry claimed to prevent. Fixed: detection now matches
  the basename of each token in the command field, which is what keeps it working
  for the default install directory `/opt/local/midea-ieco`, whose path contains
  the wrapper name.
- `tests/test_wrapper.sh`: functional tests for `midea_ieco_ensure.sh`. The wrapper
  is the second production path for `--only-if-on` (Siri shortcuts, SSH) and had no
  functional test at all — `bash -n` and `shellcheck` both stay green when `"$@"`
  is replaced by `"${1:-}"`, which is exactly the bug this project started with and
  would switch on every deliberately-off unit every 20 minutes.
- End-to-end installer tests that run the real `install.sh` in a stubbed sandbox and
  assert what actually reaches `crontab -`: both jobs present, the `*/20` schedule,
  `--only-if-on` behind the `all` target, `--all` on the refresh line, the log
  redirection, `truncate` rather than `rm`, the marker on every line the installer
  adds, and no second write on a re-run. Previously only the *contents of the shell
  variables* were checked — deleting the `echo` that writes the iECO job left the
  suite green while the product silently did nothing.

- **Two failure causes that used to fall through unclassified.** A wrong *key*
  (`msmart-ng` reports a SHA256 digest mismatch — the device answered the
  handshake, but the reply cannot be decrypted) is now named as exactly that, with
  a hint pointing at a token refresh; it is the clearest evidence the stored
  credentials are stale, and it previously produced no hint at all. A real TCP
  reset (`Connection reset by peer`, errno 54 on macOS / 104 on Linux) now
  classifies as a dropped connection instead of falling through.
- **`install.sh` points out cron jobs that predate the language pass-through.**
  It never rewrites them: the crontab belongs to the user, and `--update`
  explicitly promises not to touch it. It reports that the existing lines log in
  English (cron runs without a locale) and prints the corrected lines to copy.
  The previous entry's claim that this case was fixed was too broad — only *new*
  cron installations were covered.
- `tests/KNOWN_GAPS.md` — the behaviours that still survive deliberate mutation,
  each with its user cost and the cheapest way to close it, plus how to re-run the
  exercise. A green suite is not evidence on its own; this records where it is
  thin instead of leaving it to be re-derived. (Its own accuracy has since been
  corrected in place: the header arithmetic did not add up, `refused` was listed as
  a covered classification although the same commit removed that word from the code
  for being wrong, and the table of unclassified `msmart-ng` wordings was
  incomplete. The corrections are recorded in the file itself. The commit message of
  `f97a025` also states "228 Python (was 180)" — the correct previous number is
  166, verified by running the suite at `f97a025^`.)

- **iECO is tied to the operating mode — the tool now says so.** `midea_ieco_ensure.py`
  checks the operating mode before writing anything and reports a mode that cannot
  carry iECO by name, instead of attempting a write that silently fails and then
  reporting the generic "iECO ist laut Geraet weiterhin deaktiviert" (reported as
  issue #3). With `--only-if-on` (the recommended cron job) this is deliberately
  **not** an error — a deliberately chosen mode is not a fault, and a unit left in
  Auto no longer produces 72 failed runs a day. On an explicit call it exits
  non-zero and switches nothing at all, not even power. If iECO is already active,
  the existing short-circuit still wins, so an overly narrow mode list can never
  flag a working state as a problem; an undeterminable mode fails open.
- Measured on a real unit (PortaSplit `2060008E`, 2026-07-27, all five modes via
  the remote): **Cool and Heat carry iECO; Auto, Dry and Fan only discard it.**
  This matches `msmart-ng`'s own capability decoding (`1,3,8 - Cool, 3,4,8 - Heat`),
  which is collapsed into a single `supports_ieco` bool and therefore cannot be
  read back at runtime. Both READMEs gained a "Which modes support iECO" section;
  the practical consequence — **iECO is unavailable in Auto, through this tool and
  through the Midea app alike** — was previously undocumented anywhere.
- `tools/probe_ieco_current_mode.py` — measures whether iECO holds in the mode the
  unit is currently in, without switching modes over the network (set the mode on
  the remote). Two connections per reading, one when iECO is already on.
- `tools/probe_ieco_modes.py` — automated sweep across all modes. Prefer the
  gentler script above: Midea units accept a single local connection and can lock
  their LAN interface up temporarily under rapid session churn. The sweep now
  aborts on the first connection loss, lets the unit settle, and retries the state
  restore patiently (45 s cooldown, then 6 attempts 20 s apart).

- Both READMEs now list the press coverage of this project ("In the media" /
  "In den Medien"), with a transparency note on what was supplied to which
  outlet and that no money changed hands in either direction.

### Changed
- `SETTLE_DELAY` and `DEVICE_DELAY` are named constants in `midea_ieco_ensure.py`
  instead of inline literals, so their lower bounds can be asserted the way the
  sister module already does. Timing behaviour is unchanged.
- `summarize_failure_hint` checks the answered-codes subset after all single-cause
  branches. Behaviour is identical today; the previous order quietly assumed every
  member of `_ANSWERED_CODES` already had its own branch above it.

- **Both Python tools are now bilingual, and default to English.** They printed
  German only — so the English-speaking reporter of issue #2 filed a careful bug
  report and got answers in a language he may not read. `install.sh` has had
  English/German support for a while; the Python side never did. Language
  resolution mirrors the installer exactly (`MIDEA_IECO_LANG` > `LC_ALL` >
  `LC_MESSAGES` > `LANG` > English), so a German desktop keeps German without any
  configuration. This covers every user-facing string of `midea_ieco_ensure.py`
  and `midea_refresh_tokens.py`, including the `--help` output and the offline
  overview. **Note for existing German users:** cron jobs usually run without a
  locale, so scheduled runs will now log in English — add `MIDEA_IECO_LANG=de` to
  your crontab to keep German.
- New `midea_i18n.py` holds the shared language resolution used by both tools.
  Duplicating it would have let the two copies drift apart — a locale edge case
  fixed in one file and missed in the other. The message catalogues stay with
  their respective modules; only the mechanism is shared. Operating-mode names
  (`COOL`, `HEAT`) deliberately stay untranslated: that is what they are called
  on the unit, on the remote and in `msmart-ng`. It adopts the installer's
  *precedence*; the claim that it mirrors `install.sh` "1:1" was wrong and has
  been corrected — in four whitespace/prefix edge cases it is deliberately more
  generous (always in favour of German, never the reverse), and the module
  documents each one. All ordinary cases agree.

- Operating modes are printed as name **and** number (`mode=FAN_ONLY (5)` instead of
  `mode=5`). `msmart-ng`'s `OperationalMode` is an `IntEnum`, which renders as a bare
  number from Python 3.11 on — unreadable in logs and bug reports. This also makes
  the READMEs' log examples match reality again.

- Documentation: the measured extra consumption without iECO is now stated as
  the honest range **2 to 3.8 kWh per day and unit** instead of a flat "roughly
  4 kWh". Re-deriving the per-day figures from the raw Shelly logs showed the
  4 kWh to be the optimistic end: 3.8 kWh/day holds for the unit with a clean
  like-for-like comparison, while the second unit lands at 2.0–3.2 kWh/day
  depending on whether partial-runtime days are included. The measurement setup
  (two units, individual power meters, ten days, unchanged 23 °C setpoint) and
  its limitations are now named explicitly, as is the evidence *for*
  comparability (near-identical overnight base load on iECO and non-iECO days).
- Documentation: the claim that iECO ends on its own after roughly eight hours
  is now marked **unconfirmed** in both READMEs, after several PortaSplit owners
  reported never observing such a cut-off. Midea's own "up to eight hours with
  only 1.2 kWh" is a *consumption* figure, not a timeout — the likely source of
  the confusion — and any interaction (app, IR remote, changing the target
  temperature) appears to reset such a timer anyway, so it would rarely be seen
  in practice. It has not been isolated in a controlled measurement here.
  The reproducible behaviour this project actually addresses is now stated more
  prominently in its place: iECO is lost whenever a unit is switched off and on
  **outside the app** — at the unit or with the remote — silently, with no
  indication on the display. Switching through the app preserves it, which is
  why not every owner runs into this. No change to how the tool behaves.

### Fixed
- **The inactive-job notice split cron lines blind to quoting, and was wrong in
  both directions.** A quoted install path containing a space fell apart into
  fragments whose basename happened to be `midea-ieco`, so a job that had been
  commented out still counted as present and the notice stayed silent. In the other
  and more expensive direction, `cd /opt/local/bin;./midea-ieco all` — a semicolon
  with no space around it — was swallowed whole as a supposed `cd` operand, so a
  *running* job counted as missing and the installer offered its own line to add.
  Following that advice leaves two jobs hitting the same units every 20 minutes,
  which is what this project's firmware notes warn against.

  The command field is now tokenized the way `/bin/sh` reads it: single and double
  quotes group, a backslash escapes the next character, `;`, `&`, `|`, `<` and `>`
  separate, and an unquoted `#` at the start of a word ends the line. The operand of
  `cd` and the target of an output redirection are skipped; a quoted sub-command
  (`sh -c '…'`) is split once more so the call inside it is seen. Also fixed by the
  same change: a quoted `"cd"`, a note placed *before* the marker (`… # earlier:
  midea-ieco all # midea-ieco-managed`), a path escaped with backslashes, and a
  redirection glued to the command word.

  Two consequences worth naming rather than burying. A tokenizer has to know about
  backslashes, or it breaks on lines this installer writes itself:
  `shell_quote_for_cron` expands `'` to `'\''`, leaving an odd number of quotes, and
  a first draft without that rule read the rest of such a line as one quoted token
  and advised adding both jobs. (The old whitespace split had no trouble here — it
  never looked at quotes at all.) A self-consistency assertion now runs the
  installer's own three lines for 13 hostile install directories through the
  detector. And the notice now speaks in two cases where it used to stay
  silent by accident — text behind a comment character, and a redirection target —
  which is the expensive direction and is recorded in `tests/KNOWN_GAPS.md` gap 8.
  A line beyond 65536 characters is skipped untokenized (the split is quadratic);
  that threshold sits above anything this installer can produce, derived in gap 10.
- **`error()` writes to stderr.** Several functions are called inside command
  substitutions; on stdout their error message ended up inside the captured value
  instead of on screen, and `set -euo pipefail` then aborted completely silently —
  an install path containing a newline ended the run without a single word.
- **A crontab that could not be read reliably is no longer rewritten.**
  `crontab -l` exits non-zero both when there is no crontab and when reading fails,
  and the second case was destructive: the write branch treated the user's crontab
  as empty and replaced it with our three lines, reporting success. Reproduced end
  to end — three foreign lines in, zero out. The two cases need not be told apart;
  the crontab is now read twice and nothing is written when the results disagree.

  What this does **not** cover, stated plainly because the entry above reads
  stronger than it is: only a *flapping* read is caught. A `crontab -l` that fails
  **persistently** while `crontab -` still works returns the same empty result
  twice, the two reads agree, and the user's crontab is replaced exactly as
  before — re-measured with a stub that always fails `-l`: three foreign lines in,
  zero out, "[OK] cron jobs installed", identical at this commit and at its
  parent. Closing that would mean distinguishing "no crontab" from "cannot read
  it", which `crontab -l` does not offer. Left as is deliberately; recorded so the
  guard is not mistaken for full protection.
- **The cron language notice: a whitespace value is not a value, and the line
  wins.** `MIDEA_IECO_LANG='   '` counted as set although cron stores the spaces
  verbatim and `resolve_lang` strips them and falls back to English. And an
  assignment above the jobs masked an *empty* assignment on the job line itself,
  though shell precedence is the other way round — `VAR= command` overrides the
  environment. Four further shapes of hand-edited line are still misjudged; they
  need a shell parser to get right and are written up in `tests/KNOWN_GAPS.md`
  with the measured cases rather than guessed at.
- **The mixed-failure hint claimed things that had not happened.** When
  `VERIFY_BAD_KEY` joined the set of "the device answered" codes, the hint text was
  not revisited: for `[wrong key, silent]` it asserted a rejection that never took
  place. Its second claim was wrong in a different way — the check only looked at
  whether an answer came *first*, while the text says the device *stopped*
  answering, so `[rejected, silent, rejected]` produced "the later candidates are
  NOT meaningful" about a candidate the device had just answered. Both halves are
  now required: the first answer must precede the first blockade, **and** nothing
  after the last answer may be anything but a blockade. Worked out against a truth
  table over all 2801 code sequences up to length four before any code changed;
  the implementation was then re-checked against that table.
- **`[rejected, wrong key]` gave no hint at all** — the case where the device
  answered *every* single attempt, which is the strongest evidence the tool can
  produce that the stored credentials no longer belong to the unit. It now says so
  and points at the token retrieval (`hint_all_answered`, EN/DE).
- **The installer's "your cron jobs log in English" notice missed three cases.**
  It used to grep across the whole crontab, so it went quiet as soon as *one* of
  the two managed lines had been migrated, and it warned even when a standalone
  `MIDEA_IECO_LANG=` environment line — the usual way to set a variable for all
  cron jobs — had already set the language. Fixing those two introduced a third
  fault: the replacement recognised the environment line **wherever** it stood and
  **whatever** it contained, while cron applies such an assignment only to the jobs
  *below* it (`man 5 crontab`) and an empty value makes `resolve_lang` fall back to
  English. The check now walks the crontab line by line and carries the nearest
  preceding assignment forward, so a line below the jobs, one between them, a later
  assignment that empties an earlier one, and an empty or `''`/`""` value are all
  handled the way cron actually behaves. The same value test applies to the inline
  assignment in a job line, which previously counted `MIDEA_IECO_LANG=` with no
  value as migrated. A commented-out managed job line no longer produces a notice
  either — it does not run, so it logs nothing to translate. Worked out against a
  table over position × value × migration state × commented-out before any code
  changed; each row is a fixture in `tests/test_install.sh`.
  The notice is also reachable now: a plain re-run on a configured system exits
  before the cron section, so it was previously almost impossible to see.

- **A wrong hint is worse than no hint: the "rejected first, silent afterwards"
  pattern was order-blind.** `summarize_failure_hint` reduced its input to a set,
  so `[unreachable, rejected]` produced the same advice as `[rejected,
  unreachable]` — telling the user "the device stopped answering after the
  rejection" when the rejection had in fact been the *last* event, suppressing the
  correct "your tokens do not match this unit" conclusion and sending them off to
  wait and retry instead. Before that hint existed the case produced silence; the
  set-based check turned silence into misinformation. The hint now requires the
  device's answer to genuinely come first, and says nothing when it does not.
- **`connection was refused` claimed more than the signal supports.** `msmart-ng`
  raises `Connect failed.` for *every* `OSError` out of `create_connection` — a
  refused connection, but equally a DNS failure, an unreachable network or an
  unreachable host. Asserting "something answered, but not on this port" was wrong
  for most of them (reproduced with a DNS failure and `ENETUNREACH`). The wording
  is neutral again, still distinct from the connect-timeout case. A test now pins
  the *neutrality* rather than the exact phrasing.
- `hint_all_cap` removed: it cannot be reached. Our verification cap sits above
  `msmart-ng`'s own worst case (`authenticate` ≈ 12 s; `refresh` ≈ 6 s and it does
  not propagate network errors at all), so an all-timeout run does not occur in
  practice. Inventing advice for a state that cannot happen is worse than saying
  nothing; `VERIFY_CAP` still classifies correctly should it ever fire.
- `tests/run_all.sh` clears the bytecode caches after the run as well as before.
  The previous change moved the cleanup to the front and claimed the run no longer
  writes a cache — but `py_compile` writes `.pyc` regardless of
  `PYTHONDONTWRITEBYTECODE`, so a direct `python3 -m unittest …` afterwards could
  still be served stale bytecode. Both ends are cleaned now.

- **The most common real-world failure got no hint at all.** When a unit is
  switched off, its IP has changed, or a firewall drops the packets, `msmart-ng`
  reports `Connect timeout.` — a wording the failure classification did not know,
  so it fell through to "unclassified" and `summarize_failure_hint` returned
  nothing. The matching hint ("check the IP address, whether the unit is powered,
  and whether port 6444 is reachable") existed but could never fire. It now has
  its own marker, and one distinct from a *refused* connection: refused means
  something answered, a timeout means nobody is there — a real difference when
  hunting the cause. Found by an independent review of the previous two commits.
- **The failure classification could contradict itself depending on the call
  path.** `msmart-ng` raises `Connect timeout.` as a `TimeoutError` and only
  re-wraps it as an `AuthenticationError` on some paths. Because the "is this our
  own time limit?" check ran *before* the message markers, the same cause was
  reported as "unreachable" or as "our time limit" depending on where it came
  from. Message markers now win; the type check only catches what has no message
  of its own (which is exactly our own `asyncio.wait_for`).
- **German text still reached English users** — the thing the previous commit set
  out to fix. Every `print()` was localized, but the `RuntimeError` messages
  raised inside the discover subprocess handling were hard-coded German and are
  printed to the user verbatim, as was `midea_refresh_tokens.py --help` in full.
  An English user hitting the *most likely* first-run failure read
  `Kein tokenlist-Eintrag in der Ausgabe gefunden`. All of it is catalogued now.
- Each candidate line said "rejected" regardless of cause. Only one of the
  possible causes is an actual rejection by the device; a connection that never
  came up was rejected by nobody. The line now reads "failed", with the specific
  cause immediately after it.
- Hints added for two further cases that previously produced none: every attempt
  dropped by the device, and every attempt hitting the time limit.
- `install.sh` now passes the chosen language through to the Python tools and
  into the generated crontab. `install.sh --lang de` on an English-locale machine
  produced a German installer with English token output, and — more visibly — an
  existing German user's `ieco.log` silently switched to English after an update,
  because cron runs without a locale. The installer already used this idiom for
  its own update phase.
- `midea_i18n.py` was missing from the `py_compile` step of the test suite.

- Documentation: both READMEs claimed that the `midea_ac_lan` Home Assistant
  integration "exposes iECO as one preset" and recommended it to Home Assistant
  users. **It cannot set iECO at all** — an instance of the very ECO/iECO mix-up
  this project exists to explain. `midea_ac_lan` is built on `midea-local`, which
  has no iECO property whatsoever (verified against 6.6.1 and the current 6.11.0:
  the only eco-related AC attribute is `eco_mode`), and its documented presets are
  `none, comfort, eco, boost, sleep, away` — that `eco` being the plain
  fixed-24 °C ECO mode. The integration that *does* expose iECO is
  [`midea-ac-py`](https://github.com/mill1000/midea-ac-py), built on the same
  `msmart-ng` library this project uses for control. Both READMEs now name the
  distinction, and the "Do I need Home Assistant?" FAQ points to `midea-ac-py`.
  Reported by a user who had gone hunting for a preset that was never there —
  thank you.

### Testing
- **The wrapper guard's ordering assertion compared an integer division.** It
  checked `START_BUDGET_TICKS / 10` against the time limit, so a budget of 79 ticks
  stayed green ("7 s < 8 s") while 79 ticks really cost 8.6 s — and `run_all.sh`
  then failed with its misleading message about a left-behind process. The loop now
  bounds itself by wall time via `SECONDS` differences (never a reset, so a
  `SECONDS=1000` inherited from the environment cannot skew it), the ordering
  assertion carries a lower bound as well — a budget of 0 or 1 made the test flaky
  without ever going red — and the file measures its own total runtime against the
  same limit, so a merely slow file fails with an assertion that names it. Measured:
  normal run 1–2 s, up to 3.5 s under fourfold load; the reintroduced fault 21 s,
  caught by the guard.
- **Five of the new assertions were decoration.** An independent audit ran a kill
  mutation against each and found five that could not go red for the property their
  name claimed — among them a self-consistency loop that stayed green with an empty
  input list while still reporting "13 install paths". They were sharpened (the loop
  now counts and asserts the count) or dropped. A later pass found two rules with no
  test at all — the tokenizer's double-quote state and the reset of the `cd` marker
  — and closed both. One branch is documented as having no kill of its own, because
  the sub-split covers it.
- **The test suite could write outside its sandbox — and download to do it.**
  `install.sh` resolves its install directory from `MIDEA_IECO_RESOLVED_DIR` before
  anything else and exports that variable to child processes itself. Inherited from
  the environment, it aimed every end-to-end section at that directory: measured on
  a victim, a `venv/` was created there, along with `devices.json` and
  `devices.json.bak`, and in the update path a ZIP download from GitHub was copied
  over the target. The directory it hits is exactly the `.git`-stripped copy this
  project's own mutation-testing recipe tells you to make.
  The whole `MIDEA_IECO_*` family is now unset at the head of `tests/test_install.sh`.

  Three claims from the first write-up of this fix, corrected against measurements:
  - The victim was said to receive "a complete venv of **898 files** with msmart-ng
    and midea-local really installed from PyPI". The count and that composition do
    not demonstrably belong together: a **bare** `python3 -m venv`, offline and with
    nothing installed into it, produces 871 files here (Python 3.14.6; 569 under
    Apple's 3.9.6). 27 files is nowhere near two packages plus their dependencies,
    so the number does not show that anything was fetched from PyPI. What is certain
    about the hazard — a foreign directory is entered and written to, and the update
    path downloads and copies over it — stands on its own; the count is dropped.
  - "Zero `git`/`curl`/`unzip` calls" was wrong. An instrumented green run makes
    **nine** of them (8 × `git`, 1 × `curl`, 0 × `unzip`). Every one is served by a
    sandbox stub; the true statement is that none of them escapes the sandbox.
  - "`git`, `curl` and `unzip` are stubbed as loud failures in every end-to-end
    sandbox" holds only for the onboarding runs. In the temp-leak sandbox `curl` and
    `unzip` are *silent* `exit 1` and `git` is a silent `exit 0`; in the `--update`
    sandbox `git` is a functional shim that answers `diff --quiet`, `pull --ff-only`
    and `rev-parse` with success.

  Worth knowing which is which: only the unset protects. `python3 -m venv` creates
  the venv, and `setup_venv_and_deps` then sources `venv/bin/activate`, which puts
  `venv/bin` in front of `PATH` — from there on the *venv's own* `pip` installs the
  packages and a `pip` stub on `PATH` never fires. (In the sandboxes the fake
  `activate` leaves `PATH` alone, which is why the `pip` stub does run there: 48
  calls in the same instrumented run — 55 counting the `pip`-snippet tests outside
  those sandboxes.)
- **The wrapper test left a `sleep` holding the suite's stdout**, so anything
  capturing the output — a pipe, a command substitution, a CI step log — waited ten
  seconds: 1.0 s redirected to a file against 10.9 s captured. Fixed on both sides,
  and `run_all.sh` now captures that test the way CI does and fails above a time
  limit, which guards the pair permanently.

  That guard did not work as written, in both directions, and has been repaired.
  The same change had shortened the fake Python's sleep to 5 s, so reintroducing
  the fault stalled only 6.0 s — under the 8 s limit, and the suite reported ALL
  GREEN. Meanwhile the wrapper test's own start budget was 100 × 0.1 s = 10 s,
  *above* the limit, so a merely slow start produced a red run carrying the
  misleading message about a left-behind process. The three quantities now stand
  in the only order that works — start budget 4 s < limit 8 s < the 20 s stall the
  fault produces — the limit is handed to the test from `run_all.sh`, which is
  where it is set — a fallback of the same value also lives in
  `tests/test_wrapper.sh` for a direct call, so "in one place only" was too
  strong. The test asserts the ordering itself. "Moving any one of them turns
  the suite red" was likewise too strong for the version written then: it held
  for the limit and the stall, not for every neighbouring budget value. The
  rewritten assertion (see above) is bounded on both sides and was re-measured
  against budgets of 0, 1 and 8. Measured: normal run 1.0 s; the
  reintroduced fault 21 s, caught by the guard; a fake Python needing 12 s to start
  fails with its own assertion and never reaches the limit.
- **A newly added assertion was vacuous.** "`shell_quote_for_cron`: the error text
  does not end up in the returned value" is a bare negative: replacing the abort in
  `install.sh` with `: "$(t err_cron_newline)"` — the catalogue key stays referenced,
  the branch does nothing — left all 220 assertions green. Two positive counterparts
  now stand next to it: that path must exit non-zero, and the catalogue text must
  appear on stderr while doing so.
- **Both call sites of the cron language notice are now exercised, not counted.**
  Reachability rested on a source-text `grep -c`, which stays green when a call site
  is emptied. Two end-to-end runs replace it, and they assert on the warning text
  rather than on the printed cron line — install.sh prints those lines
  unconditionally further up, so the obvious assertion is vacuously green.
- **Existing user data is now a fixture, not an empty directory.** Four places
  where the installer touches data the user already has were only exercised from
  an empty starting state, so the assertions could not see destruction. Each of
  these survived the whole suite: dropping `echo "$EXISTING_CRON"` from the
  crontab write (which deletes every foreign cron job), turning `>>` into `>` in
  `_write_path_block` (which truncates `~/.bashrc`), dropping the `devices.json`
  backup before `--reconfigure`, and disabling the config-safe re-run guard at its
  call site — the guard function was tested, its call site was not. The production
  code was correct in all four; a crontab preload and three assertions with
  existing content close them, and the marker assertion is restricted to the lines
  the installer adds.
- **The connection-retry pause is pinned by order.** Three of the four sleeps in
  `midea_ieco_ensure.py` were pinned; the one spacing out connection attempts
  could be deleted with the suite staying green — the pacing that matters most,
  since the unit tolerates a single local connection and blocks after dense
  access.
- **Every argument pair at a message call site is checked as a rendered line.**
  The call sites were derived from the AST — each `t()` call with two or more
  values, each adjacent pair swapped in turn — which gives 60 mutations; 25 of
  them survived. All 60 are caught now, verified by re-running the survey. The
  expectations are rendered from the catalogue, so a reworded message moves them
  instead of voiding them. While doing this, `_assert_order` was rewritten to
  search forward from the previous match, which looked like a strengthening and
  measurably weakened it (a swapped pair was then found again in the next retry
  line). Reverted, and both helpers now state in their docstring what they cannot
  do.
- **`midea_ieco_ensure.sh`'s `exec` is proven by process identity.** Signal
  delivery — the reason the `exec` is there — is racy to test directly; that the
  wrapper *becomes* the Python process is the same property and is a plain PID
  comparison. Removing `exec` now turns the wrapper tests red.
- **The most safety-critical string in the project was unguarded.** Four tests
  were added proving `--only-if-on` reaches `ensure_ieco` from `main()` — but the
  only place that flag is used in production is the crontab line `install.sh`
  writes, and `tests/test_install.sh` did not mention it once. Deleting it there
  left the entire suite green while every switched-off unit would be powered on
  every 20 minutes. The generated cron lines are now asserted directly, including
  that the flag follows the `all` target rather than being read as a device name.
- **Untested core promises now have tests**, each verified by a mutation that
  previously survived: `devices.json` is left byte-identical when every candidate
  fails (the module's headline guarantee, which printed "remain unchanged" while
  a one-line change made it destroy the last working credentials); `save_config`
  is actually called; exit codes 0/1/2 in both tools; and the bodies of
  `verify_credentials` and `connect_and_refresh` — until now replaced by a mock in
  *every* test, so swapping `authenticate(token, key)` would have broken every
  device connection for every user unnoticed.
- Further gaps closed: the `online` and `supports_ieco` guards (their `FakeDevice`
  parameters existed but no test ever set them to `False`), the reconnect path
  losing `device.ieco = True` (the same "assert against a pre-configured
  verification stand-in" anti-pattern that was fixed elsewhere but left in
  `RetryHardeningTests`), the *placement* of the candidate pause (a count-based
  test cannot tell "pause before" from "pause after" — the event order is now
  asserted), and the four `resolve_lang` edge cases documented in the previous
  commit but never exercised.
- The `t()` call-site check now also samples argument *order* — a swap passes an
  arity check while rendering "did not respond within 1.2.3.4s (is the device at
  60 reachable?)" — and its self-guard threshold scales with the catalogue instead
  of being a fixed number.
- The earlier "17 of 17 mutations are caught" was accurate for the six areas it
  named but reads as a completeness claim, which it was not: an independent review
  ran 165 mutations and 51 survived. The top ten by user damage are closed here;
  the rest are written down in `tests/KNOWN_GAPS.md` rather than left implicit.
- The test suite was re-examined by an independent review, which mutated the
  source in a scratch copy to check that the tests actually go red. Everything
  below was a mutation that previously survived a fully green suite:
  **`main()` dropping `--only-if-on` entirely** (the most safety-critical path in
  the project — a regression there powers on every switched-off unit, every 20
  minutes, from cron); **never powering the unit on**; **never setting iECO**;
  **removing the final verification**, which meant a build that changed nothing
  at all still printed "iECO is active (confirmed by the device)"; a
  `CANDIDATE_DELAY` of `0`; and `DEVICE_DELAY` removed. The state-change tests
  now assert against the *acting* device rather than a separately pre-configured
  verification stand-in, which is what made those mutations invisible.
- Two assertions were fixed that could no longer fail. One checked for German
  wording in a subprocess that now runs in English (so it no longer noticed a
  cloud contact happening before the `msmart` availability check); the other
  asserted a property of the test's own stand-in class. Text assertions are now
  derived from the message catalogue instead of being retyped, so a reworded
  message moves the assertion with it rather than quietly voiding it.
- A test named `..._and_distinct_where_expected` only checked "not empty", so a
  German catalogue entry could contain English text unnoticed. It now checks what
  its name says. A new check compares every `t()` call site against its format
  string, so a wrong argument count cannot ship a `TypeError` hiding in a rarely
  hit error path.
- `tests/run_all.sh` clears the bytecode caches *before* the run and disables
  bytecode writing. Python validates a `.pyc` by size and mtime second, so an
  edit that keeps the file size identical (`sys.exit(0)` → `sys.exit(1)`) could
  be served from a stale cache — this briefly masked real failures during the
  mutation review.
- **A failed token verification now says *why* it failed** (prompted by issue #2).
  `midea_refresh_tokens.py` reported every rejected candidate with the same
  "lieferte keine gueltige Verbindung", collapsing causes that have nothing to do
  with each other: a token the unit actively rejects (it answers with an `ERROR`
  frame — `msmart-ng`'s `PacketType.ERROR = 0xF`), a unit that accepts the
  connection but never answers, a closed port, and a reset connection all look
  identical. `msmart-ng` reports all four as the same `AuthenticationError` and
  distinguishes them only by message text, so each candidate is now classified
  and named, and an unrecognised message is passed through verbatim rather than
  swallowed. After a total failure the tool adds a hint derived from *all*
  candidates — including the specific "rejected first, silent afterwards" pattern,
  which means the later candidates were never meaningfully tested.
- **Token candidates are no longer verified back to back.** Midea units hold a
  single local connection and stop answering for a while after rapid session
  churn (the same behaviour that made `tools/probe_ieco_modes.py` lock a unit up).
  The candidate loop ran with no delay at all, so the verification could produce
  the very silence it then reported: candidate 1 legitimately rejected, candidates
  2 and 3 timing out against an already-blocked unit. There is now a pause between
  candidates (`CANDIDATE_DELAY`, 5 s) and between devices (`DEVICE_DELAY`, 2 s),
  matching how `midea_ieco_ensure.py` has always spaced its access.
- `VERIFY_TIMEOUT` raised from 10 s to 15 s. `msmart-ng`'s own failure path needs
  up to 11 s (5 s connect plus three internal 2 s read retries), so the old cap
  could cut its retry logic off mid-flight and report a timeout for a unit that
  would have answered — a false negative that also corrupted the new diagnosis.

## [0.2.0] - 2026-07-11

### Added
- `midea-ieco-update` command (and an `install.sh --update` mode) to update an
  existing installation in place: refreshes the code, the pinned dependencies,
  and the wrapper commands **without** touching `devices.json` or cron jobs.
  Works for both git- and ZIP-based installs. The updater fetches first and then
  re-execs the freshly fetched script, so the running updater is never the file
  being overwritten.
- The installer offers to add the bin directory (e.g. `/opt/local/bin`) to your
  `PATH` via an idempotent, self-guarding block in the shell rc chosen by your
  login shell (`~/.bashrc` / `~/.zshrc` / `~/.profile`). Only with your
  confirmation and a TTY; for non-interactive runs or paths with unusual
  characters it prints a manual hint instead of editing anything.
- `midea-ieco` with no argument (or `midea-ieco list`) now prints an instant,
  offline overview: what the tool does, the config path, the configured devices
  (name, IP and port only — never token or key), and the common commands. It never
  contacts a device. Previously a bare invocation exited with an argparse usage
  error.
- `midea-ieco-refresh-tokens` wrapper command, installed next to `midea-ieco`
  and `midea-ieco-update`, as a friendly equivalent of
  `venv/bin/python3 midea_refresh_tokens.py`.

### Changed
- **Token retrieval is now credential-free.** `midea_refresh_tokens.py` runs a
  single `midealocal.cli discover` call with no `--username`/`--password`, and
  writes an empty `midea-local.json` (`{}`) into a private per-call temporary
  directory used as the CLI's working dir. That pins the lookup to `midealocal`'s
  default (NetHome Plus) helper account and makes it independent of any
  user-global config — deterministic across hosts. The former command-line
  fallback (which briefly exposed the password in `ps`) is removed with it.
- Re-running `install.sh` on an already-configured installation no longer
  repeats the interactive onboarding (which would overwrite `devices.json`); it
  now refreshes code, dependencies, and wrappers, then exits. Use
  `install.sh --reconfigure` to deliberately redo device setup — the existing
  `devices.json` is backed up to `devices.json.bak` (git-ignored) first.
- `list` is now a reserved target word (like `all`): the installer rejects a
  device named `list`, and the overview flags a device already named
  `all`/`list` from an earlier install as unreachable from the command line.
- Both READMEs rewrite the token-retrieval story to match reality (device-bound
  tokens, the NetHome Plus API, the built-in helper account) and drop the earlier
  "`midea-local` signs in with your own account for account-bound credentials"
  explanation, which did not hold up.

### Removed
- **The Midea-cloud-credentials prompt, the `credentials.json` file, the
  `--username`/`--password` flags of `midea_refresh_tokens.py`, and the
  `credentials.example.json` template.** Fetching device tokens never actually
  used them: tokens are bound to the device (its UDP id), not to a cloud account,
  and Midea now issues them only through the NetHome Plus cloud API — the
  MSmartHome and Meiju `getToken` endpoints answer `errorCode 3004 "value is
  illegal"` (verified against a real unit in July 2026). Both `midea-local` and
  `msmart-ng` therefore sign in with a built-in helper account, so passing your
  own credentials had no effect. The installer no longer asks for a password, and
  none is stored anywhere. An existing `credentials.json` from 0.1.x is **not**
  deleted automatically (it may hold a plaintext password) — the installer and
  updater point out that it is now unused and can be removed.

### Fixed
- Corrected a stale version reference in `midea_refresh_tokens.py`: the
  isolation-config mechanism is verified against the pinned `midea-local` 6.6.1
  (the code path is identical in 6.6.1 and 6.10.0).

## [0.1.0] - 2026-07-10

First public release.

### Added
- `midea_ieco_ensure.py` — ensures iECO (and, optionally, power) is set on one
  or all configured Midea air conditioners over the local network, with an
  `--only-if-on` mode that never powers on an intentionally switched-off unit.
- `midea_refresh_tokens.py` — fetches and verifies per-device token/key pairs
  from the Midea cloud via `midea-local` and writes them to `devices.json`,
  keeping the cloud password out of the process command line.
- `install.sh` — one-shot installer (Debian/Ubuntu/Raspberry Pi OS, Fedora/RHEL,
  Arch, Alpine, openSUSE, macOS): sets up the venv and pinned dependencies,
  builds `devices.json` and `credentials.json` interactively, retrieves tokens,
  installs a `midea-ieco` wrapper, and optionally registers cron jobs.
- `midea_ieco_ensure.sh` — SSH/Shortcuts wrapper that forwards all arguments to
  the venv Python.
- Pinned dependencies via `requirements.txt` (`msmart-ng`, `midea-local`).
  Requires **Python 3.11+** (see Fixed).
- Stdlib-only test suite (`tests/`) and GitHub Actions CI across Python
  3.11–3.13, plus a real-dependency install-smoke CI job that installs the
  pinned requirements and verifies the runtime imports resolve.
- English and German documentation.

### Security
- `midea_refresh_tokens.py` runs each cloud discovery in a private, per-call
  temporary directory, so two concurrent runs can no longer race over a shared
  `midea-local.json` and fall back to passing the password on the command line
  (where it is briefly visible in `ps`).

### Fixed
- `midea_ieco_ensure.py` no longer reports a successful run as failed. After
  `apply()` it re-read `device.ieco` on a freshly connected object that had not
  called `get_capabilities()` — but `msmart-ng`'s `refresh()` only polls
  properties in `_supported_properties`, which `get_capabilities()` populates. So
  IECO was never polled and `device.ieco` returned its default `False`, even
  though iECO was actually active on the unit, producing a bogus "iECO is still
  disabled" failure. The verification (and the initial status read) now query
  capabilities before refreshing, so `device.ieco` reflects the real state; this
  also makes the "already in iECO, nothing to do" short-circuit work, so cron
  runs stop needlessly re-applying iECO.
- `install.sh` credentials prompt heading now reads "Midea-APP-Zugangsdaten".
- `install.sh` device discovery no longer reports "no devices found" when
  devices were in fact found. It now uses `midealocal.discover.discover()` (a
  local UDP broadcast, no cloud login required) and prints each device's **IP
  address and device ID** — the two values needed for `devices.json`. The old
  code parsed the INFO log of `midealocal.cli discover`, which prints only
  device state (temperature, mode) and no IP/ID, so its IP-address regex always
  missed and warned even when devices were found. The interactive setup can now
  **auto-fill** those discovered IP/device-id pairs and ask only for a name per
  device (manual entry stays available as a fallback), so the long device IDs no
  longer have to be retyped by hand.
- Corrected the supported Python floor to **3.11** (previously documented as
  3.10, which never actually worked). `midea-local` is now pinned to **6.6.1** —
  the newest release still supporting Python 3.11 (6.7.0+ require 3.12; no
  release supports 3.10) — keeping current Raspberry Pi OS (Bookworm, Python
  3.11) in scope. `install.sh`'s version check and the CI matrix now start at
  3.11. Caught by the new install-smoke CI job, which failed the real install of
  the previously-pinned `midea-local` 6.10.0 on Python 3.10/3.11.
- Pin `typing_extensions` in `requirements.txt`. `midea-local` imports it
  (`from typing_extensions import deprecated`) but does not declare it as a
  dependency, so `python -m midealocal.cli` crashed with `ModuleNotFoundError`
  on current Python (observed on 3.13). After installing dependencies the
  installer now verifies the core imports (`midealocal`, `msmart`) and, if they
  fail, installs the missing package and re-checks — self-healing instead of
  aborting — rather than surfacing a raw traceback mid-discovery.
- `install.sh` now `git pull`s an existing clone before installing, so re-running
  it brings an installation set up before a fix (e.g. the `typing_extensions`
  pin) up to date instead of keeping its stale files forever.
- `install.sh` no longer aborts silently right after installing dependencies.
  The informational version lookup (`pip show … | awk '…exit'`) could end the
  piped `pip` process with SIGPIPE; under `set -e -o pipefail` that non-zero
  status killed the whole installer before it reached the interactive setup and
  the `midea-ieco` wrapper install. The lookup now reads pip's output fully and
  is guarded with `|| true` so it can never abort the run.
- `midea_ieco_ensure.py all` now exits non-zero with a clear message when no
  devices are configured, instead of silently reporting success (`all([])`).
- The manual cron log-rotation example truncates `refresh.log` as well as
  `ieco.log`, matching the installer-generated job.

[Unreleased]: https://github.com/tuxbox78/midea-ieco/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tuxbox78/midea-ieco/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tuxbox78/midea-ieco/releases/tag/v0.1.0
