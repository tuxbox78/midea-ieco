# midea-ieco

> 🇩🇪 **Deutsch:** Die vollständige deutsche Anleitung findest du hier: [README_german.md](README_german.md)

Small, reliable command-line tools for local control of the **iECO mode** (and general power/status) of Midea air conditioners, including the Midea PortaSplit and compatible models from Comfee, Toshiba, Carrier, Klimaire, and others. They avoid relying on an unstable cloud connection during normal operation.

`msmart-ng` can control iECO directly over the local network. Midea Cloud credentials are only required once to obtain valid device credentials, and again to refresh them when necessary.

## Why this project exists

### ECO vs. iECO — two different, easily confused modes

Midea air conditioners like the PortaSplit have **two separate energy-saving modes** that are frequently mixed up, including in some earlier drafts of this document. It is important to distinguish them correctly:

| | **ECO** (button/remote) | **iECO** (app/cloud only) |
|---|---|---|
| Activation | Physical button on the unit or remote control | Only through the MSmartHome / Midea Smarthome app |
| Target temperature | **Fixed automatically at 24 °C**, fan set to Auto | **Whatever target temperature the user has set** (e.g. 21 °C, 25 °C, etc.) — not fixed |
| Mechanism | Simple fixed setpoint | Cloud-connected, adaptive algorithm that fine-tunes compressor output around the user's chosen setpoint |
| Auto-shutoff | Can auto power off after a period of inactivity at setpoint | Automatically exits after eight hours and reverts to regular Auto mode |
| Availability | Available offline, works with the IR remote | Requires the unit to stay connected to Wi-Fi/cloud while active |

In short: **iECO does not force 24 °C.** It works at any temperature you have configured on the unit; it simply makes the compressor regulate more gently and efficiently around that setpoint instead of running at full unrestricted power. This project is specifically about **iECO**, not the simpler button-activated ECO mode.

### What iECO does

Midea advertises iECO as saving up to 60% compared with standard operation, with up to eight hours of operation using only 1.2 kWh at typical settings ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)). A German ten-hour practical test using the PortaSplit measured about 100 W lower consumption in iECO than in Auto mode while maintaining a comfortable room temperature of 24.5–25.7 °C ([4-Happy-Home on YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)). Community reports confirm that iECO can be run successfully at other target temperatures as well, such as 21 °C, with correspondingly adjusted (not fixed) energy use.

Measurements made for this project found approximately 4 kWh **per day** of additional consumption during continuous operation at a given setpoint when iECO was not active, with no apparent comfort or cooling advantage from running without it.

### The problem: iECO disappears after manual use

iECO automatically exits after eight hours and returns to regular Auto mode. More importantly, iECO can currently be enabled **only through the MSmartHome / Midea Smarthome app**; there is no physical iECO button on the remote control (that button only controls the simpler, fixed-24°C ECO mode described above).

If the air conditioner is subsequently switched off and on manually—at the unit or with the remote—iECO remains off. This is easy to miss because the unit otherwise appears to work normally, still respecting whatever target temperature was last set. Instead of remembering to open the app and re-enable iECO after every manual power cycle, this project automates that task reliably in the background.

### Why not just use the Midea app?

The app does not provide conditional logic such as "enable iECO only if the unit is already on," nor does it offer a public, documented API for third-party automation such as cron jobs or Siri. The libraries used here (`msmart-ng` and `midea-local`) communicate with the device on the local network, giving you control over when iECO is set without a cloud dependency during routine use.

## Included files

| File | Purpose |
|---|---|
| `install.sh` | One-shot installer: sets up venv, dependencies, `devices.json`, `credentials.json`, tokens, and cron job |
| `midea_ieco_ensure.py` | Checks and sets power status and iECO for one or all configured devices |
| `midea_refresh_tokens.py` | Retrieves fresh token/key pairs from Midea Cloud and updates `devices.json` |
| `midea_ieco_ensure.sh` | Wrapper for SSH/Shortcuts: runs `midea_ieco_ensure.py` with the venv Python and forwards all arguments |
| `devices.example.json` | Template for `devices.json` — copy it, then fill in your devices |
| `credentials.example.json` | Template for `credentials.json` — your Midea Cloud e-mail and password |
| `devices.json` | Your local device config (name, IP, port, ID, token, key). Generated locally, **git-ignored** |
| `credentials.json` | Your Midea Cloud login, read by `midea_refresh_tokens.py`. Created locally at `chmod 600`, **git-ignored** |

