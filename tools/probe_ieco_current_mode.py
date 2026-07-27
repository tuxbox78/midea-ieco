#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
probe_ieco_current_mode.py

Schonende Messung: prueft, ob das Geraet iECO im AKTUELL eingestellten
Betriebsmodus annimmt - und wechselt den Modus dabei NICHT selbst.

Gedacht fuer diesen Ablauf:
    1. Modus an der Anlage per FERNBEDIENUNG einstellen (IR belastet die
       LAN-Schnittstelle ueberhaupt nicht) und die Anlage einschalten.
    2. Dieses Skript aufrufen - es misst nur.
    3. Ergebnis notieren, naechsten Modus einstellen, wieder aufrufen.

Warum getrennt von probe_ieco_modes.py: Jener wechselt die Modi selbst ueber
das Netz und braucht dafuer rund drei Verbindungen pro Modus. Midea-Geraete
akzeptieren nur EINE lokale Verbindung und reagieren empfindlich auf schnelle
Sitzungsfolge - in der Praxis kann die Firmware dabei in eine voruebergehende
Blockade laufen ("Unsupported packet", danach "No response from host"). Dieses
Skript kommt mit ZWEI Verbindungen aus (setzen, verifizieren) - im guenstigsten
Fall sogar mit einer -, und laesst dem Geraet zwischen den Messungen genau so
viel Ruhe, wie du zum Umschalten der Fernbedienung brauchst.

Ein Restore ist bewusst nicht noetig: Sobald du den Modus per Fernbedienung
wechselst, faellt iECO ohnehin weg - genau das ist ja der Effekt, um den es in
diesem Projekt geht. Mit --restore laesst sich iECO nach der Messung trotzdem
wieder ausschalten (kostet eine dritte Verbindung).

devices.json wird ausschliesslich GELESEN.

Nutzung:
    python3 tools/probe_ieco_current_mode.py <Geraetename>
    python3 tools/probe_ieco_current_mode.py <Geraetename> --dry-run
    python3 tools/probe_ieco_current_mode.py <Geraetename> --restore
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msmart.device.AC.device import AirConditioner as AC

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "devices.json"

# Bewusst zurueckhaltend: lieber ein sauberer Abbruch mit klarer Meldung als
# ein Versuchssturm auf ein Geraet, das gerade ohnehin nicht mag.
CONNECT_RETRIES = 2
RETRY_DELAY = 8.0
DEFAULT_SETTLE = 12.0


def load_device(name: str) -> dict:
    """Liest devices.json (nur lesend) und liefert den Eintrag zu ``name``."""
    if not CONFIG_PATH.exists():
        sys.exit(f"FEHLER: Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError ab
        sys.exit(f"FEHLER: {CONFIG_PATH} konnte nicht gelesen werden "
                 f"({type(exc).__name__}: {exc}).")
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        sys.exit(f'FEHLER: {CONFIG_PATH} hat nicht die erwartete Form {{"devices": [...]}}.')

    for d in data["devices"]:
        if isinstance(d, dict) and d.get("name") == name:
            for key in ("ip", "id", "token", "key"):
                if not d.get(key):
                    sys.exit(f"FEHLER: Geraet '{name}' hat kein gefuelltes Feld '{key}'.")
            return d

    known = [d.get("name") for d in data["devices"]
             if isinstance(d, dict) and isinstance(d.get("name"), str)]
    sys.exit(f"FEHLER: Geraet '{name}' nicht in {CONFIG_PATH.name} gefunden. "
             f"Bekannt: {', '.join(known) if known else '(keine)'}")


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


async def connect(dev_conf: dict) -> AC:
    """Baut eine frische, authentifizierte Verbindung auf und liest den Status.

    get_capabilities() laeuft IMMER vor refresh(): msmart-ng's refresh() pollt
    nur Properties aus _supported_properties, die erst get_capabilities()
    befuellt. Ohne diesen Aufruf liefert device.ieco konstant den Default False
    und jede Messung waere wertlos."""
    from msmart.device.AC.device import AirConditioner as AC

    last_exc = None
    for attempt in range(1, CONNECT_RETRIES + 1):
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
            await close_device(device)
            print(f"  Verbindungsversuch {attempt}/{CONNECT_RETRIES} fehlgeschlagen "
                  f"({type(exc).__name__}: {exc})")
            if attempt < CONNECT_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"Verbindung fehlgeschlagen nach {CONNECT_RETRIES} Versuchen") from last_exc


