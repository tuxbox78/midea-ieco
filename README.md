# midea-ieco

> 🇩🇪 **Deutsch:** For the complete German documentation, see [README_german.md](README_german.md).

Small, reliable command-line tools for locally controlling the **iECO mode** and power/status of Midea air conditioners. This includes the Midea PortaSplit and potentially compatible models from Comfee, Toshiba, Carrier, Klimaire, and other Midea-based brands.

The normal control path is local LAN communication; a Midea Cloud login is required only to obtain or refresh the device token/key credentials. No Home Assistant is required.

## Quick start

Your air conditioner must already be set up in the **MSmartHome / Smarthome app** and connected to Wi-Fi. Have the **email address (or user name) and password of that Midea app account** ready. They are needed by `midea_refresh_tokens.py` to obtain local device credentials; they are **not** your Wi-Fi credentials.

> **Security first:** Your Midea account password, device token, and device key are secrets. Do not commit them to GitHub and do not publish them in issues, log files, screenshots, or forum posts.

### 1. Download the project

**With Git (recommended):**

```bash
git clone https://github.com/tuxbox78/midea-ieco.git
cd midea-ieco
```

**Without Git:** On the GitHub project page, choose **Code → Download ZIP**, extract the archive, then open a terminal in the extracted `midea-ieco` directory. A GitHub Release is not required for this: GitHub automatically provides a ZIP of the current source tree.

### 2. Create the Python environment

On Debian/Ubuntu, install virtual-environment support once:

```bash
sudo apt update
sudo apt install -y python3-venv
```

Then, from the project directory:

```bash
python3 -m venv venv
venv/bin/python3 -m pip install --upgrade pip
venv/bin/python3 -m pip install msmart-ng midea-local
```

> Do **not** use `sudo pip install` or `--break-system-packages`. A virtual environment keeps this project separate from the Python packages managed by Debian.

### 3. Configure your device list

Open `devices.json`. For a new installation, it may simply contain an empty list:

```json
{
  "devices": []
}
```

Alternatively, create an entry yourself; only the name and current LAN IP address are needed initially:

```json
{
  "devices": [
    {
      "name": "LivingRoom",
      "ip": "192.168.0.186",
      "port": 6444,
      "id": "",
      "token": "",
      "key": ""
    }
  ]
}
```

Create a DHCP reservation in your router for every air conditioner so its IP address remains stable.

### 4. Add your Midea app credentials

Open `midea_refresh_tokens.py` and replace these placeholders near the top of the file:

```python
DEFAULT_USERNAME = "USERNAME_EMAIL_SMARTHOMEAPP"
DEFAULT_PASSWORD = "PASSWORD_SMARTHOMEAPP"
```

with the credentials of the account used in the MSmartHome / Midea Smarthome app. Then restrict access to the two files:

```bash
chmod 600 devices.json midea_refresh_tokens.py
```

The script also accepts `--username` and `--password` command-line options. Storing the password in the protected local file is generally preferable, because a password passed on a command line can be visible temporarily to other local processes/users.

### 5. Obtain local device credentials

**To add the first device** (the script adds the entry and obtains its ID, token, and key):

```bash
venv/bin/python3 midea_refresh_tokens.py \
  --name LivingRoom \
  --host 192.168.0.186
```

**To refresh every device already listed in `devices.json`:**

```bash
venv/bin/python3 midea_refresh_tokens.py --all
```

The script calls `midealocal.cli discover --debug`, extracts the local token/key pair, and writes the result to `devices.json`.

### 6. Test local control

```bash
venv/bin/python3 midea_ieco_ensure.py LivingRoom
```

For every configured device:

```bash
venv/bin/python3 midea_ieco_ensure.py all
```

If the command reports a timeout or token/key error, refresh the credentials first and repeat the test. Do not share the resulting `devices.json` file.

## Why this project exists

### What iECO does

Midea advertises iECO as saving up to 60% compared with standard operation and as providing up to eight hours of operation using 1.2 kWh ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)). A German ten-hour practical test of the PortaSplit measured approximately 100 W less power consumption in iECO than in Auto mode while maintaining a comfortable 24.5–25.7 °C room temperature ([4-Happy-Home on YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)).

In normal use, iECO fixes the target temperature at 24 °C and fan speed to automatic; the unit then regulates compressor output more gently than in unrestricted Auto/Cool operation. Measurements made for this project found roughly 4 kWh **per day** additional consumption during continuous operation when iECO was inactive, without an apparent comfort or cooling advantage.

### The problem: iECO can be lost

