# midea-ieco

> 🇩🇪 **Deutsch:** Die vollständige deutsche Anleitung findest du hier: [README_german.md](README_german.md)

Small, reliable command-line tools for local control of the **iECO mode** (and general power/status) of Midea air conditioners, including the Midea PortaSplit and compatible models from Comfee, Toshiba, Carrier, Klimaire, and others. They avoid relying on an unstable cloud connection during normal operation.

`msmart-ng` can control iECO directly over the local network. Midea Cloud credentials are only required to obtain valid device credentials initially and to refresh them when necessary.

## Why this project exists

### What iECO does

Midea advertises iECO as saving up to 60% compared with standard operation, with up to eight hours of operation using only 1.2 kWh ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)). A German ten-hour practical test using the PortaSplit measured about 100 W lower consumption in iECO than in Auto mode while maintaining a comfortable room temperature of 24.5–25.7 °C ([4-Happy-Home on YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)).

In normal use, iECO fixes the target temperature at 24 °C and the fan at automatic speed; the unit then regulates compressor output more gently than it does in unrestricted Auto/Cool operation. Measurements made for this project found approximately 4 kWh **per day** of additional consumption during continuous operation when iECO was not active, with no apparent comfort or cooling advantage.

### The problem: iECO disappears after manual use

iECO automatically exits after eight hours and returns to regular Auto mode. More importantly, iECO can currently be enabled **only through the MSmartHome / 美的美居 app**; there is no physical iECO button on the remote control.

If the air conditioner is subsequently switched off and on manually—at the unit or with the remote—iECO remains off. This is easy to miss because the unit otherwise appears to work normally. Instead of remembering to open the app and re-enable iECO after every manual power cycle, this project automates that task reliably in the background.

### Why not just use the Midea app?

The app does not provide conditional logic such as “enable iECO only if the unit is already on,” nor does it offer a public, documented API for third-party automation such as cron jobs or Siri. The libraries used here (`msmart-ng` and `midea-local`) communicate with the device on the local network, giving you control over when iECO is set without a cloud dependency during routine use.

## Included scripts

| File | Purpose |
|---|---|
| `midea_ieco_ensure.py` | Checks and sets power status and iECO for one or all configured devices |
| `midea_refresh_tokens.py` | Retrieves fresh token/key pairs from Midea Cloud and updates `devices.json` |
| `devices.json` | Central configuration: name, IP address, port, device ID, token, and key per device |

## Requirements

- Python 3.10 or later
- A Midea Cloud account (MSmartHome or 美的美居) in which the devices are already configured
- Network access from the controlling computer to the air conditioners on the local network (port 6444/TCP)

### Python packages

```bash
python3 -m venv venv
source venv/bin/activate
pip install msmart-ng midea-local
```

- **`msmart-ng`** performs the actual local device control, including the iECO attribute.
- **`midea-local`** is used only to log in to the cloud and obtain token/key pairs. `midea_refresh_tokens.py` invokes its `discover --debug` command as a subprocess.

> **Note on `msmart-ng discover`:** When used with `--auto`, or without your own account credentials, `msmart-ng discover` may return temporary session credentials that change between runs. Use `midea_refresh_tokens.py`, which uses `midea-local`, to obtain and store persistent token/key values in `devices.json`.

## One-time setup

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

### 3. Store credentials in `midea_refresh_tokens.py`

At the top of the file, set:

```python
DEFAULT_USERNAME = "your@account.example"
DEFAULT_PASSWORD = "yourPassword"
```

Restrict access afterwards because this file contains your cloud password in plain text:

```bash
chmod 600 midea_refresh_tokens.py
```

### 4. Retrieve token/key pairs

```bash
python3 midea_refresh_tokens.py --all
```

The script runs `python3 -m midealocal.cli discover --debug`, extracts the token and key from its output using a regular expression, and writes them back to `devices.json`. It also applies `chmod 600` to the configuration file.

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

