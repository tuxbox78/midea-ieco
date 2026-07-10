#!/usr/bin/env python3
"""
midea_ieco_ensure.py

Stellt sicher, dass iECO auf einer oder mehreren Midea-Klimaanlagen aktiv ist.

Standardverhalten: Schaltet das Geraet bei Bedarf ein UND aktiviert iECO.
Mit --only-if-on: Schaltet NICHTS ein. Nur wenn das Geraet bereits laeuft,
wird iECO bei Bedarf nachgezogen. Ist es aus, wird sofort abgebrochen,
OHNE zusaetzliche Netzwerkabfragen (kein get_capabilities()-Aufruf).

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
ACTION_RETRIES = 3
RETRY_DELAY = 3.0


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


async def close_device(device: AC) -> None:
    for method_name in ("close", "disconnect", "stop"):
        method = getattr(device, method_name, None)
        if method is None:
            continue
        try:
            result = method()
            if asyncio.iscoroutine(result):
                await result
            return
        except Exception:
            pass


async def connect_and_refresh(dev_conf: dict, retries: int = CONNECT_RETRIES) -> AC:
    """Verbindet, authentifiziert und liest NUR den Live-Status.
    Ruft absichtlich KEIN get_capabilities() auf - das ist ein
    zusaetzlicher Netzwerk-Roundtrip, der nur benoetigt wird, wenn wir
    tatsaechlich vorhaben, eine capability-gebundene Property (ieco) zu
    setzen. Fuer eine reine Status-/Power-Abfrage ist er unnoetig."""
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
            await device.refresh()
            return device
        except Exception as exc:
            last_exc = exc
            await close_device(device)
            print(f"  [{name}] Verbindungsversuch {attempt}/{retries} "
                  f"fehlgeschlagen ({type(exc).__name__}): {exc}")
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"Verbindung zu {name} fehlgeschlagen nach {retries} Versuchen") from last_exc


async def ensure_ieco(dev_conf: dict, only_if_on: bool) -> bool:
    name = dev_conf["name"]

    try:
        device = await connect_and_refresh(dev_conf)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER: {exc}")
        return False

    try:
        if not device.online:
            print(f"[{name}] FEHLER: Geraet meldet sich nicht als online.")
            return False

        is_on = device.power_state
        print(f"[{name}] Status vor Aktion: power={is_on}, "
              f"mode={device.operational_mode}, ieco={device.ieco}, eco={device.eco}")

        # Fruehzeitiger Ausstieg VOR jeder teuren Capability-Abfrage:
        # Wenn das Geraet aus ist und wir es per --only-if-on nicht
        # einschalten duerfen, ist hier bereits alles gesagt - kein
        # get_capabilities(), kein apply(), keine weitere Netzwerklast.
        if only_if_on and not is_on:
            print(f"[{name}] --only-if-on aktiv und Geraet ist aus. "
                  f"Keine Aktion, keine weiteren Abfragen.")
            return True

        if is_on and device.ieco:
            print(f"[{name}] Bereits im gewuenschten Zustand (an, iECO aktiv). "
                  f"Keine weiteren Abfragen notwendig.")
            return True

        # Ab hier wird tatsaechlich etwas geaendert (Einschalten und/oder
        # iECO setzen) - erst jetzt lohnt sich der zusaetzliche
        # get_capabilities()-Roundtrip, der laut msmart-ng-Dokumentation
        # vor dem Setzen capability-gebundener Properties nötig ist.
        try:
            await device.get_capabilities()
        except Exception as exc:
            print(f"[{name}] FEHLER bei get_capabilities(): "
                  f"{type(exc).__name__}: {exc}")
            return False

        if not device.supports_ieco:
            print(f"[{name}] FEHLER: Geraet meldet keine iECO-Faehigkeit.")
            return False

        was_off = not is_on
        if was_off:
            device.power_state = True
        if not device.ieco:
            device.ieco = True

        applied = False
        last_exc = None
        for attempt in range(1, ACTION_RETRIES + 1):
            try:
                await device.apply()
                applied = True
                break
            except Exception as exc:
                last_exc = exc
                print(f"  [{name}] apply()-Versuch {attempt}/{ACTION_RETRIES} "
                      f"fehlgeschlagen ({type(exc).__name__}): {exc}")
                if attempt < ACTION_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
                    await close_device(device)
                    try:
                        device = await connect_and_refresh(dev_conf)
                        await device.get_capabilities()
                        if was_off:
                            device.power_state = True
                        device.ieco = True
                    except RuntimeError as exc2:
                        last_exc = exc2

        if not applied:
            print(f"[{name}] FEHLER beim Setzen: {last_exc}")
            return False

        await asyncio.sleep(2.0)
        await close_device(device)
        try:
            device = await connect_and_refresh(dev_conf)
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

    finally:
        await close_device(device)


async def main() -> None:
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