def mode_label(device: AC) -> str:
    mode = device.operational_mode
    return f"{getattr(mode, 'name', mode)} ({int(mode)})"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prueft, ob iECO im aktuell eingestellten Betriebsmodus haelt. "
                    "Wechselt den Modus NICHT.")
    parser.add_argument("device", help="Geraetename aus devices.json")
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                        help=f"Wartezeit in Sekunden zwischen Setzen und Zurueklesen "
                             f"(Standard: {DEFAULT_SETTLE:g}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur den aktuellen Zustand anzeigen, nichts setzen.")
    parser.add_argument("--restore", action="store_true",
                        help="iECO nach der Messung wieder ausschalten, wenn es "
                             "vorher aus war (kostet eine weitere Verbindung).")
    args = parser.parse_args()

    if args.settle < 0:
        sys.exit("FEHLER: --settle darf nicht negativ sein.")

    dev_conf = load_device(args.device)

    try:
        import msmart  # noqa: F401  (reine Verfuegbarkeitspruefung)
    except ImportError:
        sys.exit("FEHLER: msmart-ng ist im aktiven Python-Interpreter nicht installiert. "
                 "Aufruf z.B.: venv/bin/python3 tools/probe_ieco_current_mode.py ...")

    # --- Verbindung 1: Status lesen (und ggf. iECO setzen) ------------------
    print(f"Verbinde mit '{args.device}' ({dev_conf['ip']}) ...")
    try:
        device = await connect(dev_conf)
    except RuntimeError as exc:
        sys.exit(f"FEHLER: {exc}\n"
                 f"  Falls die Anlage kurz zuvor viele Verbindungen gesehen hat, "
                 f"bitte 1-2 Minuten warten und erneut versuchen.")

    try:
        if not device.online:
            sys.exit("FEHLER: Geraet meldet sich nicht als online.")
        if not device.supports_ieco:
            sys.exit("FEHLER: Geraet meldet keine iECO-Faehigkeit - eine Messung "
                     "ist damit gegenstandslos.")

        label = mode_label(device)
        print(f"Aktueller Zustand: power={device.power_state}, mode={label}, "
              f"target={device.target_temperature}, ieco={device.ieco}")

        if not device.power_state:
            sys.exit("\nDas Geraet ist AUS. Bitte per Fernbedienung einschalten und "
                     "den gewuenschten Modus einstellen, dann erneut aufrufen.\n"
                     "(Es wurde nichts veraendert.)")

        # iECO ist bereits aktiv: das IST bereits die Antwort fuer diesen Modus.
        # Wenn der Modus iECO nicht traegt, kann iECO darin nicht aktiv sein -
        # also ohne jeden weiteren Schreibvorgang oder Roundtrip fertig.
        if device.ieco is True:
            print("")
            print(f"ERGEBNIS: {label} -> iECO AKTIV")
            print("  (war bereits aktiv - dieser Modus traegt iECO; nichts veraendert)")
            return

        if args.dry_run:
            print("")
            print("--dry-run: iECO waere jetzt gesetzt worden. Es wurde NICHTS veraendert.")
            return

        print("")
        print(f"Setze iECO in Modus {label} ...")
        device.ieco = True
        await device.apply()
    finally:
        await close_device(device)

    # Firmware Zeit geben, den Wechsel zu uebernehmen, bevor zurueckgelesen wird.
    await asyncio.sleep(args.settle)

    # --- Verbindung 2: echten Zustand zurueklesen --------------------------
    try:
        device = await connect(dev_conf)
    except RuntimeError as exc:
        sys.exit(f"FEHLER bei der Verifikation: {exc}\n"
                 f"  Das Ergebnis fuer diesen Modus ist damit NICHT aussagekraeftig. "
                 f"Bitte nach einer kurzen Pause wiederholen.")

    try:
        confirmed = device.ieco is True
        label_after = mode_label(device)
        print(f"Zustand nach dem Setzen: power={device.power_state}, "
              f"mode={label_after}, ieco={device.ieco}")

        print("")
        if label_after != label:
            print(f"ERGEBNIS: NICHT AUSSAGEKRAEFTIG - der Modus wechselte waehrend der "
                  f"Messung ({label} -> {label_after}).")
            print("  Bitte den Modus stabil halten und erneut messen.")
        elif confirmed:
            print(f"ERGEBNIS: {label} -> iECO AKTIV")
            print("  Dieser Modus traegt iECO.")
        else:
            print(f"ERGEBNIS: {label} -> iECO IGNORIERT")
            print("  Die Anlage hat den Befehl angenommen, aber iECO nicht aktiviert - "
                  "dieser Modus traegt iECO nicht.")

        if args.restore and confirmed:
            print("")
            print("--restore: schalte iECO wieder aus ...")
            device.ieco = False
            await device.apply()
            print("  iECO wieder ausgeschaltet.")
    finally:
        await close_device(device)

    print("")
    print("Naechster Modus: per Fernbedienung umstellen, dann dieses Skript erneut "
          "aufrufen.")


if __name__ == "__main__":
    asyncio.run(main())