In practice, credentials often remain valid for a long time. Refresh them when an app session changes fundamentally or when a device is reconnected.

## Cron automation

Edit your crontab with `crontab -e`:

```cron
# Every 20 minutes: re-enable iECO without turning units on
*/20 * * * * cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Every Sunday at 03:00: refresh credentials as a precaution
0 3 * * 0 cd /home/USER/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Remember log rotation, for example with `logrotate` or simply:

```cron
0 0 1 * * truncate -s 0 /home/USER/midea-ieco/ieco.log
```

## Siri and iOS Shortcuts

The simplest solution without additional server software is the native iOS Shortcuts action **Run Script over SSH**.

### Linux host requirements

- An OpenSSH server running and reachable on the local network or through a VPN
- A dedicated SSH key for the iPhone, recommended instead of password authentication

### Setup

1. In Shortcuts on the iPhone, create a new shortcut and add **Run Script over SSH**.
2. Enter the host, username, and authentication method (SSH key recommended).
3. Use a command such as:

   ```bash
   cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py LivingRoom
   ```

4. Name the shortcut, for example **Living room iECO**.
5. Call it with Siri, for example: *“Hey Siri, enable eco mode in the living room.”*

> **Tip for non-interactive SSH sessions:** Use `venv/bin/python3` directly rather than `source venv/bin/activate && python3`. It is more robust because non-interactive shells can handle `source` differently.

For all devices, use `all` in place of the device name. Add `--only-if-on` if Siri must not turn on an intentionally powered-off unit.

### Alternative: Homebridge and HomeKit

If you prefer regular switches, status display, scenes, and automations in Apple Home, use Homebridge with `homebridge-cmd4`. It can map arbitrary shell commands to on/off/status operations, such as `midea_ieco_ensure.py LivingRoom` for “on.” This is more work than the SSH Shortcuts option but provides full HomeKit integration.

## Development lessons learned

This table documents specific observations made while developing this setup with `msmart-ng` in 2026. It is a reference rather than universal troubleshooting guidance: internal APIs can change between versions. When in doubt, inspect the version actually installed:

```bash
python3 -c "import inspect; from msmart.device.AC.device import AirConditioner as AC; print(inspect.signature(AC.__init__))"
```

| Symptom | Cause observed during development | Solution |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | The `midea-local` API changed | Inspect the installed signature with `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` |
| `Device is not capable of property IECO` | Capabilities were not queried, or were queried with a damaged object | Query `get_capabilities()` before `refresh()`/`apply()` on a fresh `AC` object |
| Capability query times out / `Failed to query capabilities` although credentials are correct | The device only answers `get_capabilities()` while powered on | Turn it on first (`power_state = True` plus `apply()`), then query capabilities; `midea_ieco_ensure.py` follows this order |
| `[Errno 104] Connection reset by peer` after several attempts | A failed connection attempt left the `AC` object with a broken socket state | Create a **new** `AC` object for every retry |
| Token/key suddenly stop working | Usually the preceding socket issue, less often real credential invalidation | Run `midea_refresh_tokens.py --name <device>` |
| `msmart-ng discover` returns credentials that stop working shortly afterwards | `--auto` uses an internal helper account and its keys are temporary | Use `midea_refresh_tokens.py` via `midea-local` to obtain persistent credentials |

## Security notes

- `devices.json` contains sensitive token/key values: run `chmod 600 devices.json`.
- `midea_refresh_tokens.py` contains your cloud password in plain text: run `chmod 600 midea_refresh_tokens.py`.
- For Siri over SSH, use SSH-key authentication and do not expose SSH to the Internet using port forwarding. Use a VPN for remote access instead.

## License and sharing

You may freely share and adapt these scripts. They use the open libraries `msmart-ng` and `midea-local` and do not replace official Midea support.

---

> 🇩🇪 **Deutsche Anleitung:** [README_german.md](README_german.md)