## Requirements

- Python 3.10 or later
- A Midea Cloud account (**MSmartHome** or **Midea Smarthome**) in which the devices are already registered and working
- The controlling computer must be on the same local network as the air conditioners (port 6444/TCP reachable — no client isolation or VLAN separation)

## Quick install (one-liner)

The fastest way to get started is the automated installer. It works on Debian/Ubuntu/Raspberry Pi OS, Fedora/RHEL, Arch Linux, Alpine, openSUSE, and macOS (with Homebrew):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
```

By default, the installer places all program files in `/opt/local/midea-ieco` and a small wrapper command called `midea-ieco` in `/opt/local/bin`. Both locations are configurable — either by editing the `DEFAULT_INSTALL_DIR` / `DEFAULT_BIN_DIR` variables at the top of `install.sh` (useful if you downloaded the script manually), or via environment variables without editing anything:

```bash
MIDEA_IECO_DIR=/your/custom/path MIDEA_IECO_BIN_DIR=/your/custom/bin \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
```

If the install directory doesn't exist yet, the installer creates it and hands ownership to you (using `sudo` only if its parent isn't writable). It never takes over a directory that already exists: if the install directory is present but not writable, it stops and shows your options (choose another path via `MIDEA_IECO_DIR`, fix the permissions yourself, or remove it). The small `midea-ieco` wrapper is placed into the bin directory with a single `sudo install` step when that directory is root-owned (e.g. MacPorts' `/opt/local/bin`), without changing the directory's ownership.

> **Before you begin:** Have your **MSmartHome username and password** ready — the same credentials used in the official Midea app. They are requested once during installation to fetch device tokens.

> **Recommended but not required:** Assign fixed IP addresses (DHCP reservation by MAC address) to your air conditioners in your router before running the installer. This prevents the IP from changing after a device or router restart. If you skip this step, or the IP changes later, you can always edit it afterwards directly in `devices.json` — no reinstall needed.

The installer will:

1. Detect your OS and package manager, and install missing prerequisites (`python3`, `python3-venv`, `git`/`curl`)
2. Create a Python virtual environment and install `msmart-ng` and `midea-local`
3. Ask for your Midea Cloud credentials and run device discovery
4. Let you enter device names, IPs, and IDs interactively to build `devices.json`
5. Retrieve token/key pairs and store them securely (`chmod 600`)
6. Create a `midea-ieco` wrapper command and offer an optional test run and cron job setup

### Manual installation (alternative)

If you prefer to install everything yourself instead of using `install.sh`:

```bash
# 1. Clone or download the repository
git clone https://github.com/tuxbox78/midea-ieco.git
cd midea-ieco

# 2. Create a Python virtual environment and install dependencies
# (required on Debian/Ubuntu/Raspberry Pi OS – do NOT use pip directly as root)
sudo apt-get install -y python3-venv   # only needed once if python3-venv is missing
python3 -m venv venv
source venv/bin/activate
pip install msmart-ng midea-local

# 3. Discover your devices and note their IDs and IP addresses
python3 -m midealocal.cli discover --username "YOUR_SMARTHOME_EMAIL" --password "YOUR_SMARTHOME_PASSWORD"

# 4. Assign fixed IP addresses to your air conditioners in your router
#    (DHCP reservation by MAC address) so that the configuration stays stable.
#    Not required — you can edit the IP later directly in devices.json.

# 5. Create devices.json from the template, then edit it (see "One-time setup" below)
cp devices.example.json devices.json

# 6. Create credentials.json from the template with your Midea Cloud login,
#    then restrict access (it holds your cloud password in plain text):
cp credentials.example.json credentials.json   # then edit username/password
chmod 600 credentials.json

# 7. Retrieve token/key pairs for all devices
python3 midea_refresh_tokens.py --all

# 8. Test: enable iECO on one device (the unit must be reachable on the network)
python3 midea_ieco_ensure.py LivingRoom

