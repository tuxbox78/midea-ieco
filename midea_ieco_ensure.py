#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
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
    """Liest devices.json. Fehlt die Datei, ist sie unlesbar, kein gueltiges
    JSON oder hat sie nicht die erwartete Form ({"devices": [...]}), wird mit
    klarer Meldung abgebrochen statt mit einem rohen Traceback - relevant fuer
    den 20-Minuten-Cron-Lauf."""
    if not CONFIG_PATH.exists():
        print(f"Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
        sys.exit(1)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FEHLER: {CONFIG_PATH} konnte nicht gelesen werden "
              f"({type(exc).__name__}: {exc}).")
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        print(f"FEHLER: {CONFIG_PATH} hat nicht die erwartete Form "
              '{"devices": [...]}.')
        sys.exit(1)
    return data


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


async def connect_and_refresh(dev_conf: dict, retries: int = CONNECT_RETRIES,
                              with_capabilities: bool = False) -> AC:
    """Verbindet, authentifiziert und liest den Live-Status.

    Standardmaessig OHNE get_capabilities() - fuer eine reine Status-/Power-
    Abfrage (z.B. den --only-if-on-Schnellpfad) ist dieser zusaetzliche
    Netzwerk-Roundtrip unnoetig.

    Mit with_capabilities=True wird get_capabilities() VOR refresh() aufgerufen.
    Das ist zwingend, sobald der WAHRE ieco-Zustand gelesen werden soll: msmart-
    ng's refresh() pollt nur Properties aus _supported_properties, und die werden
    erst durch get_capabilities() befuellt. Ohne diesen Aufruf pollt refresh()
    die IECO-Property NICHT, und device.ieco liefert immer den Default False -
    selbst wenn iECO am Geraet aktiv ist (genau das liess die Verifikation frueher
    faelschlich fehlschlagen)."""
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
            if with_capabilities:
                await device.get_capabilities()
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

        # Fruehzeitiger Ausstieg VOR jeder teuren Capability-Abfrage:
        # Ist das Geraet aus und duerfen wir es per --only-if-on nicht
        # einschalten, ist alles gesagt. power_state kommt aus refresh() und ist
        # ohne get_capabilities() korrekt; der ieco-Zustand spielt hier keine
        # Rolle - also kein get_capabilities(), kein apply(), keine Netzwerklast.
        if only_if_on and not is_on:
            print(f"[{name}] --only-if-on aktiv und Geraet ist aus. "
                  f"Keine Aktion, keine weiteren Abfragen.")
            return True

        # Ab hier brauchen wir den ECHTEN ieco-Zustand (fuer die Statusanzeige,
        # den 'schon aktiv'-Kurzschluss und spaeter die Verifikation). refresh()
        # pollt die IECO-Property aber nur nach get_capabilities() (das
        # _supported_properties befuellt) - sonst liest device.ieco immer den
        # Default False. Also Capabilities abfragen und danach erneut refreshen.
        try:
            await device.get_capabilities()
            await device.refresh()
        except Exception as exc:
            print(f"[{name}] FEHLER bei get_capabilities()/refresh(): "
                  f"{type(exc).__name__}: {exc}")
            return False

        print(f"[{name}] Status vor Aktion: power={is_on}, "
              f"mode={device.operational_mode}, ieco={device.ieco}, eco={device.eco}")

        if not device.supports_ieco:
            print(f"[{name}] FEHLER: Geraet meldet keine iECO-Faehigkeit.")
            return False

        if is_on and device.ieco:
            print(f"[{name}] Bereits im gewuenschten Zustand (an, iECO aktiv). "
                  f"Keine weiteren Abfragen notwendig.")
            return True

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
                    # Fuer den naechsten Versuch eine FRISCHE Verbindung
                    # aufbauen (ein fehlgeschlagener Versuch kann das AC-Objekt
                    # mit defektem Socket-Zustand hinterlassen). Scheitert schon
                    # der Reconnect, ist ein weiterer apply()-Versuch auf dem
                    # toten Objekt zwecklos - das Retry-Budget steckt bereits in
                    # connect_and_refresh (drei interne Versuche). Darum hier
                    # sauber abbrechen, statt in die naechste Iteration auf einem
                    # geschlossenen Objekt zu laufen. Es wird jede Exception
                    # abgefangen (nicht nur RuntimeError), damit z.B. ein Timeout
                    # in get_capabilities() nicht den gesamten 'all'-Lauf mit
                    # einem Traceback beendet, sondern nur dieses eine Geraet.
                    try:
                        device = await connect_and_refresh(dev_conf)
                        await device.get_capabilities()
                        if was_off:
                            device.power_state = True
                        device.ieco = True
                    except Exception as exc2:
                        last_exc = exc2
                        print(f"  [{name}] Reconnect vor Wiederholung "
                              f"fehlgeschlagen ({type(exc2).__name__}): {exc2}")
                        break

        if not applied:
            print(f"[{name}] FEHLER beim Setzen: {last_exc}")
            return False

        await asyncio.sleep(2.0)
        await close_device(device)
        try:
            # with_capabilities=True ist hier zwingend: sonst pollt refresh() die
            # IECO-Property nicht und device.ieco laese faelschlich False - die
            # Ursache der frueher zu Unrecht als Fehlschlag gewerteten Verifikation.
            device = await connect_and_refresh(dev_conf, with_capabilities=True)
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
    elif not devices:
        # 'all' auf leerer Geraeteliste: all([]) waere True und wuerde
        # faelschlich "Gesamtergebnis: OK." (Exit 0) melden und damit eine
        # leere/kaputte devices.json maskieren. Bewusst klar abbrechen -
        # konsistent zum Schwestermodul midea_refresh_tokens.py, das bei leerem
        # --all ebenfalls mit Exit 1 endet -, damit eine Fehlkonfiguration auch
        # im stillen Cron-Lauf sichtbar wird.
        print("Keine Geraete in devices.json konfiguriert. Nichts zu tun.")
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
