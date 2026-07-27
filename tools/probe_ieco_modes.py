#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
probe_ieco_modes.py

Diagnose-Werkzeug: misst empirisch, in WELCHEN Betriebsmodi eine Midea-
Klimaanlage den iECO-Modus tatsaechlich annimmt.

Hintergrund: msmart-ng dekodiert die iECO-Capability laut eigenem Quellcode als
"1,3,8 - Cool, 3,4,8 - Heat, 8 = ECOMaster" - iECO ist also je nach Geraet an
COOL und/oder HEAT gebunden, FAN_ONLY taucht in keiner dieser Listen auf. Die
oeffentliche API kollabiert diese Information jedoch zu einem einzelnen Bool
(supports_ieco); der zulaessige Modus ist daraus nicht mehr ableitbar (in
msmart-ng als "TODO iECO can be cool, heat or both" vermerkt). Dieses Skript
ermittelt ihn deshalb durch Messung am echten Geraet.

WICHTIG - das Skript greift aktiv ein:
  * Es schaltet das Geraet ein und wechselt nacheinander den Betriebsmodus.
  * Der Ausgangszustand (Power, Modus, Zieltemperatur, iECO) wird VOR dem
    ersten Eingriff gesichert und am Ende wiederhergestellt - auch bei einem
    Fehler oder bei Abbruch per Strg+C.
  * devices.json wird ausschliesslich GELESEN, niemals geschrieben.

Nutzung:
    python3 tools/probe_ieco_modes.py <Geraetename>
    python3 tools/probe_ieco_modes.py <Geraetename> --dry-run
    python3 tools/probe_ieco_modes.py <Geraetename> --modes cool,heat,auto
    python3 tools/probe_ieco_modes.py <Geraetename> --yes --settle 12
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

# devices.json liegt im Repo-Root, dieses Skript in tools/ - daher parent.parent.
CONFIG_PATH = Path(__file__).resolve().parent.parent / "devices.json"

CONNECT_RETRIES = 3
RETRY_DELAY = 3.0
# Wartezeit nach jedem apply(), damit die Firmware den Wechsel uebernimmt, bevor
# zurueckgelesen wird. Ein Moduswechsel schaltet ggf. den Verdichter - das
# braucht spuerbar laenger als ein reines Flag. Bewusst grosszuegig; ein zu
# kurzer Wert wuerde ein "ignoriert" vortaeuschen, wo nur zu frueh gelesen wurde.
DEFAULT_SETTLE = 10.0

# Ergebnis-Kategorien einer Einzelmessung.
RESULT_ACCEPTED = "iECO AKTIV"
RESULT_IGNORED = "iECO IGNORIERT"
RESULT_MODE_REJECTED = "MODUS NICHT UEBERNOMMEN"
RESULT_ERROR = "FEHLER"


def load_device(name: str) -> dict:
    """Liest devices.json (nur lesend) und liefert den Eintrag zu ``name``.

    Bewusst mit denselben klaren Abbruchmeldungen wie midea_ieco_ensure.py,
    statt mit einem rohen Traceback zu enden."""
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
    """Verbindung best-effort schliessen (Methodenname variiert je Version)."""
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


async def connect(dev_conf: dict, retries: int = CONNECT_RETRIES) -> AC:
    """Baut eine FRISCHE, authentifizierte Verbindung auf und liest den Status.

    get_capabilities() wird IMMER vor refresh() aufgerufen: msmart-ng's refresh()
    pollt nur Properties aus _supported_properties, und die werden erst durch
    get_capabilities() befuellt. Ohne diesen Aufruf liefert device.ieco konstant
    den Default False - eine Messung waere dann wertlos.

    Fuer jeden Versuch wird ein NEUES AC-Objekt erzeugt: ein fehlgeschlagener
    Verbindungsversuch kann das alte mit defektem Socket-Zustand hinterlassen."""
    from msmart.device.AC.device import AirConditioner as AC

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
            await close_device(device)
            print(f"    Verbindungsversuch {attempt}/{retries} fehlgeschlagen "
                  f"({type(exc).__name__}: {exc})")
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"Verbindung fehlgeschlagen nach {retries} Versuchen") from last_exc


def snapshot(device: AC) -> dict:
    """Sichert den wiederherstellbaren Ausgangszustand des Geraets."""
    return {
        "power_state": device.power_state,
        "operational_mode": device.operational_mode,
        "target_temperature": device.target_temperature,
        "ieco": device.ieco is True,
    }


def describe(snap: dict) -> str:
    mode = snap["operational_mode"]
    mode_name = getattr(mode, "name", mode)
    return (f"power={snap['power_state']}, mode={mode_name} ({int(mode)}), "
            f"target={snap['target_temperature']}, ieco={snap['ieco']}")


