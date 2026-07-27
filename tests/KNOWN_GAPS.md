# Known test gaps

This file lists behaviour that **survives deliberate mutation** — i.e. you can break
it in the source and the whole suite still passes. It exists so the next person
(or the next session) starts from a written baseline instead of re-deriving it.

Everything here is *known and accepted for now*, not forgotten. Each entry says what
breaks, what it would cost a real user, and the cheapest way to close it.

## How this list was produced

Two independent reviews mutation-tested the suite: break one thing in the source,
run `bash tests/run_all.sh`, record whether it goes red. The second review ran 165
mutations (111 caught, 51 survived) and ranked the survivors by user damage. The top
ten of that ranking have since been closed — this file is what remained, minus a few
entries closed alongside them.

To re-run the exercise:

```bash
cp -R . /tmp/mut && cd /tmp/mut && rm -rf .git venv
# edit one thing, then:
bash tests/run_all.sh
```

Use a copy. Mutating the real working tree risks committing a mutation by accident.

> **Stale-bytecode trap:** an edit that keeps the file size identical
> (`sys.exit(0)` → `sys.exit(1)`) within the same mtime second can be served from a
> stale `.pyc` and look like a survivor when it is not. `tests/run_all.sh` clears the
> caches before *and* after each run for this reason; if you run `unittest` directly,
> export `PYTHONDONTWRITEBYTECODE=1` yourself.

---

## Open gaps

### 1. `midea_ieco_ensure.py` — pacing beyond the constants

`RETRY_DELAY` and `CONNECT_RETRIES`/`ACTION_RETRIES` now have lower-bound tests, but
**removing the sleep calls themselves** still survives: the retry sleep, the 2.0 s
settle after `apply()`, and the 1.0 s pause between devices.

*Cost:* Midea units hold a single local connection and stop answering after rapid
access. Losing the pacing makes the tool manufacture the very "device is silent"
failures it then reports.

*Cheapest fix:* the event-order pattern from `CandidatePacingOrderTests` in
`test_refresh_tokens.py` — record `asyncio.sleep` calls interleaved with device
operations and assert the sequence, not just the count.

### 2. `midea_refresh_tokens.py` — `SUBPROCESS_TIMEOUT` has no headroom test

`SUBPROCESS_TIMEOUT = 60` can be lowered to `3` unnoticed. `VERIFY_TIMEOUT` has a
headroom test anchored to msmart-ng's own worst case; this constant does not.

*Cost:* a discover call that needs more than the (too short) limit fails with a
timeout message on every run, and the cause looks like a network problem.

*Cheapest fix:* an assertion tying it to a realistic cloud round-trip, in the shape
of `VerifyTimeoutHeadroomTests`.

### 3. `_parse_discover_output` — two diagnostic details

- `returncode != 0` → `> 0` survives: a discover killed by a signal (negative
  return code) would be treated as success.
- The failure tail `combined_output[-800:]` → `[:800]` survives: the user would see
  the *first* 800 characters of a long debug log instead of the last, i.e. the setup
  noise instead of the actual error.

*Cost:* low individually, but both make a real failure harder to read — which is the
entire point of that code path.

### 4. `_atomic_write_json` — `os.fsync` can be dropped unnoticed

The 0600 mode, the `os.replace`, and the "original survives a crash" property are all
tested. The `fsync` is not.

*Cost:* only matters on power loss between write and rename. Low probability, total
loss of `devices.json` if it happens.

*Cheapest fix:* assert `os.fsync` was called, via `mock.patch` on the module.

### 5. `midea_ieco_ensure.py` — device is never closed

Removing `close_device` calls, or gutting the `finally` block, survives.

*Cost:* leaked sockets against a device that tolerates exactly one connection. In a
20-minute cron this could accumulate into the blocked state the project documents
elsewhere.

*Cheapest fix:* `_RecordingAC` in `test_ensure.py` already records a `close` call —
assert on it from the `ensure_ieco` paths.

### 6. `msmart-ng` wordings that still fall through to `VERIFY_OTHER`

The classification now covers the ERROR frame, wrong key (SHA256 digest mismatch),
silence, reset (both `Transport is closing` and a real `Connection reset by peer`),
refused, and connect timeout. These remain unclassified and therefore produce no
summary hint:

| message | source |
|---|---|
| `Invalid data length for key handshake.` | `lan.py` |
| `Token and key must be supplied.` | `lan.py` |
| `Unexpected type: N` | `lan.py` |
| `Protocol has not been authenticated.` | `lan.py` |
| `Invalid start of packet:` / `Invalid magic byte:` | `lan.py` |

*Cost:* low — these indicate a protocol-level anomaly rather than a user-fixable
condition, and the raw message is still shown verbatim. Worth revisiting if any of
them shows up in a real bug report.

### 7. `MsmartMissingProbeTests` self-skips where msmart-ng is installed

`test_probe_exits_before_cloud_contact` is the **only** test covering "refresh exits
1, before any cloud contact, when msmart-ng is missing". It skips itself when
msmart-ng *is* importable — so on a developer machine with it installed for the
system interpreter, that behaviour is silently unguarded.

*Cheapest fix:* drive the same path in-process with `sys.modules` manipulation
instead of relying solely on the subprocess probe.

### 8. `env -i` (no `HOME`) breaks two install-update tests

Pre-existing and confirmed against `a159ec7`. `git` needs `HOME` for its config, so
the `install.sh --update` end-to-end tests fail without it. Also, `env -i` with a
bare `PATH` may resolve `python3` to a system interpreter older than the project's
3.11 floor, which reduces how much of the suite runs at all.

*Not a product defect* — cron and every realistic invocation set `HOME`. Listed so
nobody re-diagnoses it from scratch.

---

## Equivalent mutants (survive by design, not by omission)

Removing `asyncio.TimeoutError` from the `isinstance` check in
`classify_verify_failure` survives — correctly. On Python 3.11+ (the project's
floor) `asyncio.TimeoutError is TimeoutError`, so the edit changes nothing at all.
It is spelled out anyway because on older interpreters they are distinct classes,
and the branch would then silently never fire; this was found when the module
became importable under Python 3.9 and the timeout test went red there.

Do not "fix" this by adding a test. There is nothing to observe on a supported
interpreter.

## Deliberately not covered

- **`hint_all_cap`** was removed rather than tested: our verification cap sits above
  msmart-ng's own worst case (`authenticate` ≈ 12 s, `refresh` ≈ 6 s and it does not
  propagate network errors at all), so an all-timeout run cannot realistically occur.
  `VERIFY_CAP` still classifies correctly if it ever does; there is simply no invented
  advice attached to it.
- **The generated crontab of existing installations** is never rewritten. The
  installer points out a missing `MIDEA_IECO_LANG` and prints the corrected lines, but
  the user's crontab is theirs. This is a deliberate boundary, not a gap.
