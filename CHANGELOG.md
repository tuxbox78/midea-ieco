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
- `install.sh` device discovery no longer reports "no devices found" when
  devices were in fact found. It now uses `midealocal.discover.discover()` (a
  local UDP broadcast, no cloud login required) and prints each device's **IP
  address and device ID** — the two values needed for `devices.json`. The old
  code parsed the INFO log of `midealocal.cli discover`, which prints only
  device state (temperature, mode) and no IP/ID, so its IP-address regex always
  missed and warned even when devices were found.
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

[Unreleased]: https://github.com/tuxbox78/midea-ieco/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tuxbox78/midea-ieco/releases/tag/v0.1.0