async def restore(dev_conf: dict, snap: dict) -> bool:
    """Stellt den gesicherten Ausgangszustand wieder her.

    Alle Werte werden gesetzt und mit EINEM apply() uebertragen. iECO wird nur
    dann wieder aktiviert, wenn es im Ausgangszustand aktiv war. Rueckgabe: True
    bei Erfolg - bei False muss der Nutzer selbst nachsehen, deshalb wird der
    Zielzustand dann ausdruecklich ausgegeben."""
    device = None
    try:
        device = await connect(dev_conf)
        device.operational_mode = snap["operational_mode"]
        if snap["target_temperature"] is not None:
            device.target_temperature = snap["target_temperature"]
        device.ieco = snap["ieco"]
        # power_state zuletzt setzen: der Zielzustand soll auch dann stimmen,
        # wenn das Geraet urspruenglich ausgeschaltet war.
        device.power_state = snap["power_state"]
        await device.apply()
        return True
    except Exception as exc:
        print(f"  WARNUNG: Wiederherstellung fehlgeschlagen "
              f"({type(exc).__name__}: {exc}).")
        return False
    finally:
        if device is not None:
            await close_device(device)


async def probe_mode(dev_conf: dict, mode, settle: float) -> tuple[str, str]:
    """Misst EINEN Betriebsmodus. Liefert (Ergebnis-Kategorie, Detailtext).

    Ablauf, bewusst mit frischer Verbindung je Schritt (das Projekt hat gelernt,
    dass ein AC-Objekt nach einem Fehlversuch unbrauchbar sein kann):
      1. Geraet einschalten und in den Zielmodus versetzen -> apply()
      2. zurueckstellen lassen, neu verbinden, pruefen ob der Modus wirklich
         uebernommen wurde (sonst ist die iECO-Messung nicht aussagekraeftig)
      3. iECO setzen -> apply()
      4. zurueckstellen lassen, neu verbinden, echten iECO-Zustand lesen
    """
    mode_name = mode.name
    device = None
    try:
        # --- Schritt 1: Modus stellen -------------------------------------
        device = await connect(dev_conf)
        device.power_state = True
        device.operational_mode = mode
        # iECO bewusst zuerst ausschalten, damit ein Rest aus dem vorherigen
        # Durchgang die Messung nicht als falsches Positiv verfaelscht.
        device.ieco = False
        await device.apply()
        await close_device(device)
        device = None
        await asyncio.sleep(settle)

        # --- Schritt 2: wurde der Modus uebernommen? -----------------------
        device = await connect(dev_conf)
        actual = device.operational_mode
        if int(actual) != int(mode):
            actual_name = getattr(actual, "name", actual)
            return (RESULT_MODE_REJECTED,
                    f"Geraet meldet {actual_name} ({int(actual)}) statt {mode_name}")
        if device.ieco is True:
            # Sollte nach dem expliziten Ausschalten nicht vorkommen; wuerde die
            # Messung unbrauchbar machen, daher klar benennen statt still werten.
            return (RESULT_ERROR, "iECO liess sich vor der Messung nicht ausschalten")

        # --- Schritt 3: iECO aktivieren ------------------------------------
        device.ieco = True
        await device.apply()
        await close_device(device)
        device = None
        await asyncio.sleep(settle)

        # --- Schritt 4: echten Zustand zurueklesen -------------------------
        device = await connect(dev_conf)
        confirmed = device.ieco is True
        still_mode = int(device.operational_mode) == int(mode)
        detail = f"ieco={device.ieco}"
        if not still_mode:
            other = getattr(device.operational_mode, "name", device.operational_mode)
            detail += f", Modus wechselte auf {other}"
        return (RESULT_ACCEPTED if confirmed else RESULT_IGNORED, detail)

    except Exception as exc:
        return (RESULT_ERROR, f"{type(exc).__name__}: {exc}")
    finally:
        if device is not None:
            await close_device(device)


def parse_modes(spec: str | None, device: AC) -> list:
    """Bestimmt die zu messenden Modi.

    Ohne --modes werden alle Modi gemessen, die das Geraet laut Capabilities
    ueberhaupt beherrscht. Mit --modes wird die Auswahl gegen genau diese Liste
    geprueft, damit kein Durchgang an einem vom Geraet gar nicht unterstuetzten
    Modus verschwendet wird."""
    supported = list(device.supported_operation_modes)
    if not spec:
        return supported

    by_name = {m.name.lower(): m for m in supported}
    chosen, unknown = [], []
    for raw in spec.split(","):
        key = raw.strip().lower()
        if not key:
            continue
        if key in by_name:
            if by_name[key] not in chosen:
                chosen.append(by_name[key])
        else:
            unknown.append(raw.strip())
    if unknown:
        sys.exit(f"FEHLER: Unbekannte/nicht unterstuetzte Modi: {', '.join(unknown)}. "
                 f"Verfuegbar: {', '.join(sorted(by_name))}")
    if not chosen:
        sys.exit("FEHLER: --modes enthielt keinen gueltigen Modus.")
    return chosen


