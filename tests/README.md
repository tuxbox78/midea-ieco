# Tests

Stdlib-only tests — no external dependencies, no AC hardware or Midea Cloud
account required. Run everything with:

```bash
bash tests/run_all.sh
```

Contents:

- `test_refresh_tokens.py` — unit tests for `midea_refresh_tokens.py`
  (config loading/atomic write, credential resolution, token extraction, the
  discover config-file route, the msmart-missing probe).
- `test_ensure.py` — unit tests for `midea_ieco_ensure.py` (config loading and
  the apply-retry hardening).
- `_stub_msmart.py` — registers a minimal fake `msmart` package so the modules
  import without the real dependency.
- `test_install.sh` — extracts individual `install.sh` functions and exercises
  them in isolation (atomic 0600 writes, cron-line quoting, device-name
  validation, the triplet device write, and the directory-ownership safety of
  `ensure_install_dir`). The installer itself is never executed.
