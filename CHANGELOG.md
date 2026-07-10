# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Stdlib-only test suite (`tests/`) and GitHub Actions CI across Python
  3.10–3.13.
- English and German documentation.

### Security
- `midea_refresh_tokens.py` runs each cloud discovery in a private, per-call
  temporary directory, so two concurrent runs can no longer race over a shared
  `midea-local.json` and fall back to passing the password on the command line
  (where it is briefly visible in `ps`).
- `install.sh` registers the short-lived `midea-local.json` for cleanup on any
  exit, so interrupting device discovery (Ctrl+C) never leaves the 0600
  credential file behind.

### Fixed
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

[Unreleased]: https://github.com/tuxbox78/midea-ieco/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tuxbox78/midea-ieco/releases/tag/v0.1.0
