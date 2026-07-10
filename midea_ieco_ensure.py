#!/usr/bin/env python3
"""
midea_ieco_ensure.py (msmart-ng Edition, finale Version 5 - mit --only-if-on)

Stellt sicher, dass iECO aktiv ist.

Standardverhalten: Schaltet das Geraet bei Bedarf ein UND aktiviert iECO.
Mit --only-if-on: Schaltet NICHTS ein. Prueft nur den Status; ist das Geraet
bereits an, wird iECO bei Bedarf nachgezogen. Ist es aus, wird nichts getan.

Nutzung:
    python3 midea_ieco_ensure.py <device_name|all>
    python3 midea_ieco_ensure.py <device_name|all> --only-if-on
"""

import argparse
import asyncio
import json
import sys
import logging
from pathlib import Path

from msmart.device.AC.device import AirConditioner as AC

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH = Path(__file__).parent / "devices.json"
CONNECT_RETRIES = 3
RETRY_DELAY = 3.0
ACTION_RETRIES = 3


async def build_and_authenticate(dev_conf: dict, retries=CONNECT_RETRIES):
    name = dev_conf["name"]
    last_exc = None
    for attempt in range(1, retries + 1):
        device = AC(
            ip=dev_conf["ip"],
            port=int(dev_conf.get("port", 6444)),
            device_id=int(dev_conf["id"]),
        )
        try:
            await device.authenticate(dev_conf["token"], dev_conf["key"])
            await device.get_capabilities()
            await device.refresh()
            return device
        except Exception as exc:
            last_exc = exc
            print(f"  [{name}] Verbindungsversuch {attempt}/{retries} fehlgeschlagen: {exc}")
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"Verbindung zu {name} fehlgeschlagen nach {retries} Versuchen") from last_exc


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


async def ensure_ieco(dev_conf: dict, only_if_on: bool, target_temp=None) -> bool:
    name = dev_conf["name"]

    try:
        device = await build_and_authenticate(dev_conf)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER: {exc}")
        return False

    if not device.online:
        print(f"[{name}] FEHLER: Geraet meldet sich nicht als online.")
        return False

    is_on = device.power_state
    print(f"[{name}] Status vor Aktion: power={is_on}, "
          f"mode={device.operational_mode}, ieco={device.ieco}, eco={device.eco}")

    if only_if_on and not is_on:
        print(f"[{name}] --only-if-on aktiv und Geraet ist aus. Keine Aktion, kein Einschalten.")
        return True

    changed = False
    was_off = not is_on
    if was_off:
        device.power_state = True
        if target_temp is not None:
            device.target_temperature = target_temp
        changed = True

    if not device.ieco:
        device.ieco = True
        changed = True

    if not changed:
        print(f"[{name}] Bereits im gewuenschten Zustand, keine Aenderung notwendig.")
        print(f"[{name}] OK: iECO ist aktiv (vom Geraet bestaetigt).")
        return True

    applied = False
    last_exc = None
    for attempt in range(1, ACTION_RETRIES + 1):
        try:
            await device.apply()
            applied = True
            break
        except Exception as exc:
            last_exc = exc
            print(f"  [{name}] apply()-Versuch {attempt}/{ACTION_RETRIES} fehlgeschlagen: {exc}")
            if attempt < ACTION_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
                try:
                    device = await build_and_authenticate(dev_conf)
                    if was_off and not only_if_on:
                        device.power_state = True
                        if target_temp is not None:
                            device.target_temperature = target_temp
                    device.ieco = True
                except RuntimeError as exc2:
                    last_exc = exc2

    if not applied:
        print(f"[{name}] FEHLER beim Setzen: {last_exc}")
        return False

    await asyncio.sleep(2.0)

    try:
        device = await build_and_authenticate(dev_conf)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER bei Verifikation: {exc}")
        return False

    print(f"[{name}] Status nach Aktion: power={device.power_state}, "
          f"mode={device.operational_mode}, ieco={device.ieco}, eco={device.eco}")

    if not device.ieco:
        print(f"[{name}] FEHLER: iECO ist laut Geraet weiterhin deaktiviert!")
        return False

    print(f"[{name}] OK: iECO ist aktiv (vom Geraet bestaetigt).")
    return True


async def main():
    parser = argparse.ArgumentParser(
        description="Stellt sicher, dass iECO auf Midea-Klimaanlagen aktiv ist."
    )
    parser.add_argument("target", help="Geraetename aus devices.json, oder 'all'")
    parser.add_argument(
        "--only-if-on",
        action="store_true",
        help="Geraet NICHT einschalten. Nur pruefen und iECO nachziehen, falls es bereits laeuft.",
    )
    args = parser.parse_args()

    config = load_config()
    devices = config["devices"]

    if args.target != "all":
        devices = [d for d in devices if d["name"] == args.target]
        if not devices:
            print(f"Geraet '{args.target}' nicht in devices.json gefunden.")
            sys.exit(1)

    results = []
    for d in devices:
        results.append(await ensure_ieco(d, only_if_on=args.only_if_on))
        await asyncio.sleep(1.0)

    if all(results):
        print("Gesamtergebnis: OK.")
        sys.exit(0)
    else:
        print("Gesamtergebnis: FEHLER - mindestens ein Geraet hat ein Problem.")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
