# Known test gaps

This file lists behaviour that **survives deliberate mutation** — i.e. you can break
it in the source and the whole suite still passes. It exists so the next person
(or the next session) starts from a written baseline instead of re-deriving it.

Everything here is *known and accepted for now*, not forgotten. Each entry says what
breaks, what it would cost a real user, and the cheapest way to close it.

## How this list was produced

Several independent reviews mutation-tested the suite: break one thing in the
source, run `bash tests/run_all.sh`, record whether it goes red. The most recent
independent run reported **213 mutations, 144 caught, 69 survived**. That count
comes from the review and was **not** re-derived here — do not treat it as a
measurement of the current tree.

The survivors that review ranked by user damage have since been worked through. In
the round before this one, **88 mutations were applied one at a time and each was
confirmed to turn the suite red**. No claim is made that those 88 map one-to-one
onto the 69 survivors above; they were derived from the findings, not from that
list. That round's header claimed the 88 covered "every finding except the ones
listed below" — they did not: two mutations derived from those same findings
(dropping `echo "$EXISTING_CRON"`, dropping the connection-retry pause) survived
and were not listed.

The round after that started by re-checking ten known survivors, then closed them
one phase at a time. What was **measured** at the end, rather than argued:

- the ten survivors that opened the round (foreign cron jobs, `~/.bashrc`
  truncation, the `devices.json` backup, the config-safe re-run guard, the
  connection-retry pause, two argument swaps, and `exec` in the wrapper) each turn
  the suite red now;
- six mutations of the rewritten cron language check, each caught by the fixture
  that names its case;
- all 60 adjacent-pair swaps derived from the AST (see gap 4 below);
- the suite stays green under the default locale, `de_DE.UTF-8`, `LC_ALL=C`,
  `tr_TR.UTF-8`, `MIDEA_IECO_LANG=de` and under Python 3.9, and `shellcheck` 0.9.0
  (the version CI runs) reports nothing on any of the five shell files.

None of that says the suite is complete — only that these specific things were
checked. What is known to be open is below.

To re-run the exercise:

```bash
cp -R . /tmp/mut && cd /tmp/mut && rm -rf .git venv
# edit one thing, then:
bash tests/run_all.sh
```

Use a copy. Mutating the real working tree risks committing a mutation by accident.

Five traps that have all produced wrong results here at least once:

> **Check the baseline first.** If the copy is already red, every mutation result is
> worthless. A newly reported `shellcheck` finding is enough to cause this.
>
> **Assert that the mutation was actually applied** (count the replacements). An
> edit that silently matched nothing looks exactly like a survivor.
>
> **Run the right part of the suite.** A mutation in `install.sh` cannot be caught
> by `python3 -m unittest`; a shell-only run says "survived" for reasons that have
> nothing to do with test coverage.
>
> **Stale bytecode:** an edit that keeps the file size identical
> (`sys.exit(0)` → `sys.exit(1)`) within the same mtime second can be served from a
> stale `.pyc` and look like a survivor when it is not. `tests/run_all.sh` clears the
> caches before *and* after each run for this reason; if you run `unittest` directly,
> export `PYTHONDONTWRITEBYTECODE=1` yourself.
>
> **Replace the whole thing.** A replacement that matches only the first fragment of
> a multi-line string leaves the rest standing and fakes a survivor. This happened
> with a catalogue text wrapped over four lines.

---

## Open gaps

### 1. `msmart-ng` wordings that still fall through to `VERIFY_OTHER`