# The SSH/Shortcuts wrapper midea_ieco_ensure.sh ships executable; the cron
# examples below call the venv Python directly and do not need it. If your
# download method dropped the executable bit, restore it with:
chmod +x midea_ieco_ensure.sh
```

## One-time setup (manual path details)

### 1. Find device IDs and IP addresses

```bash
python3 -m midealocal.cli discover --username "YOUR_ACCOUNT" --password "YOUR_PASSWORD"
```

Record the device ID (`id`) and IP address for every device.

### 2. Create `devices.json`

```json
{
  "devices": [
    {
      "name": "LivingRoom",
      "ip": "192.168.0.186",
      "port": 6444,
      "id": 153931629346858,
      "token": "",
      "key": ""
    },
    {
      "name": "Bedroom",
      "ip": "192.168.0.185",
      "port": 6444,
      "id": 152832117825892,
      "token": "",
      "key": ""
    }
  ]
}
```

The token and key may initially be empty; `midea_refresh_tokens.py` retrieves them in the next step.

### 3. Store credentials in `credentials.json`

Copy the template and enter your Midea Cloud login:

```bash
cp credentials.example.json credentials.json
```

```json
{
  "username": "your@account.example",
  "password": "yourPassword"
}
```

Restrict access afterwards because this file contains your cloud password in plain text (the installer does this automatically):

```bash
chmod 600 credentials.json
```

Alternatively, pass `--username`/`--password` on each call, or let the script prompt you interactively when no file is present.

### 4. Retrieve token/key pairs

```bash
python3 midea_refresh_tokens.py --all
```

The script runs `python3 -m midealocal.cli discover --debug`, extracts the token and key from its output using a regular expression, and writes them back to `devices.json`. It also applies `chmod 600` to the configuration file automatically.

> **Why `midea-local` rather than `msmart-ng discover`?** `midea-local` authenticates with your own Midea account and therefore obtains device credentials associated with that account. `msmart-ng discover --auto` can use an internal helper account and may return credentials that change on every call or expire quickly, making them unsuitable for unattended use.

You can also add a new device directly by name and IP address:

```bash
python3 midea_refresh_tokens.py --name Kitchen --host 192.168.0.190
```

## Daily use

### Ensure iECO is enabled (powers on if necessary)

```bash
python3 midea_ieco_ensure.py LivingRoom
python3 midea_ieco_ensure.py all
```

This does **not** change your target temperature. It only ensures iECO is active at whatever temperature the unit is already set to.

### Only re-enable iECO when the unit is already on

This is recommended for cron:

```bash
python3 midea_ieco_ensure.py all --only-if-on
```

With `--only-if-on`, the script never turns on a unit. A unit that is off is left untouched; iECO is enabled when a unit is on and needs it. This makes frequent cron runs safe without starting an air conditioner that was intentionally switched off.

### Refresh token/key values

If a device reports `Connection reset`, a timeout, or a credential problem:

```bash
python3 midea_refresh_tokens.py --name LivingRoom
python3 midea_refresh_tokens.py --all
```

In practice, credentials often remain valid for a long time. Refresh them when an app session changes fundamentally (e.g. after changing your Midea account password) or when a device is reconnected to the network.

## Cron automation

If you didn't use `install.sh`'s automatic cron setup, edit your crontab with `crontab -e`:

```cron
# Every 20 minutes: re-enable iECO without turning units on
*/20 * * * * cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Every Sunday at 03:00: refresh credentials as a precaution
0 3 * * 0 cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Remember log rotation, for example with `logrotate` or simply:

```cron
0 0 1 * * truncate -s 0 /opt/local/midea-ieco/ieco.log
```

## Siri and iOS Shortcuts

The simplest solution without additional server software is the native iOS Shortcuts action **Run Script over SSH**.

### Linux host requirements

- An OpenSSH server running and reachable on the local network or through a VPN
- A dedicated SSH key for the iPhone (recommended instead of password authentication)

### Setup

1. In Shortcuts on the iPhone, create a new shortcut and add **Run Script over SSH**.
2. Enter the host, username, and authentication method (SSH key recommended).
3. Use a command such as:

   ```bash
   /opt/local/bin/midea-ieco LivingRoom
   ```

   or, without the wrapper:

   ```bash
   cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py LivingRoom
   ```

4. Name the shortcut, for example **Living room iECO**.
5. Call it with Siri, for example: *"Hey Siri, enable eco mode in the living room."*

> **Tip for non-interactive SSH sessions:** Use `venv/bin/python3` directly rather than `source venv/bin/activate && python3`. It is more robust because non-interactive shells can handle `source` differently.

For all devices, use `all` in place of the device name. Add `--only-if-on` if Siri must not turn on an intentionally powered-off unit.

### Alternative: Homebridge and HomeKit

