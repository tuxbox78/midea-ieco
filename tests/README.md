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
  `all`/`list`, the obsolete-`credentials.json` hint that never deletes, and the
  i18n catalog check in both directions). The installer itself is never executed.
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
