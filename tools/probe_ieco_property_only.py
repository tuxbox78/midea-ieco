#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
probe_ieco_property_only.py

On-Device-Validierung fuer midea_apply.apply_ieco_only: nimmt ein Geraet iECO aus
einem REINEN Property-Write (SetPropertiesCommand, OHNE vorangehenden
Zustandsbefehl) an - und bleiben power/mode/target dabei nachweislich unberuehrt?

Das ist die Frage, die Unit- und Vertragstests NICHT beantworten koennen: sie
belegen, dass der Aufruf gegen die Bibliothek gueltig ist, nicht, dass eine reale
Firmware ihn UEBERNIMMT. Erst wenn diese Probe an echter Hardware gruen ist, darf
apply_ieco_only in ensure_ieco verdrahtet werden (Plan Phase 5).

WAS ES TUT
    1. Verbindung 1: Status lesen (power/mode/target/ieco). Nur wenn die Anlage
       AN ist, in COOL/HEAT laeuft und iECO kann, wird ueberhaupt geschrieben.
    2. iECO auf das GEGENTEIL des Ist-Werts setzen - ausschliesslich ueber
       apply_ieco_only (property-only). So beweist die Messung eine echte
       AENDERUNG, nicht nur eine folgenlose Wiederholung.
    3. Verbindung 2: zuruecklesen. Zusicherung: iECO ist umgesprungen UND
       power/mode/target sind unveraendert (die eigentliche Clobber-Freiheit).
       Danach den urspruenglichen iECO-Wert wiederherstellen (wieder property-only).
    4. Verbindung 3: zuruecklesen. Zusicherung: iECO ist wieder wie am Anfang,
       power/mode/target weiterhin unveraendert.

Midea-Geraete vertragen nur EINE lokale Verbindung und reagieren empfindlich auf
schnelle Sitzungsfolge. Zwischen den drei Verbindungen liegt deshalb eine
grosszuegige Ruhepause (--settle). Schlaegt eine spaetere Verbindung fehl, meldet
die Probe ehrlich, in welchem Zustand das Geraet dann steht.

devices.json wird AUSSCHLIESSLICH GELESEN. Token/Key werden nie ausgegeben.

Nutzung:
    python3 tools/probe_ieco_property_only.py <Geraetename>
    python3 tools/probe_ieco_property_only.py <Geraetename> --dry-run
    python3 tools/probe_ieco_property_only.py <Geraetename> --config /pfad/devices.json
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

# tools/ liegt unter dem Repo-Wurzelverzeichnis, die gemeinsamen Module dort.
# Ohne diesen Eintrag scheitert der Import beim direkten Aufruf.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from midea_apply import apply_ieco_only  # noqa: E402
from midea_conn import close_connection  # noqa: E402

logging.basicConfig(level=logging.WARNING)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "devices.json"

# Zurueckhaltend: lieber ein sauberer Abbruch als ein Versuchssturm auf ein
# Geraet, das gerade ohnehin keine Verbindung mag.
CONNECT_RETRIES = 2
RETRY_DELAY = 8.0
DEFAULT_SETTLE = 15.0

# iECO haelt nur in COOL(2)/HEAT(4) - in anderen Modi nimmt das Geraet den Befehl
# an, verwirft ihn aber still (siehe midea_ieco_ensure.IECO_CAPABLE_MODE_VALUES).
# Eine Messung dort waere gegenstandslos.
IECO_CAPABLE_MODE_VALUES = frozenset({2, 4})


def load_device(config_path: Path, name: str) -> dict:
    """Liest devices.json (NUR lesend) und liefert den Eintrag zu ``name``."""
    if not config_path.exists():
        sys.exit(f"FEHLER: Konfigurationsdatei nicht gefunden: {config_path}")
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        sys.exit(f"FEHLER: {config_path} konnte nicht gelesen werden "
                 f"({type(exc).__name__}: {exc}).")
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        sys.exit(f'FEHLER: {config_path} hat nicht die erwartete Form '
                 f'{{"devices": [...]}}.')
    for d in data["devices"]:
        if isinstance(d, dict) and d.get("name") == name:
            for key in ("ip", "id", "token", "key"):
                if not d.get(key):
                    sys.exit(f"FEHLER: Geraet '{name}' hat kein gefuelltes "
                             f"Feld '{key}'.")
            return d
    known = [d.get("name") for d in data["devices"]
             if isinstance(d, dict) and isinstance(d.get("name"), str)]
    sys.exit(f"FEHLER: Geraet '{name}' nicht in {config_path.name} gefunden. "
             f"Bekannt: {', '.join(known) if known else '(keine)'}")