If you prefer regular switches, status display, scenes, and automations in Apple Home, use Homebridge with `homebridge-cmd4`. It can map arbitrary shell commands to on/off/status operations, such as `midea_ieco_ensure.py LivingRoom` for "on." This is more work than the SSH Shortcuts option but provides full HomeKit integration.

## Network troubleshooting

If you see `No response from host` for every request, the most likely causes are:

- **Client isolation / AP isolation** active in your router for the WLAN the air conditioner is connected to — disable this for the relevant network segment
- **VLAN separation** between IoT devices and computers — ensure your server and both air conditioners are on the same VLAN, or create a firewall rule permitting TCP port 6444
- **IP address changed** — always assign fixed IP addresses (DHCP reservation by MAC address) in your router, or update `devices.json` manually if it changed
- **Device is in WLAN power-saving mode / sleep** — verify the device is reachable with `ping 192.168.x.x` and `nc -zv 192.168.x.x 6444`
- **Firewall on the server** blocking outgoing connections to port 6444 — check with `iptables -L` or `ufw status`

## Development lessons learned

This table documents specific observations made while developing this setup with `msmart-ng` in 2026. It is a reference rather than universal troubleshooting guidance: internal APIs can change between versions. When in doubt, inspect the version actually installed:

```bash
python3 -c "import inspect; from msmart.device.AC.device import AirConditioner as AC; print(inspect.signature(AC.__init__))"
```

| Symptom | Cause observed during development | Solution |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | The `midea-local` API changed | Inspect the installed signature with `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` |
| `Device is not capable of property IECO` | Capabilities were never queried on the object used for `apply()` — `supports_ieco` is populated only by `get_capabilities()` | Call `get_capabilities()` on the fresh, authenticated `AC` object before setting capability-bound properties and calling `apply()`. The order relative to `refresh()` does not matter — `midea_ieco_ensure.py` deliberately refreshes first (cheap status check) and queries capabilities only once a change is actually pending |
| Capability query times out / `Failed to query capabilities` although credentials are correct | Initially misread as "the unit answers `get_capabilities()` only while powered on" — neither the `msmart-ng` code nor the final flow supports that; a connection left broken by a previous failed attempt produces the same symptom (see next row) | Retry on a completely fresh connection: `midea_ieco_ensure.py` re-creates the `AC` object and re-queries capabilities on every retry. The shipped flow queries capabilities before powering the unit on |
| `[Errno 104] Connection reset by peer` after several attempts | A failed connection attempt left the `AC` object with a broken socket state | Create a **new** `AC` object for every retry |
| Token/key suddenly stop working | Usually the preceding socket issue, less often real credential invalidation | Run `midea_refresh_tokens.py --name <device>` |
| `msmart-ng discover` returns credentials that stop working shortly afterwards | `--auto` uses an internal helper account and its keys are temporary | Use `midea_refresh_tokens.py` via `midea-local` to obtain persistent credentials |
| Confusion between "ECO" and "iECO" in logs/UI | Midea's own documentation and app use similar naming for two different mechanisms | Remember: normal ECO = fixed 24 °C via button/remote; iECO = user's own setpoint via app/cloud algorithm |

## Security notes

- `devices.json` and `credentials.json` hold sensitive values (device token/key and your cloud password): keep both at `chmod 600`. Both are **git-ignored**, so your real values are never tracked — only the `*.example.json` templates are. (An early commit did track a `devices.json`, but it contained only non-functional dummy placeholders; no real credentials exist anywhere in the git history.)
- Your Midea Cloud password lives in `credentials.json` (plain text), read by `midea_refresh_tokens.py`. It is never written into any tracked source file.
- `midea-local.json` is a short-lived file `midea_refresh_tokens.py` writes (mode 0600) only for the duration of a cloud lookup, to hand your credentials to the `midealocal` CLI through a config file instead of the process command line (so the password is not visible in `ps`). It is removed afterwards and is **git-ignored**.
- For Siri over SSH, use SSH-key authentication and do **not** expose SSH to the Internet using port forwarding. Use a VPN (e.g. Tailscale) for remote access instead.

## License and sharing

This project is licensed under the [MIT License](LICENSE) — you may use, share, and adapt these scripts freely, provided the copyright notice stays included. The libraries it builds on, [`msmart-ng`](https://github.com/mill1000/midea-msmart) and [`midea-local`](https://github.com/rokam/midea-local), are MIT-licensed as well. This project is not affiliated with Midea and does not replace official Midea support.

---

> 🇩🇪 **Deutsche Anleitung:** [README_german.md](README_german.md)