def print_summary(results: list[tuple[str, str, str]]) -> None:
    """Gibt die Ergebnistabelle und eine direkt verwertbare Auswertung aus."""
    width = max((len(name) for name, _, _ in results), default=10)
    print("")
    print("=" * 64)
    print("ERGEBNIS")
    print("=" * 64)
    for name, result, detail in results:
        print(f"  {name:<{width}}  {result:<24}  {detail}")

    accepted = [n for n, r, _ in results if r == RESULT_ACCEPTED]
    ignored = [n for n, r, _ in results if r == RESULT_IGNORED]
    inconclusive = [(n, r) for n, r, _ in results
                    if r in (RESULT_MODE_REJECTED, RESULT_ERROR)]

    print("")
    print(f"iECO wird angenommen in : {', '.join(accepted) if accepted else '(keinem gemessenen Modus)'}")
    print(f"iECO wird ignoriert in  : {', '.join(ignored) if ignored else '(keinem)'}")
    if inconclusive:
        print(f"Nicht aussagekraeftig   : "
              f"{', '.join(f'{n} ({r})' for n, r in inconclusive)}")
        print("  -> Diese Modi bitte einzeln nachmessen, bevor sie ausgewertet werden.")

    if accepted:
        literal = ", ".join(f"OperationalMode.{n}" for n in accepted)
        print("")
        print("Fuer den geplanten Modus-Guard (nur die BESTAETIGTEN Modi):")
        print(f"  IECO_CAPABLE_MODES = frozenset({{{literal}}})")
    print("=" * 64)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Misst, in welchen Betriebsmodi das Geraet iECO annimmt.")
    parser.add_argument("device", help="Geraetename aus devices.json")
    parser.add_argument("--modes",
                        help="Kommaliste der zu messenden Modi (z.B. cool,heat,auto). "
                             "Standard: alle vom Geraet unterstuetzten.")
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                        help=f"Wartezeit in Sekunden nach jedem apply() "
                             f"(Standard: {DEFAULT_SETTLE:g}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur den Messplan anzeigen, das Geraet NICHT anfassen.")
    parser.add_argument("--yes", action="store_true",
                        help="Sicherheitsabfrage ueberspringen (fuer nicht-interaktive Laeufe).")
    args = parser.parse_args()

    if args.settle < 0:
        sys.exit("FEHLER: --settle darf nicht negativ sein.")

    dev_conf = load_device(args.device)

    try:
        import msmart  # noqa: F401  (reine Verfuegbarkeitspruefung)
    except ImportError:
        sys.exit("FEHLER: msmart-ng ist im aktiven Python-Interpreter nicht installiert. "
                 "Ist die venv aktiv? Aufruf z.B.: venv/bin/python3 tools/probe_ieco_modes.py ...")

    print(f"Verbinde mit '{args.device}' ({dev_conf['ip']}) ...")
    device = await connect(dev_conf)
    try:
        if not device.online:
            sys.exit("FEHLER: Geraet meldet sich nicht als online.")
        if not device.supports_ieco:
            sys.exit("FEHLER: Geraet meldet keine iECO-Faehigkeit - eine Messung "
                     "der Modi ist damit gegenstandslos.")
        original = snapshot(device)
        modes = parse_modes(args.modes, device)
    finally:
        await close_device(device)

    print(f"Ausgangszustand: {describe(original)}")
    print("")
    print("Messplan:")
    for m in modes:
        print(f"  - {m.name} ({int(m)})")
    duration = len(modes) * (2 * args.settle + 12) + 15
    print("")
    print(f"Geschaetzte Dauer: ca. {duration / 60:.0f}-{duration / 40:.0f} Minuten.")

    if args.dry_run:
        print("")
        print("--dry-run: Es wurde NICHTS am Geraet veraendert.")
        return

    print("")
    print("!! Das Geraet wird jetzt eingeschaltet und der Betriebsmodus mehrfach")
    print("!! umgeschaltet. Bei HEAT heizt die Anlage kurzzeitig.")
    print("!! Der oben genannte Ausgangszustand wird danach wiederhergestellt.")
    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("FEHLER: Nicht-interaktiver Lauf ohne --yes - abgebrochen.")
        try:
            if input("Fortfahren? [j/N]: ").strip().lower() not in ("j", "y"):
                sys.exit("Abgebrochen - es wurde nichts veraendert.")
        except EOFError:
            sys.exit("\nFEHLER: Eingabe abgebrochen (EOF).")

    results: list[tuple[str, str, str]] = []
    try:
        for idx, mode in enumerate(modes, start=1):
            print("")
            print(f"[{idx}/{len(modes)}] Messe {mode.name} ({int(mode)}) ...")
            result, detail = await probe_mode(dev_conf, mode, args.settle)
            print(f"    -> {result}  ({detail})")
            results.append((mode.name, result, detail))
    except KeyboardInterrupt:
        # Abbruch ist ausdruecklich erlaubt - die Wiederherstellung unten laeuft
        # trotzdem, damit das Geraet nicht in einem Messzustand zurueckbleibt.
        print("")
        print("Abbruch durch Nutzer - stelle Ausgangszustand wieder her ...")
    finally:
        print("")
        print(f"Stelle Ausgangszustand wieder her: {describe(original)}")
        ok = await restore(dev_conf, original)
        if ok:
            print("  Ausgangszustand wiederhergestellt.")
        else:
            print("  ACHTUNG: Bitte den oben genannten Zustand von Hand pruefen "
                  "(App oder Fernbedienung).")

    if results:
        print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