The classification covers the ERROR frame, a wrong key (SHA256 digest mismatch),
silence, reset (both `Transport is closing` and a real `Connection reset by peer`)
and the connect timeout. The following wordings from `msmart-ng` 2026.7.0 remain
unclassified and therefore produce no summary hint (all verified against the pinned
version's `lan.py`):

| message | source |
|---|---|
| `Invalid data length for key handshake.` | `lan.py` |
| `Token and key must be supplied.` | `lan.py` |
| `Unexpected type: N` | `lan.py` |
| `Unknown type: N` | `lan.py` (distinct from `Unexpected type`) |
| `Protocol has not been authenticated.` | `lan.py` |
| `Invalid start of packet:` / `Invalid magic byte:` | `lan.py` |
| `Packet is too short:` | `lan.py` |
| `Packet is truncated.` | `lan.py` |
| `Unsupported packet:` | `lan.py` |
| a bare `IOError()` with no message at all | `lan.py` |

*Cost:* low — these indicate a protocol-level anomaly rather than a user-fixable
condition, and the raw message is still shown verbatim. Worth revisiting if any of
them shows up in a real bug report.

### 2. Three wordings are classified, but the classification can be wrong

These are *latent*: they cannot currently reach a user, because the code paths that
would produce them are swallowed inside `msmart-ng` (`base_device.py` catches
`ProtocolError`/`TimeoutError` in `_send_command`, so `refresh()` never propagates
them). Written down because the marker text alone does not distinguish them:

- **`digest do not match` matches three different states** in `lan.py`: the intended
  SHA256 handshake mismatch (key really is wrong), an **MD5** check against a
  hard-wired constant key (nothing to do with the device key at all), and a SHA256
  check *after* a successful handshake. All three currently report "token accepted,
  key wrong — re-run token retrieval"; for the latter two the key is demonstrably
  correct.
- **`Read cancelled.`** is raised by `msmart-ng` as a `TimeoutError` when its own
  read is cancelled. Our `isinstance(exc, TimeoutError)` branch therefore reports it
  as *our* verification cap, which it is not.

*Cheapest fix:* match the marker together with the exception type, or ask msmart-ng
upstream for distinguishable messages. Not worth it while the paths are unreachable.

### 3. `env -i` (no `HOME`) breaks two install-update tests

Pre-existing and confirmed against `a159ec7`. `git` needs `HOME` for its config, so
the `install.sh --update` end-to-end tests fail without it.

*Not a product defect* — cron and every realistic invocation set `HOME`. Listed so
nobody re-diagnoses it from scratch.

### 4. Installer messages with several placeholders are only spot-checked

For the two Python tools this was measured rather than asserted. The call sites
were derived from the AST — every `t()` call with two or more values, each
adjacent pair swapped in turn — which yields **60 mutations**. 25 of them survived
the suite; all 60 turn it red now, and the survey was re-run to confirm that. The
expectations are rendered from the catalogue via `t()`, so a reworded message
moves the assertion with it; what is pinned is the argument order.

Two limits of that measurement, so it is not read as more than it is: it covers
*adjacent* pairs (a three-way rotation is not in the 60), and it covers the two
Python modules only.

For `install.sh` only the four-value case (`dev_line_auto`, checked end-to-end
through the discovery path) and the cron lines are covered that way; the remaining
two-value installer messages are covered by the catalog completeness test only.

*Cost:* cosmetic. These strings are progress output during a supervised, interactive
run, not diagnostics someone has to act on hours later.

### 5. The cron language notice ignores standalone `LANG=`/`LC_ALL=` lines

The notice walks the crontab line by line and honours the nearest preceding
`MIDEA_IECO_LANG` assignment. A standalone `LANG=de_DE.UTF-8` line would also make
the jobs log in German — `resolve_lang` falls back to the locale — but it does not
suppress the notice.

*Cost:* a superfluous notice in an exotic setup. It suggests a line to copy and
never rewrites anything, so the worst case is one paragraph of unnecessary advice.
Deliberate: the notice recommends `MIDEA_IECO_LANG`, and matching every way a
locale can arrive would widen the check for no practical gain.

---

### 6. The cron language notice misjudges four shapes of hand-edited line

The notice asks one question: does this managed cron line set `MIDEA_IECO_LANG`?
Answering it exactly means parsing the command field the way `/bin/sh` does —
quotes, backslash escapes, command separators, and the schedule fields in front.
Two attempts at that parser each introduced defects worse than the gap (one of
them would have made the installer warn about every line it writes itself), so
the shapes below are measured and written down instead:

| line (no environment assignment above it) | notice | truth |
|---|---|---|
| `… MIDEA_IECO_LANG='   ' venv/bin/python3 …` | silent | job logs English |
| `… # midea-ieco-managed # MIDEA_IECO_LANG=de` | silent | job logs English |
| `… midea_ieco_ensure.py --note MIDEA_IECO_LANG=de all` | silent | job logs English |
| `… >> /opt/MIDEA_IECO_LANG=de.log 2>&1` | silent | job logs English |

All four need a hand-edited crontab, and the cost is a missing hint — never a
wrong action. The reverse direction exists too and is the safe one: a token after
the command word with an *empty* value produces a superfluous notice.

A related, deliberate boundary: a value that is present but not one this project
understands (`MIDEA_IECO_LANG=de # x`, which cron stores verbatim, or `fr`)
counts as set. The notice reports "you never set it", not "you set it wrongly" —
second-guessing a value the user typed would nag whoever chose
`MIDEA_IECO_LANG=en` on purpose.

### 7. Where a test guards a conjunction rather than each half

- **The wrapper test's `exec sleep` and its `>/dev/null`** each remove the stall
  on their own, so neither can be killed individually. What is guarded is the
  conjunction: `tests/run_all.sh` captures the wrapper test's output the way CI
  does and fails above 8 seconds (normal ≈ 1 s, the known fault ≈ 11 s).
- **The `git`/`curl`/`unzip` stubs in the end-to-end sandboxes** cannot be killed
  either: with the `MIDEA_IECO_*` unset in place the suite makes no such calls
  anyway. The `unset` itself *is* killable — removing it and pointing
  `MIDEA_IECO_RESOLVED_DIR` at a victim directory fills it with 898 files.
  Whether the suite still writes outside its sandbox is a checkpoint someone has
  to run, not a permanent assertion; a permanent one would need a recursion guard
  and roughly half again the runtime.

### 8. What the inactive-job notice cannot see

`print_cron_missing_hint` matches the prefixes `midea_ieco_ensure` and
`midea_refresh_tokens` on marked, active lines. Consequences, all print-only:

- a hand-written marked line invoking the **`midea-ieco` bin wrapper** is not
  recognised, so the notice fires although the job exists (superfluous advice —
  the marker text itself contains `midea-ieco`, so matching it would hit every
  managed line);
- an install directory whose *path* contains `midea_ieco_ensure` makes every
  marked line look like the iECO job, silencing that half of the notice;
- a foreign line carrying our marker makes the notice fire for both tools;
- the notice is not suppressible: a user who removed the jobs but left a
  marker-bearing comment sees it on every run.

## Equivalent mutants (survive by design, not by omission)

Do not "fix" these by adding a test — there is nothing to observe.

- **Removing `asyncio.TimeoutError` from the `isinstance` check** in
  `classify_verify_failure`. On Python 3.11+ (the project's floor)
  `asyncio.TimeoutError is TimeoutError`, so the edit changes nothing. It is spelled
  out anyway because on older interpreters they are distinct classes and the branch
  would then silently never fire; this was found when the module became importable
  under Python 3.9 and the timeout test went red there.
- **Removing `os.fchmod(fd, 0o600)` from `_atomic_write_json`.** `tempfile.mkstemp`
  already creates the file with mode 0600, so the call cannot change the outcome.
  The 0600 assertion in `SaveConfigTests` passes either way — correctly. The call
  stays as an explicit statement of intent that does not depend on a `mkstemp`
  implementation detail.

**No longer an equivalent mutant:** adding `VERIFY_SILENT` to `_ANSWERED_CODES` used
to be one, because the old `summarize_failure_hint` only ever intersected that set.
The current version also tests `unique <= _ANSWERED_CODES`, so the edit now changes
observable behaviour and is caught. Mentioned here because an earlier analysis
listed it as equivalent, and that no longer holds.

## Deliberately not covered

- **`hint_all_cap`** was removed rather than tested: our verification cap sits above
  msmart-ng's own worst case (`authenticate` ≈ 12 s and it does not propagate
  network errors at all; the `refresh` ≈ 6 s figure is an estimate that has not been
  re-derived from the pinned source), so an all-timeout run is not expected to occur.
  `VERIFY_CAP` still classifies correctly if it ever does; there is simply no invented
  advice attached to it.
- **The generated crontab of existing installations** is never rewritten. The
  installer points out managed lines that lack `MIDEA_IECO_LANG` and prints the
  corrected ones, but the user's crontab is theirs. This is a deliberate boundary,
  not a gap.
- **`hint_mixed` also fires when the first candidate was unclassified**, e.g.
  `[other, rejected, silent]`. The head is deliberately more permissive than the
  tail: both claims of the text hold — the device did answer, and afterwards it did
  not — and what happened *before* the first answer cannot change either, whereas
  the same code in the tail would undercut the asserted ending. 64 of the 2801
  sequences up to length four take this path. Written down and pinned by a test so
  the asymmetry is not later mistaken for an oversight and tightened away, which
  would drop a correct hint.
- **The `exec` test compares process identity, not signals.** `tests/test_wrapper.sh`
  asserts that the wrapper *becomes* the Python process (same PID), which is the
  property `exec` provides and from which signal delivery follows; it does not send a
  `SIGTERM` and observe the process tree. Should a shell optimise the last command of
  a script into an `exec` by itself, the test stays green for correct code and only
  loses its grip on the mutation. Measured against this project's bash (3.2), the
  call without `exec` forks.
- **The wording of `midea_ieco_ensure.sh`'s venv error message.** The wrapper is glue
  code and, unlike the two Python tools, single-language. `tests/test_wrapper.sh`
  pins the exit code and that the message names the missing path and points at
  `install.sh` — deliberately not the phrasing, so translating it later needs no
  test change.

## Corrections to earlier versions of this file

Recorded so the same wrong statements do not get re-derived from the history:

- The header used to read "165 mutations (111 caught, 51 survived)". Those numbers do
  not add up (111 + 51 = 162) and the three excluded equivalent mutants were missing
  from the arithmetic. Replaced with the counts above.
- **`refused` was listed as a covered classification.** The very commit that added
  that line had *removed* the word from the code, because `Connect failed.` does not
  mean a refusal. The document asserted what the fix had just eliminated.
- The table of unclassified `msmart-ng` wordings was incomplete; four entries and the
  bare `IOError()` have been added above.
- The entry about `MsmartMissingProbeTests` self-skipping named only that one test;
  `OverviewWithoutMsmartTests` in `test_ensure.py` has the same property. Both now
  have companions that run everywhere (`RefreshWithoutMsmartTests`,
  `OverviewWithoutMsmartAnywhereTests`), so the gap is closed rather than documented.
- The claim that Python 3.9 "reduces how much of the suite runs at all" was true for
  the predecessor commit only. Since the `__future__` import all modules are
  importable under Apple's system Python 3.9 and the full suite runs there.
- The commit message of `f97a025` says "228 Python (was 180)". The correct previous
  number is **166** — verified by running the suite at `f97a025^`.
- **"Both Python tools now assert the rendered message of every multi-placeholder
  call site."** They did not: 25 of 60 adjacent-pair swaps survived when that
  sentence was written. It has been replaced by what was measured, together with
  what the measurement does *not* cover (gap 4 above).
- **The header claimed the round's 88 mutations covered "every finding except the
  ones listed below".** Two mutations derived from those findings survived without
  being listed. Corrected at the top of this file.
- **`tests/README.md` listed `git` among the onboarding sandbox's PATH stubs.** It
  is not one there — it is simply never called, because without a `.git` directory
  `fetch_project_files` takes another branch. `git` *is* a stub in the `--update`
  sandbox, and `crontab` is not needed there. The sentence now describes each
  sandbox separately.
- The `--reconfigure` re-run had no `.bak` assertion, and the "commented-out
  environment line does not count" fixture passed for a different reason than its
  name suggested (the variable name check rejects `# MIDEA_IECO_LANG` before the
  comment guard ever matters). The case the comment guard actually protects — a
  commented-out managed *job* line — now has its own fixture.