iECO exits automatically after eight hours and returns to regular Auto mode. On the PortaSplit, iECO is activated in the MSmartHome / 美的美居 app rather than with a dedicated physical remote-control button. When the unit is later switched off and on manually, iECO may no longer be active. This project is designed to check and restore it instead of requiring a manual app visit after each power cycle.

### Why not use the app alone?

The app does not offer conditional automation such as “enable iECO only when the unit is already on,” nor a public documented API intended for cron jobs or Siri. This project uses `msmart-ng` and `midea-local` to communicate with the device locally and control when iECO is set.

## Included files

| File | Purpose |
|---|---|
| `midea_ieco_ensure.py` | Checks/sets power state and iECO for one or all configured devices |
| `midea_refresh_tokens.py` | Obtains fresh cloud-derived token/key pairs and updates `devices.json` |
| `devices.json` | Local configuration: name, IP, port, device ID, token, and key |

## Daily use

### Ensure iECO (power on if required)

```bash
venv/bin/python3 midea_ieco_ensure.py LivingRoom
venv/bin/python3 midea_ieco_ensure.py all
```

### Only restore iECO on running units

For unattended automation, use `--only-if-on`:

```bash
venv/bin/python3 midea_ieco_ensure.py all --only-if-on
```

This mode does not turn on an intentionally powered-off air conditioner. It skips it; for a running unit, it restores iECO if necessary.

### Refresh credentials

```bash
venv/bin/python3 midea_refresh_tokens.py --name LivingRoom
venv/bin/python3 midea_refresh_tokens.py --all
```

Refresh when the script reports an authentication/credential problem, after a significant Midea app/account change, or after reconnecting a device.

> **Why use `midea_refresh_tokens.py` rather than `msmart-ng discover --auto`?** Auto-discovery can use temporary credentials that change between runs. The refresh script uses your own Midea account through `midea-local` and saves the resulting device credentials in your protected local configuration.

## Cron automation

Edit your crontab with `crontab -e`:

```cron
# Every 20 minutes: restore iECO without powering on units
*/20 * * * * cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Every Sunday at 03:00: refresh credentials as a precaution
0 3 * * 0 cd /home/USER/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Rotate or truncate logs, for example:

```cron
0 0 1 * * truncate -s 0 /home/USER/midea-ieco/ieco.log
```

## Siri and iOS Shortcuts

The simplest solution without an additional web service is Shortcuts’ **Run Script over SSH** action.

1. Ensure the Linux host runs OpenSSH and is reachable on the LAN or through a VPN.
2. Prefer a dedicated SSH key for the iPhone over password authentication.
3. Create a Shortcut, add **Run Script over SSH**, then configure host, user, and key.
4. Use, for example:

   ```bash
   cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py LivingRoom
   ```

5. Give the shortcut a clear name, such as **Living room iECO**, and invoke it through Siri.

Using `venv/bin/python3` directly is more robust than relying on `source venv/bin/activate` in non-interactive SSH sessions. Use `all` instead of a device name for all units; append `--only-if-on` if Siri must never power on an off unit.

### HomeKit alternative

For native Apple Home switches, status display, scenes, and automations, Homebridge with `homebridge-cmd4` can map shell commands to accessories. It is more involved than SSH Shortcuts but provides full HomeKit integration.

## Troubleshooting notes

These are observations from developing this setup with `msmart-ng` in 2026, not a guarantee for future library versions.

| Symptom | Likely cause | Action |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | `midea-local` API changed | Inspect the installed API: `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` |
| `Device is not capable of property IECO` | Capabilities were not loaded correctly | Use a fresh `AC` object and query capabilities before setting iECO |
| `Failed to query capabilities` / timeout | Some devices do not answer the capability request while powered off | Follow the control script’s power-on/retry logic; check that device IP and credentials are current |
| `[Errno 104] Connection reset by peer` after retries | A prior failure left a bad socket state | Retry with a new `AC` object; refresh credentials if the problem persists |
| Token/key stops working | Credential invalidation or a preceding connection failure | Run `midea_refresh_tokens.py --name <device>` |

## Security

- `devices.json` contains device tokens/keys; keep it private and run `chmod 600 devices.json`.
- `midea_refresh_tokens.py` contains the Midea account password if you use `DEFAULT_PASSWORD`; run `chmod 600 midea_refresh_tokens.py`.
- Use SSH keys for Shortcuts and never expose SSH directly to the Internet via port forwarding. Use a VPN for remote use.
- Before contributing, remove real credentials from all files, diffs, logs, screenshots, and terminal history.

## License and sharing

You may share and adapt these scripts. They depend on the open-source libraries `msmart-ng` and `midea-local` and do not replace official Midea support.

---

> 🇩🇪 **Deutsche Dokumentation:** [README_german.md](README_german.md)
