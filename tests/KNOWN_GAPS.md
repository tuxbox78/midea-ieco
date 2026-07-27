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

The survivors that review ranked by user damage have since been worked through. For
this round **88 mutations were applied one at a time and each was confirmed to turn
the suite red**, covering every finding except the ones listed below. No claim is
made that those 88 map one-to-one onto the 69 survivors above; they were derived
from the findings, not from that list.

To re-run the exercise:

```bash
cp -R . /tmp/mut && cd /tmp/mut && rm -rf .git venv
# edit one thing, then:
bash tests/run_all.sh
```

Use a copy. Mutating the real working tree risks committing a mutation by accident.

Four traps that have all produced wrong results here at least once:

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

Both Python tools now assert the **rendered** message of every multi-placeholder
call site, because a swapped pair produces a syntactically perfect but wrong
sentence. For `install.sh` only the four-value case (`dev_line_auto`, checked
end-to-end through the discovery path) and the cron lines are covered that way; the
remaining two-value installer messages are covered by the catalog completeness test
only.

*Cost:* cosmetic. These strings are progress output during a supervised, interactive
run, not diagnostics someone has to act on hours later.

---

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
  msmart-ng's own worst case (`authenticate` ≈ 12 s, `refresh` ≈ 6 s and it does not
  propagate network errors at all), so an all-timeout run cannot realistically occur.
  `VERIFY_CAP` still classifies correctly if it ever does; there is simply no invented
  advice attached to it.
- **The generated crontab of existing installations** is never rewritten. The
  installer points out managed lines that lack `MIDEA_IECO_LANG` and prints the
  corrected ones, but the user's crontab is theirs. This is a deliberate boundary,
  not a gap.
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