async def connect(dev_conf: dict) -> AC:
    """Frische, authentifizierte Verbindung samt Status-Read.

    get_capabilities() IMMER vor refresh(): sonst pollt refresh() die
    IECO-Property nicht und device.ieco/_ieco_number blieben auf dem Default."""
    from msmart.device.AC.device import AirConditioner as AC

    last_exc = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        device = AC(ip=dev_conf["ip"], port=int(dev_conf.get("port", 6444)),
                    device_id=int(dev_conf["id"]))
        try:
            await device.authenticate(dev_conf["token"], dev_conf["key"])
            await device.get_capabilities()
            await device.refresh()
            return device
        except Exception as exc:
            last_exc = exc
            close_connection(device)
            print(f"  Verbindungsversuch {attempt}/{CONNECT_RETRIES} "
                  f"fehlgeschlagen ({type(exc).__name__}: {exc})")
            if attempt < CONNECT_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(
        f"Verbindung fehlgeschlagen nach {CONNECT_RETRIES} Versuchen") from last_exc


def _mode_value(device: AC):
    try:
        return int(device.operational_mode)
    except (TypeError, ValueError):
        return None


def _snapshot(device: AC) -> dict:
    """Nur nicht-geheime Zustandswerte (nie token/key)."""
    return {
        "power": device.power_state,
        "mode": device.operational_mode,
        "mode_value": _mode_value(device),
        "target": device.target_temperature,
        "ieco": device.ieco,
    }


def _fmt(state: dict) -> str:
    mode = state["mode"]
    label = f"{getattr(mode, 'name', mode)} ({state['mode_value']})"
    return (f"power={state['power']}, mode={label}, "
            f"target={state['target']}, ieco={state['ieco']}")


def _unchanged(a: dict, b: dict) -> list[str]:
    """Liste der Felder, die sich zwischen a und b GEAENDERT haben (power/mode/
    target - genau die Felder, die ein Full-State-apply() clobbern wuerde)."""
    changed = []
    for key in ("power", "mode_value", "target"):
        if a[key] != b[key]:
            changed.append(f"{key}: {a[key]!r} -> {b[key]!r}")
    return changed


