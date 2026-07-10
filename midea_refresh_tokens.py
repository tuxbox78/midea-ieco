#!/usr/bin/env python3
"""
midea_refresh_tokens.py

Holt frische Token/Key-Paare aus der Midea-Cloud (ueber den bewaehrten
Befehl `python3 -m midealocal.cli discover --debug`) und aktualisiert
devices.json. Verifiziert JEDES gefundene Token/Key-Paar mit einer
echten lokalen Verbindung, bevor es gespeichert wird.

Sicherheitshinweis: Das Passwort wird als Kommandozeilenargument an den
midealocal-Unterprozess uebergeben. Auf einem Mehrbenutzer-System kann
das Passwort dadurch waehrend der Laufzeit des Unterprozesses ueber
`ps aux` oder /proc/<pid>/cmdline sichtbar sein. Nutze dieses Skript
daher nur auf vertrauenswuerdigen Einzelbenutzer-Servern.

Nutzung:
    python3 midea_refresh_tokens.py --name Wohnzimmer
    python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
    python3 midea_refresh_tokens.py --all
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

from msmart.device.AC.device import AirConditioner as AC

# ---------------------------------------------------------------------------
# CONFIG: Zugangsdaten hier hinterlegen, damit sie nicht bei jedem Aufruf
# erneut angegeben werden muessen. Datei danach unbedingt absichern:
#   chmod 600 midea_refresh_tokens.py
# ---------------------------------------------------------------------------
DEFAULT_USERNAME = "dein@account.example"
DEFAULT_PASSWORD = "HIER_PASSWORT_EINTRAGEN"
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "devices.json"
SUBPROCESS_TIMEOUT = 60
VERIFY_TIMEOUT = 10

# Matcht JEDEN tokenlist-Eintrag in der rohen JSON-Antwort der Cloud
# (Format mit doppelten Anfuehrungszeichen, z.B.
#  response: b'{"result": {"tokenlist": [{"udpId": "...", "key": "...", "token": "..."}]}...}')
TOKENLIST_RE = re.compile(
    r'"tokenlist"\s*:\s*\[.*?"key"\s*:\s*"([0-9a-fA-F]+)"\s*,\s*"token"\s*:\s*"([0-9a-fA-F]+)"'
)
APPLIANCE_ID_RE = re.compile(r"applianceCodes['\"]?\s*[:=]\s*['\"]?(\d+)")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"devices": []}


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    CONFIG_PATH.chmod(0o600)


def fetch_candidate_credentials(username: str, password: str, host: str) -> tuple[list[tuple[str, str]], str | None]:
    """Ruft discover --debug auf und gibt ALLE gefundenen (key, token)-
    Kandidaten zurueck, sowie die gemeldete Appliance-ID (falls vorhanden).
    Wirft RuntimeError bei einem klaren Ausfuehrungsfehler."""
    cmd = [
        sys.executable, "-m", "midealocal.cli", "discover",
        "--username", username, "--password", password,
        "--host", host, "--debug",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"discover-Befehl hat nach {SUBPROCESS_TIMEOUT}s nicht reagiert "
                            f"(Geraet unter {host} erreichbar?)") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("midealocal ist im aktuellen Python-Interpreter nicht installiert "
                            "(falsches venv aktiv?)") from exc

    combined_output = result.stdout + result.stderr

    if result.returncode != 0:
        tail = combined_output[-800:] if combined_output else "(keine Ausgabe)"
        raise RuntimeError(f"discover-Befehl endete mit Exit-Code {result.returncode}. "
                            f"Letzte Ausgabe: {tail}")

    matches = TOKENLIST_RE.findall(combined_output)
    if not matches:
        tail = combined_output[-800:] if combined_output else "(keine Ausgabe)"
        raise RuntimeError(f"Kein tokenlist-Eintrag in der Ausgabe gefunden. Letzte Ausgabe: {tail}")

    appliance_ids = APPLIANCE_ID_RE.findall(combined_output)
    appliance_id = appliance_ids[0] if appliance_ids else None

    return matches, appliance_id


async def verify_credentials(ip: str, port: int, device_id: int, key: str, token: str) -> bool:
    """Testet ein Token/Key-Paar mit einer echten, minimalen Verbindung,
    BEVOR es in devices.json gespeichert wird. Verhindert, dass ein
    falscher Kandidat (z.B. 'method 2' statt 'method 1') unbemerkt
    gespeichert wird."""
    device = AC(ip=ip, port=port, device_id=device_id)
    try:
        await asyncio.wait_for(device.authenticate(token, key), timeout=VERIFY_TIMEOUT)
        await asyncio.wait_for(device.refresh(), timeout=VERIFY_TIMEOUT)
        return True
    except Exception:
        return False
    finally:
        for method_name in ("close", "disconnect", "stop"):
            method = getattr(device, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass


def update_device(dev_conf: dict, username: str, password: str) -> bool:
    name = dev_conf.get("name", "unbekannt")
    host = dev_conf.get("ip")
    if not host:
        print(f"[{name}] FEHLER: Keine IP-Adresse in devices.json fuer dieses Geraet hinterlegt.")
        return False

    print(f"[{name}] Hole Token/Key-Kandidaten ueber {host} ...")
    try:
        candidates, appliance_id = fetch_candidate_credentials(username, password, host)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER beim Token-Abruf: {exc}")
        return False

    existing_id = str(dev_conf.get("id", "")).strip()
    if appliance_id is not None:
        if existing_id and existing_id != appliance_id:
            print(f"[{name}] WARNUNG: In devices.json steht id={existing_id}, "
                  f"aber die Cloud meldet id={appliance_id}. Bestehenden Wert NICHT ueberschrieben.")
        elif not existing_id:
            dev_conf["id"] = int(appliance_id)

    device_id_str = str(dev_conf.get("id", "")).strip()
    if not device_id_str:
        print(f"[{name}] FEHLER: Keine Geraete-ID verfuegbar (weder in devices.json noch von der "
              f"Cloud gemeldet). Eintrag wird NICHT gespeichert.")
        return False
    device_id = int(device_id_str)
    port = int(dev_conf.get("port", 6444))

    print(f"[{name}] {len(candidates)} Kandidat(en) gefunden, verifiziere der Reihe nach ...")
    for idx, (key, token) in enumerate(candidates, start=1):
        ok = asyncio.run(verify_credentials(host, port, device_id, key, token))
        if ok:
            dev_conf["token"] = token
            dev_conf["key"] = key
            dev_conf.setdefault("port", 6444)
            print(f"[{name}] Kandidat {idx}/{len(candidates)} erfolgreich verifiziert und gespeichert.")
            return True
        print(f"[{name}] Kandidat {idx}/{len(candidates)} lieferte keine gueltige Verbindung, "
              f"versuche naechsten ...")

    print(f"[{name}] FEHLER: Keiner der {len(candidates)} Kandidaten liess sich verbinden. "
          f"Bestehende Werte in devices.json bleiben unveraendert.")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holt frische Midea Token/Key-Paare per discover --debug, "
                    "verifiziert sie, und aktualisiert devices.json."
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Midea-Account (Default aus CONFIG)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Midea-Passwort (Default aus CONFIG)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Alle Geraete aus devices.json aktualisieren")
    group.add_argument("--name", help="Name des Geraets (neu oder bestehend)")
    parser.add_argument("--host", help="IP-Adresse (nur zusammen mit --name fuer NEUE Geraete)")
    args = parser.parse_args()

    if not args.username or not args.password or args.password == "HIER_PASSWORT_EINTRAGEN":
        print("FEHLER: Bitte DEFAULT_USERNAME/DEFAULT_PASSWORD im Skript-Kopf setzen, "
              "oder --username/--password beim Aufruf angeben.")
        sys.exit(1)

    config = load_config()
    devices = config.setdefault("devices", [])

    if args.all:
        if not devices:
            print("devices.json enthaelt noch keine Geraete. Nutze --name/--host fuer ein neues Geraet.")
            sys.exit(1)
        targets = devices
        new_entry = None
    else:
        targets = [d for d in devices if d.get("name") == args.name]
        new_entry = None
        if not targets:
            if not args.host:
                print(f"Geraet '{args.name}' ist neu. Bitte zusaetzlich --host angeben.")
                sys.exit(1)
            new_entry = {"name": args.name, "ip": args.host, "port": 6444, "id": "", "token": "", "key": ""}
            targets = [new_entry]

    ok = True
    successful_new_entry = False
    for dev in targets:
        success = update_device(dev, args.username, args.password)
        ok = ok and success
        if dev is new_entry and success:
            successful_new_entry = True

    # Ein neues Geraet nur dann dauerhaft speichern, wenn der Abruf
    # tatsaechlich erfolgreich UND verifiziert war. Verhindert kaputte
    # Platzhalter-Eintraege in devices.json nach einem fehlgeschlagenen Versuch.
    if new_entry is not None and successful_new_entry:
        devices.append(new_entry)
    elif new_entry is not None and not successful_new_entry:
        print(f"Neues Geraet '{args.name}' wurde NICHT gespeichert, da der Abruf fehlgeschlagen ist.")

    if new_entry is None or successful_new_entry:
        save_config(config)
        print(f"devices.json aktualisiert: {CONFIG_PATH}")

    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
