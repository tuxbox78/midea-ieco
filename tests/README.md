# Tests

Stdlib-only tests — no external dependencies, no AC hardware or Midea Cloud
account required. Run everything with:

```bash
bash tests/run_all.sh
```

Contents:

- `test_refresh_tokens.py` — unit tests for `midea_refresh_tokens.py`
  (config loading/atomic write, credential resolution incl. CLI-placeholder
  rejection and EOF handling, token extraction, the discover config-file
  route, the msmart-missing probe).
- `test_ensure.py` — unit tests for `midea_ieco_ensure.py` (config loading, the
  apply-retry hardening, and the offline `list`/no-argument overview — exit 0,
  no network contact, and never prints token/key).
- `_stub_msmart.py` — registers a minimal fake `msmart` package so the modules
  import without the real dependency.
- `test_install.sh` — extracts individual `install.sh` functions and exercises
  them in isolation (atomic 0600 writes, cron-line quoting incl. both logs
  covered by the logrotate line, device-name validation, the triplet device
  write, the directory-ownership safety of `ensure_install_dir`, the
  pipefail-safe version extraction, the shell-safe wrapper-heredoc quoting for
  all three generated wrappers, and rejection of the reserved device names
  `all`/`list`). The installer itself is never executed.