async def _write_ieco_only(device: AC, target: bool) -> None:
    """Setzt iECO ausschliesslich ueber den property-only-Pfad; scheitert laut,
    wenn der Pfad nicht verfuegbar ist (in der Probe ist der Rueckfall auf ein
    volles apply() gerade NICHT gewuenscht - wir testen ja den Property-Pfad)."""
    device.ieco = target
    issued = await apply_ieco_only(device)
    if not issued:
        raise RuntimeError(
            "apply_ieco_only meldet den property-only-Pfad als NICHT verfuegbar "
            "(die private msmart-Kette fehlt oder hat sich geaendert). Damit ist "
            "diese Validierung gegenstandslos - bitte den Vertragstest pruefen.")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validiert an echter Hardware, dass ein reiner iECO-"
                    "Property-Write angenommen wird und power/mode/target "
                    "unveraendert laesst. devices.json wird nur gelesen.")
    parser.add_argument("device", help="Geraetename aus devices.json")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"Pfad zu devices.json (Standard: {DEFAULT_CONFIG}). "
                             f"Wird ausschliesslich GELESEN.")
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                        help=f"Ruhepause (s) zwischen den Verbindungen "
                             f"(Standard: {DEFAULT_SETTLE:g}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur den aktuellen Zustand lesen, NICHTS schreiben.")
    args = parser.parse_args()

    if args.settle < 0:
        sys.exit("FEHLER: --settle darf nicht negativ sein.")

    dev_conf = load_device(args.config, args.device)

    try:
        import msmart  # noqa: F401  (reine Verfuegbarkeitspruefung)
    except ImportError:
        sys.exit("FEHLER: msmart-ng ist im aktiven Interpreter nicht installiert. "
                 "Aufruf z.B.: venv/bin/python3 tools/probe_ieco_property_only.py ...")

    # --- Verbindung 1: Status lesen (und ggf. iECO toggeln) -----------------
    print(f"Verbinde mit '{args.device}' ({dev_conf['ip']}) ...")
    try:
        device = await connect(dev_conf)
    except RuntimeError as exc:
        sys.exit(f"FEHLER: {exc}\n  Falls die Anlage kurz zuvor viele "
                 f"Verbindungen sah, bitte 1-2 Minuten warten und erneut versuchen.")

    try:
        if not device.online:
            sys.exit("FEHLER: Geraet meldet sich nicht als online.")
        if not device.supports_ieco:
            sys.exit("FEHLER: Geraet meldet keine iECO-Faehigkeit - eine "
                     "Messung ist gegenstandslos. (Nichts veraendert.)")
        before = _snapshot(device)
        print(f"BEFORE : {_fmt(before)}")

        if not before["power"]:
            sys.exit("\nDas Geraet ist AUS. Der --only-if-on-Nudge greift nur an "
                     "einer laufenden Anlage - bitte per Fernbedienung einschalten "
                     "(COOL oder HEAT) und erneut aufrufen. (Nichts veraendert.)")
        if before["mode_value"] not in IECO_CAPABLE_MODE_VALUES:
            sys.exit(f"\nModus {before['mode_value']} traegt iECO nicht (nur "
                     f"COOL/HEAT). Bitte per Fernbedienung auf COOL oder HEAT "
                     f"stellen und erneut aufrufen. (Nichts veraendert.)")

        if args.dry_run:
            print("\n--dry-run: geeigneter Zustand. Es wuerde ein property-only "
                  "iECO-Write erfolgen. NICHTS veraendert.")
            return

        target = not before["ieco"]
        print(f"\nSetze iECO={target} - AUSSCHLIESSLICH property-only "
              f"(kein Zustandsbefehl) ...")
        await _write_ieco_only(device, target)
    finally:
        close_connection(device)

    await asyncio.sleep(args.settle)

    # --- Verbindung 2: verifizieren + Ausgangswert wiederherstellen ---------
    try:
        device = await connect(dev_conf)
    except RuntimeError as exc:
        sys.exit(f"FEHLER bei der Verifikation: {exc}\n  Achtung: iECO wurde auf "
                 f"{target} gesetzt und konnte NICHT wieder hergestellt werden "
                 f"(Verbindung fehlt). Bitte nach kurzer Pause den Ausgangswert "
                 f"iECO={before['ieco']} manuell/per Tool zuruecksetzen.")

    try:
        after = _snapshot(device)
        print(f"AFTER  : {_fmt(after)}")
        adopted = (after["ieco"] == target)
        clobbered = _unchanged(before, after)

        print("")
        print(f"  iECO uebernommen (property-only)? {'JA' if adopted else 'NEIN'} "
              f"(Ziel war {target}, gelesen {after['ieco']})")
        print(f"  power/mode/target unveraendert?   "
              f"{'JA' if not clobbered else 'NEIN -> ' + '; '.join(clobbered)}")

        print(f"\nStelle Ausgangswert iECO={before['ieco']} wieder her "
              f"(property-only) ...")
        await _write_ieco_only(device, before["ieco"])
    finally:
        close_connection(device)

    await asyncio.sleep(args.settle)

    # --- Verbindung 3: Wiederherstellung bestaetigen ------------------------
    restored = None
    final_clobber = None
    try:
        device = await connect(dev_conf)
        try:
            final = _snapshot(device)
            print(f"FINAL  : {_fmt(final)}")
            restored = (final["ieco"] == before["ieco"])
            final_clobber = _unchanged(before, final)
        finally:
            close_connection(device)
    except RuntimeError as exc:
        print(f"\nWARNUNG: Wiederherstellung konnte nicht bestaetigt werden "
              f"({exc}). Der Restore-Write wurde abgesetzt; bitte spaeter "
              f"pruefen, dass iECO={before['ieco']} steht.")

    # --- Gesamturteil -------------------------------------------------------
    print("\n" + "=" * 60)
    ok = adopted and not clobbered and (restored is not False) \
        and not (final_clobber or [])
    if adopted and not clobbered:
        print("ERGEBNIS: property-only iECO-Write WIRD ANGENOMMEN und laesst "
              "power/mode/target unveraendert.")
        if restored is False or (final_clobber or []):
            print("  ABER: Ausgangszustand nicht sauber wiederhergestellt - "
                  "bitte manuell pruefen.")
        elif restored is None:
            print("  (Wiederherstellung nicht bestaetigt - siehe Warnung oben.)")
        else:
            print("  Ausgangszustand wiederhergestellt.")
    elif not adopted:
        print("ERGEBNIS: property-only iECO-Write WIRD NICHT UEBERNOMMEN "
              "(Firmware ignoriert ihn oder antwortet anders). apply_ieco_only "
              "sollte NICHT in ensure_ieco verdrahtet werden - der volle "
              "apply()-Pfad (durch Reconcile abgesichert) bleibt.")
    else:
        print("ERGEBNIS: der property-only Write hat power/mode/target VERAENDERT "
              f"({'; '.join(clobbered)}) - das darf nicht sein. NICHT verdrahten.")
    print("=" * 60)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
