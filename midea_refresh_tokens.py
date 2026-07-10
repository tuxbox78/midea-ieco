#!/usr/bin/env python3
"""
midea_refresh_tokens.py

Holt frische Token/Key-Paare, indem der bewaehrte Befehl
    python3 -m midealocal.cli discover --username ... --password ... --host ... --debug
als Subprozess aufgerufen und dessen Debug-Ausgabe nach dem tokenlist-Eintrag
durchsucht wird. Aktualisiert damit automatisch devices.json.

Nutzung Beispiele:
    # Bestehendes Geraet aktualisieren (Zugangsdaten kommen aus CONFIG unten)
    python3 midea_refresh_tokens.py --name Midea1

    # Neues Geraet hinzufuegen (IP zusaetzlich angeben, Geraete-ID wird automatisch
    # aus der Cloud-Antwort ermittelt)
    python3 midea_refresh_tokens.py --name Midea2 --host 192.168.0.185

    # Alle Geraete aus devices.json auf einmal aktualisieren
    python3 midea_refresh_tokens.py --all

    # Zugangsdaten optional auch per CLI ueberschreiben (statt CONFIG unten)
    python3 midea_refresh_tokens.py --name Midea1 --username "..." --password "..."
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG: Hier feste Zugangsdaten hinterlegen, damit sie nicht bei jedem
# Aufruf erneut als Parameter angegeben werden muessen.
# Sicherheitshinweis: Diese Datei sollte nicht world-readable sein
# (z.B. "chmod 600 midea_refresh_tokens.py").
# ---------------------------------------------------------------------------
DEFAULT_USERNAME = "USERNAME_EMAIL_SMARTHOMEAPP"
DEFAULT_PASSWORD = "PASSWORD_SMARTHOMEAPP"
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "devices.json"
SUBPROCESS_TIMEOUT = 60

# Matcht die rohe JSON-Antwort der Cloud (doppelte Anfuehrungszeichen), z.B.:
# response: b'{"result":{"tokenlist":[{"udpId":"...","key":"AA..","token":"BB.."}]},...}'
TOKENLIST_RE = re.compile(
    r'"tokenlist":\[\{"udpId":"[^"]*","key":"([0-9a-fA-F]+)","token":"([0-9a-fA-F]+)"\}\]'
)

# Matcht die Geraete-ID aus der Request-Payload (Python-dict-Repr mit
# einfachen Anfuehrungszeichen), z.B.: 'applianceCodes': '153931629346858'
APPLIANCE_ID_RE = re.compile(r"'applianceCodes':\s*'(\d+)'")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"devices": []}


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    # devices.json enthaelt sensible Tokens -> Zugriff einschraenken
    CONFIG_PATH.chmod(0o600)


def fetch_token_key(username: str, password: str, host: str):
    """
    Ruft den discover --debug Befehl auf und extrahiert Token, Key und
    (falls vorhanden) die vom Geraet gemeldete Appliance-ID.
    Gibt (token, key, appliance_id) zurueck, appliance_id kann None sein.
    """
    cmd = [
        sys.executable, "-m", "midealocal.cli", "discover",
        "--username", username,
        "--password", password,
        "--host", host,
        "--debug",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"discover-Befehl hat nach {SUBPROCESS_TIMEOUT}s nicht reagiert "
            f"(Geraet unter {host} erreichbar?)"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "midealocal ist im aktuellen Python-Interpreter nicht installiert "
            "(falsches venv aktiv?)"
        ) from exc

    combined_output = result.stdout + result.stderr

    matches = TOKENLIST_RE.findall(combined_output)
    if not matches:
        tail = combined_output[-800:] if combined_output else "(keine Ausgabe)"
        raise RuntimeError(
            f"Kein tokenlist-Eintrag gefunden. Exit-Code: {result.returncode}. "
            f"Letzte Ausgabe:\n{tail}"
        )

    if len(matches) > 1:
        print(f"  Hinweis: {len(matches)} tokenlist-Eintraege gefunden, "
              f"verwende den ersten (method 1).")

    key, token = matches[0]

    appliance_ids = APPLIANCE_ID_RE.findall(combined_output)
    appliance_id = appliance_ids[0] if appliance_ids else None

    return token, key, appliance_id


def update_device(dev_conf: dict, username: str, password: str) -> bool:
    name = dev_conf.get("name", "unbekannt")
    host = dev_conf.get("ip")
    if not host:
        print(f"[{name}] FEHLER: Keine IP-Adresse in devices.json fuer dieses Geraet hinterlegt.")
        return False

    print(f"[{name}] Hole frischen Token/Key ueber {host} ...")
    try:
        token, key, appliance_id = fetch_token_key(username, password, host)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER beim Token-Abruf: {exc}")
        return False

    # Konsistenz-Check: passt die von der Cloud gemeldete Geraete-ID zu devices.json?
    if appliance_id is not None:
        existing_id = str(dev_conf.get("id", "")).strip()
        if existing_id and existing_id != appliance_id:
            print(f"[{name}] WARNUNG: In devices.json steht id={existing_id}, "
                  f"aber die Cloud meldet id={appliance_id}. Bestehenden Wert NICHT ueberschrieben.")
        elif not existing_id:
            dev_conf["id"] = int(appliance_id)
            print(f"[{name}] Geraete-ID automatisch ermittelt: {appliance_id}")

    dev_conf["token"] = token
    dev_conf["key"] = key
    dev_conf.setdefault("port", 6444)
    print(f"[{name}] Neuer Token/Key erfolgreich gespeichert.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holt frische Midea Token/Key-Paare per discover --debug und aktualisiert devices.json."
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME,
                         help=f"Midea Account (Default aus CONFIG: {DEFAULT_USERNAME})")
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                         help="Midea Account Passwort (Default aus CONFIG oben im Skript)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Alle Geraete aus devices.json aktualisieren")
    group.add_argument("--name", help="Name des Geraets")

    parser.add_argument("--host", help="IP-Adresse (nur fuer NEUE Geraete zusammen mit --name)")

    args = parser.parse_args()

    if not args.username or not args.password or args.password == "HIER_PASSWORT_EINTRAGEN":
        print("FEHLER: Bitte DEFAULT_USERNAME/DEFAULT_PASSWORD im Skript-Kopf setzen, "
              "oder --username/--password beim Aufruf angeben.")
        sys.exit(1)

    config = load_config()
    devices = config.setdefault("devices", [])

    if args.all:
        if not devices:
            print("devices.json enthaelt noch keine Geraete. Nutze --name + --host fuer ein neues Geraet.")
            sys.exit(1)
        targets = devices
    else:
        targets = [d for d in devices if d["name"] == args.name]
        if not targets:
            if not args.host:
                print(f"Geraet '{args.name}' ist neu. Bitte zusaetzlich --host angeben.")
                sys.exit(1)
            new_dev = {
                "name": args.name,
                "ip": args.host,
                "port": 6444,
                "id": "",
                "token": "",
                "key": "",
            }
            devices.append(new_dev)
            targets = [new_dev]

    ok = True
    for dev in targets:
        success = update_device(dev, args.username, args.password)
        ok = ok and success

    save_config(config)
    print(f"devices.json aktualisiert: {CONFIG_PATH}")

    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
