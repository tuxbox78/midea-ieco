# Tests

Stdlib-only tests — no external dependencies, no AC hardware or Midea Cloud
account required. Run everything with:

```bash
bash tests/run_all.sh
```

Contents:

- `test_refresh_tokens.py` — unit tests for `midea_refresh_tokens.py`
  (config loading/atomic write, token extraction, and the credential-free
  discover invocation: exact argv, the empty-`{}` isolation config written 0600
  into a per-call temp dir, temp-dir cleanup on both success and error, a single
  invocation with no fallback, that the removed `--username`/`--password` flags
  are rejected, and the msmart-missing probe).
- `test_ensure.py` — unit tests for `midea_ieco_ensure.py` (config loading, the
  apply-retry hardening, and the offline `list`/no-argument overview — exit 0,
  no network contact, and never prints token/key).
- `_stub_msmart.py` — registers a minimal fake `msmart` package so the modules
  import without the real dependency.
- `test_install.sh` — extracts individual `install.sh` functions and exercises
  them in isolation (the atomic 0600 `devices.json` write, cron-line quoting
  incl. both logs covered by the logrotate line, device-name validation, the
  triplet device write, the directory-ownership safety of `ensure_install_dir`,
  the pipefail-safe version extraction, the shell-safe wrapper-heredoc quoting
  for all three generated wrappers, rejection of the reserved device names
  `all`/`list`, the obsolete-`credentials.json` hint that never deletes, the
  per-line cron language check, and the i18n catalog check in both directions).
  It also runs the **real installer end-to-end** in a fully stubbed sandbox —
  `--update`, and onboarding with and without discovered devices, with an empty
  crontab and with foreign jobs already in it — and asserts what actually reaches
  `crontab -`, including that existing entries survive byte-identically. Nothing
  leaves the sandbox: `HOME` and the install/bin directories point into a temp dir
  and no network is touched. What actually keeps it there is the `unset` of the
  whole `MIDEA_IECO_*` family at the head of the file — `install.sh` resolves its
  install directory from `MIDEA_IECO_RESOLVED_DIR` first and exports that variable
  itself, so an inherited value aims every end-to-end run at a foreign directory.

  The network commands are stubbed on top of that, as defence in depth — but not
  uniformly, and it is worth knowing which sandbox does what:

  | sandbox | `python3` | `pip` | `crontab` | `git` | `curl` / `unzip` |
  |---|---|---|---|---|---|
  | onboarding | probe answers, real interpreter otherwise | yes | yes | loud failure | loud failure |
  | `--update` | yes | yes | — | functional shim, exits 0 | loud failure |
  | temp-leak | yes | — | — | silent `exit 0` | silent `exit 1` |

  A green run does *invoke* those stubs — measured with instrumented stubs: nine
  calls in total (8 × `git`, 1 × `curl`, 0 × `unzip`). What holds is not that there
  are none, but that none of them reaches the real binary.

  The stubbing is also not what keeps a *real* run inside its directory:
  `python3 -m venv` creates the venv, and `setup_venv_and_deps` then sources
  `venv/bin/activate`, which puts `venv/bin` in front of `PATH` — the packages are
  installed by the *venv's own* `pip`, which a `PATH` stub cannot intercept. Inside
  these sandboxes the fake `activate` leaves `PATH` alone, so there the `PATH` stub
  does fire (51 calls in the same run). The onboarding `python3` stub answers only
  the installer's probe calls and delegates the `devices.json` write to the real
  interpreter, so the test does not merely check its own stub.
- `test_wrapper.sh` — functional tests for `midea_ieco_ensure.sh`, the wrapper
  behind the Siri shortcuts and SSH calls: that every argument (especially
  `--only-if-on`) reaches Python unchanged, that the exit code is passed through,
  that a missing venv produces a clear error instead of a cryptic `exec` failure,
  and that the wrapper *becomes* the Python process (same PID) rather than forking
  — the property `exec` is there for. The wrapper runs as a copy in a sandbox
  against a fake venv Python. Three time quantities in here hang together and are
  asserted to: the start budget for that fake Python (4 s) must stay below the time
  limit `run_all.sh` applies to this file (8 s), which must stay well below the
  stall a left-behind process produces (20 s). Get that order wrong and the guard
  in `run_all.sh` stops catching its own fault — which is what happened once.
- `KNOWN_GAPS.md` — behaviour that still survives deliberate mutation, with the
  cost of each gap and the cheapest way to close it. Read it before assuming a
  green run means a behaviour is covered, and update it whenever you close one.

## Verifying that a test actually protects something

A green suite is not evidence on its own. The habit this project uses: copy the
repo to `/tmp`, break the behaviour in the source, and confirm the suite goes red.
Several tests here exist because that exercise found assertions that could no
longer fail — for example one checking for German wording in a subprocess that had
since switched to English. Text assertions are therefore derived from the message
catalogue (`_longest_literal`) rather than retyped, so a reworded message moves the
assertion with it instead of quietly voiding it.
