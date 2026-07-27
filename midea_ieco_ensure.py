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

Die Ausgabe ist zweisprachig (Englisch als Default, Deutsch bei deutscher
Locale oder MIDEA_IECO_LANG=de) - siehe midea_i18n.py.

Nutzung:
    python3 midea_ieco_ensure.py <device_name|all>
    python3 midea_ieco_ensure.py <device_name|all> --only-if-on
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import logging
from pathlib import Path
from typing import TYPE_CHECKING

# Sprachwahl: gemeinsame Mechanik fuer beide Werkzeuge (Reihenfolge und
# englischer Default sind dort dokumentiert). resolve_lang wird mitimportiert,
# damit es weiterhin ueber dieses Modul erreichbar bleibt.
from midea_i18n import make_translator, resolve_lang  # noqa: F401

if TYPE_CHECKING:
    # Nur fuer Typpruefer: der echte Import passiert lazy in
    # connect_and_refresh, damit das Modul - und damit die netzwerkfreie
    # Uebersicht `list` - auch ohne installiertes msmart importierbar bleibt.
    from msmart.device.AC.device import AirConditioner as AC

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH = Path(__file__).parent / "devices.json"
CONNECT_RETRIES = 3
ACTION_RETRIES = 3
RETRY_DELAY = 3.0

# Reservierte Ziel-Woerter: das sind keine Geraetenamen, sondern Sonderbefehle.
# 'all' -> alle Geraete; 'list' -> nur die Uebersicht anzeigen (kein Netz).
# WICHTIG: in Sync halten mit is_valid_device_name() in install.sh, das
# Geraetenamen mit genau diesen Werten bereits bei der Einrichtung ablehnt.
TARGET_ALL = "all"
TARGET_LIST = "list"
RESERVED_TARGETS = frozenset({TARGET_ALL, TARGET_LIST})

# Stabile, oeffentliche Befehlsnamen: die von install.sh in BIN_DIR angelegten
# Wrapper. Als Konstanten gebuendelt, damit die Uebersicht/Hilfe genau die
# Namen nennt, unter denen die Werkzeuge tatsaechlich installiert werden.
CMD_MAIN = "midea-ieco"
CMD_REFRESH = "midea-ieco-refresh-tokens"
CMD_UPDATE = "midea-ieco-update"

# Betriebsmodi, in denen iECO nachweislich haelt. Am echten Geraet gemessen
# (2026-07-27, Midea PortaSplit / 2060008E, alle fuenf Modi durchgeschaltet):
#   COOL (2) -> aktiv     HEAT (4) -> aktiv
#   AUTO (1), DRY (3), FAN_ONLY (5) -> Befehl wird angenommen, aber verworfen
# Das deckt sich mit msmart-ngs eigener Capability-Dekodierung, die iECO als
# "1,3,8 - Cool, 3,4,8 - Heat, 8 = ECOMaster" liest (msmart/device/AC/command.py)
# und dort mit "TODO iECO can be cool, heat or both" vermerkt ist: iECO ist je
# nach Geraet an COOL und/oder HEAT gebunden, andere Modi kommen nicht vor.
#
# Als ZAHLEN hinterlegt, nicht als Enum-Member: msmart wird bewusst erst lazy
# importiert (damit die netzwerkfreie Uebersicht auch ohne installiertes msmart
# laeuft), ein Enum-Zugriff auf Modulebene wuerde das zunichtemachen.
IECO_CAPABLE_MODE_VALUES = frozenset({2, 4})

# Katalog: key -> (englisch, deutsch). Platzhalter im printf-Stil (%s), damit
# beide Sprachfassungen strukturell identisch bleiben und ein fehlender
# Platzhalter beim Testen sofort auffaellt.
_MESSAGES: dict[str, tuple[str, str]] = {
    # --- Modusnamen ---------------------------------------------------------
    # Die Modus-Bezeichner COOL/HEAT selbst bleiben unuebersetzt: so heissen sie
    # im Geraet, in msmart-ng und auf der Fernbedienung. Nur das Bindewort ist
    # sprachabhaengig.
    "mode_names_capable": ("COOL or HEAT", "COOL oder HEAT"),
    # --- Konfiguration ------------------------------------------------------
    "cfg_not_found": (
        "Configuration file not found: %s",
        "Konfigurationsdatei nicht gefunden: %s"),
    "cfg_unreadable": (
        "ERROR: %s could not be read (%s: %s).",
        "FEHLER: %s konnte nicht gelesen werden (%s: %s)."),
    "cfg_bad_shape": (
        'ERROR: %s does not have the expected form {"devices": [...]}.',
        'FEHLER: %s hat nicht die erwartete Form {"devices": [...]}.'),
    "soft_cfg_missing": (
        "No %s yet - please run install.sh.",
        "Noch keine %s vorhanden - bitte install.sh ausfuehren."),
    "soft_cfg_unreadable": (
        "%s could not be read (%s: %s).",
        "%s konnte nicht gelesen werden (%s: %s)."),
    "soft_cfg_bad_shape": (
        '%s does not have the expected form {"devices": [...]}.',
        '%s hat nicht die erwartete Form {"devices": [...]}.'),
    # --- Uebersicht ---------------------------------------------------------
    "ov_headline": (
        "%s - keeps the iECO mode of Midea air conditioners active, locally.",
        "%s - haelt den iECO-Modus von Midea-Klimaanlagen lokal aktiv."),
    "ov_config": (
        "Configuration: %s",
        "Konfiguration: %s"),
    "ov_note": (
        "Note: %s",
        "Hinweis: %s"),
    "ov_no_devices": (
        "Configured devices: (none)",
        "Konfigurierte Geraete: (keine)"),
    "ov_devices_count": (
        "Configured devices (%s):",
        "Konfigurierte Geraete (%s):"),
    "ov_skipped_entry": (
        "  - (skipped: unexpected entry of type %s)",
        "  - (uebersprungen: unerwarteter Eintrag vom Typ %s)"),
    "ov_unnamed": (
        "(unnamed)",
        "(ohne Namen)"),
    "ov_invalid_name": (
        "(invalid name)",
        "(ungueltiger Name)"),
    "ov_reserved_name": (
        "      WARNING: '%s' is a reserved word and cannot be addressed from the "
        "command line - please rename it in %s.",
        "      WARNUNG: '%s' ist ein reserviertes Wort und per Kommando nicht "
        "ansteuerbar - bitte in %s umbenennen."),
    "ov_examples_header": (
        "Examples:",
        "Beispiele:"),
    "ov_example_device": (
        "  %s <device-name>          ensure iECO (powers on if needed)",
        "  %s <Geraetename>          iECO sicherstellen (schaltet bei Bedarf ein)"),
    "ov_example_all": (
        "  %s %s                    all configured devices",
        "  %s %s                    alle konfigurierten Geraete"),
    "ov_example_only_if_on": (
        "  %s %s --only-if-on       only running devices, powers nothing on",
        "  %s %s --only-if-on       nur laufende Geraete, schaltet nichts ein"),
    "ov_example_list": (
        "  %s %s                   show this overview",
        "  %s %s                   diese Uebersicht anzeigen"),
    "ov_example_refresh": (
        "  %s --all     renew token/key from the cloud",
        "  %s --all     Token/Key aus der Cloud erneuern"),
    "ov_example_update": (
        "  %s              update the project",
        "  %s              Projekt aktualisieren"),
    "ov_full_options": (
        "All options: %s --help",
        "Vollstaendige Optionen: %s --help"),
    "ov_path_note": (
        "(The %s commands require their BIN directory to be on the PATH; "
        "otherwise call directly: venv/bin/python3 <script> ...)",
        "(Die %s-Befehle setzen ihr BIN-Verzeichnis im PATH voraus; "
        "sonst direkt: venv/bin/python3 <skript> ...)"),
    # --- Verbindung ---------------------------------------------------------
    "conn_attempt_failed": (
        "  [%s] Connection attempt %s/%s failed (%s): %s",
        "  [%s] Verbindungsversuch %s/%s fehlgeschlagen (%s): %s"),
    "conn_gave_up": (
        "Connection to %s failed after %s attempts",
        "Verbindung zu %s fehlgeschlagen nach %s Versuchen"),
    # --- ensure_ieco --------------------------------------------------------
    "dev_error": (
        "[%s] ERROR: %s",
        "[%s] FEHLER: %s"),
    "dev_not_online": (
        "[%s] ERROR: Device does not report itself as online.",
        "[%s] FEHLER: Geraet meldet sich nicht als online."),
    "dev_off_only_if_on": (
        "[%s] --only-if-on is active and the device is off. No action, no "
        "further queries.",
        "[%s] --only-if-on aktiv und Geraet ist aus. Keine Aktion, keine "
        "weiteren Abfragen."),
    "dev_caps_failed": (
        "[%s] ERROR during get_capabilities()/refresh(): %s: %s",
        "[%s] FEHLER bei get_capabilities()/refresh(): %s: %s"),
    "dev_status_before": (
        "[%s] Status before action: power=%s, mode=%s, ieco=%s, eco=%s",
        "[%s] Status vor Aktion: power=%s, mode=%s, ieco=%s, eco=%s"),
    "dev_no_ieco_capability": (
        "[%s] ERROR: Device reports no iECO capability.",
        "[%s] FEHLER: Geraet meldet keine iECO-Faehigkeit."),
    "dev_already_desired": (
        "[%s] Already in the desired state (on, iECO active). No further "
        "queries necessary.",
        "[%s] Bereits im gewuenschten Zustand (an, iECO aktiv). Keine weiteren "
        "Abfragen notwendig."),
    "dev_mode_unsupported": (
        "[%s] Operating mode %s does not support iECO (only %s).",
        "[%s] Betriebsmodus %s unterstuetzt kein iECO (nur %s)."),
    "dev_mode_only_if_on": (
        "[%s] --only-if-on is active: no action, not an error.",
        "[%s] --only-if-on aktiv: keine Aktion, kein Fehler."),
    "dev_mode_nothing_switched": (
        "[%s] Nothing was switched. Please set the unit to %s and call again.",
        "[%s] Es wurde nichts geschaltet. Bitte die Anlage auf %s stellen und "
        "erneut aufrufen."),
    "dev_apply_attempt_failed": (
        "  [%s] apply() attempt %s/%s failed (%s): %s",
        "  [%s] apply()-Versuch %s/%s fehlgeschlagen (%s): %s"),
    "dev_reconnect_failed": (
        "  [%s] Reconnect before retry failed (%s): %s",
        "  [%s] Reconnect vor Wiederholung fehlgeschlagen (%s): %s"),
    "dev_apply_failed": (
        "[%s] ERROR while setting: %s",
        "[%s] FEHLER beim Setzen: %s"),
    "dev_verify_failed": (
        "[%s] ERROR during verification: %s",
        "[%s] FEHLER bei Verifikation: %s"),
    "dev_status_after": (
        "[%s] Status after action: power=%s, mode=%s, ieco=%s, eco=%s",
        "[%s] Status nach Aktion: power=%s, mode=%s, ieco=%s, eco=%s"),
    "dev_still_disabled": (
        "[%s] ERROR: According to the device, iECO is still disabled!",
        "[%s] FEHLER: iECO ist laut Geraet weiterhin deaktiviert!"),
    "dev_ok": (
        "[%s] OK: iECO is active (confirmed by the device).",
        "[%s] OK: iECO ist aktiv (vom Geraet bestaetigt)."),
    # --- Konfigurationspruefung --------------------------------------------
    "cfgchk_missing_field": (
        "required field '%s' is missing",
        "Pflichtfeld '%s' fehlt"),
    "cfgchk_id_not_numeric": (
        "field 'id' is not numeric (%s)",
        "Feld 'id' ist nicht numerisch (%s)"),
    "cfgchk_port_not_numeric": (
        "field 'port' is not numeric (%s)",
        "Feld 'port' ist nicht numerisch (%s)"),
    # --- main() -------------------------------------------------------------
    "cli_description": (
        "Ensures that iECO is active on Midea air conditioners.",
        "Stellt sicher, dass iECO auf Midea-Klimaanlagen aktiv ist."),
    "cli_help_target": (
        "Device name from devices.json, 'all' (all devices) or 'list' (show "
        "configured devices). Without an argument: overview.",
        "Geraetename aus devices.json, 'all' (alle Geraete) oder 'list' "
        "(konfigurierte Geraete anzeigen). Ohne Argument: Uebersicht."),
    "cli_help_only_if_on": (
        "Do NOT power the device on. Only check and re-enable iECO if it is "
        "already running.",
        "Geraet NICHT einschalten. Nur pruefen und iECO nachziehen, falls es "
        "bereits laeuft."),
    "main_skipped_entries": (
        "WARNING: %s unexpected entry/entries in %s skipped (not an object).",
        "WARNUNG: %s unerwartete(r) Eintrag/Eintraege in %s uebersprungen "
        "(kein Objekt)."),
    "main_incomplete_entry": (
        "WARNING: Device %s in %s is incomplete (%s) - skipped.",
        "WARNUNG: Geraet %s in %s unvollstaendig (%s) - uebersprungen."),
    "main_device_not_found": (
        "Device '%s' not found in devices.json.",
        "Geraet '%s' nicht in devices.json gefunden."),
    "main_no_devices_configured": (
        "No devices configured in devices.json. Nothing to do.",
        "Keine Geraete in devices.json konfiguriert. Nichts zu tun."),
    "main_result_ok": (
        "Overall result: OK.",
        "Gesamtergebnis: OK."),
    "main_result_error": (
        "Overall result: ERROR - at least one device has a problem.",
        "Gesamtergebnis: FEHLER - mindestens ein Geraet hat ein Problem."),
}

t = make_translator(_MESSAGES)


def _mode_value(mode: object) -> int | None:
    """Liefert den numerischen Wert eines Betriebsmodus, oder None, wenn er
    sich nicht bestimmen laesst.

    None ist ausdruecklich erlaubt und bedeutet 'unbekannt': der Modus-Guard
    laesst in diesem Fall bewusst durch (fail-open), statt eine womoeglich
    funktionierende Kombination auf einem anderen Geraet oder unter einer
    kuenftigen msmart-Version faelschlich zu blockieren."""
    try:
        return int(mode)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _mode_label(mode: object) -> str:
    """Formatiert einen Betriebsmodus lesbar als 'COOL (2)'.

    msmart-ng's OperationalMode ist ein IntEnum: ab Python 3.11 rendert es im
    f-String als nackte Zahl ('5'), was in Logs und Fehlerberichten schwer zu
    deuten ist. Name UND Zahl zusammen sind eindeutig - und genau das Format,
    das die READMEs ohnehin zeigen."""
    name = getattr(mode, "name", None)
    value = _mode_value(mode)
    if name is None:
        return str(mode) if value is None else str(value)
    return f"{name} ({value})" if value is not None else str(name)


def load_config() -> dict:
    """Liest devices.json. Fehlt die Datei, ist sie unlesbar, nicht als UTF-8
    dekodierbar, kein gueltiges JSON oder hat sie nicht die erwartete Form
    ({"devices": [...]}), wird mit
    klarer Meldung abgebrochen statt mit einem rohen Traceback - relevant fuer
    den 20-Minuten-Cron-Lauf."""
    if not CONFIG_PATH.exists():
        print(t("cfg_not_found", CONFIG_PATH))
        sys.exit(1)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError (nicht-UTF-8-Datei) ab
        print(t("cfg_unreadable", CONFIG_PATH, type(exc).__name__, exc))
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        print(t("cfg_bad_shape", CONFIG_PATH))
        sys.exit(1)
    return data


def _read_devices_soft() -> tuple[list, str | None]:
    """Liest die Geraeteliste NUR fuer die Anzeige (Uebersicht) moeglichst
    tolerant: statt wie load_config() bei jedem Problem hart abzubrechen, wird
    ein (devices, hinweis)-Paar zurueckgegeben. So bleibt `midea-ieco list`
    auch bei fehlender oder kaputter devices.json informativ und endet mit
    Exit 0. Gibt es keinen verwertbaren Inhalt, ist devices leer und hinweis
    erklaert warum. Es findet KEIN Netzwerkzugriff statt."""
    if not CONFIG_PATH.exists():
        return [], t("soft_cfg_missing", CONFIG_PATH.name)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError (nicht-UTF-8-Datei) ab
        return [], t("soft_cfg_unreadable", CONFIG_PATH, type(exc).__name__, exc)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        return [], t("soft_cfg_bad_shape", CONFIG_PATH)
    return data["devices"], None


def print_overview() -> None:
    """Gibt eine kompakte, netzwerkfreie Uebersicht aus: was das Werkzeug tut,
    wo die Konfiguration liegt, welche Geraete konfiguriert sind (nur Name/IP/
    Port - NIEMALS token/key) und die wichtigsten Beispielaufrufe inklusive der
    Schwesterbefehle. Zweck: die Nutzung vollstaendig erfassen, ohne Pfade oder
    Dateien kennen zu muessen. Endet immer erfolgreich (der Aufrufer setzt
    Exit 0). Baut bewusst KEINE Verbindung auf, damit die Uebersicht sofort und
    unabhaengig von der Netzwerklage funktioniert."""
    devices, note = _read_devices_soft()

    print(t("ov_headline", CMD_MAIN))
    print(t("ov_config", CONFIG_PATH))
    print("")

    if note is not None:
        print(t("ov_note", note))
    elif not devices:
        print(t("ov_no_devices"))
    else:
        print(t("ov_devices_count", len(devices)))
        for d in devices:
            # Robust gegen eine von Hand kaputt editierte devices.json: ein
            # Eintrag, der kein Objekt ist, darf die Uebersicht NICHT mit einem
            # Traceback abbrechen (Ziel: funktioniert auch bei kaputter Datei).
            if not isinstance(d, dict):
                print(t("ov_skipped_entry", type(d).__name__))
                continue
            # name als String erzwingen: ein verschachteltes Objekt unter 'name'
            # wuerde sonst die 'in RESERVED_TARGETS'-Pruefung (unhashbar) mit
            # einem TypeError abbrechen - und beim Formatieren ungewollt Inhalt
            # ausgeben. ip/port erscheinen nur in einem f-String (absturzsicher).
            name = d.get("name", t("ov_unnamed"))
            if not isinstance(name, str):
                name = t("ov_invalid_name")
            ip = d.get("ip", "?")
            port = d.get("port", 6444)
            print(f"  - {name}  ->  {ip}:{port}")
            # Sicherheits-/Konsistenz-Guard: ein Geraetename, der zufaellig
            # einem reservierten Wort entspricht (nur per Hand-Edit moeglich),
            # ist per CLI nicht ansteuerbar - er wuerde als Sonderbefehl
            # interpretiert. Klar darauf hinweisen, statt still zu scheitern.
            if name in RESERVED_TARGETS:
                print(t("ov_reserved_name", name, CONFIG_PATH.name))

    print("")
    print(t("ov_examples_header"))
    print(t("ov_example_device", CMD_MAIN))
    print(t("ov_example_all", CMD_MAIN, TARGET_ALL))
    print(t("ov_example_only_if_on", CMD_MAIN, TARGET_ALL))
    print(t("ov_example_list", CMD_MAIN, TARGET_LIST))
    print(t("ov_example_refresh", CMD_REFRESH))
    print(t("ov_example_update", CMD_UPDATE))
    print("")
    print(t("ov_full_options", CMD_MAIN))
    print(t("ov_path_note", CMD_MAIN))


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
    # Lazy-Import (spiegelt midea_refresh_tokens.verify_credentials): erst der
    # tatsaechliche Geraetezugriff braucht msmart. So bleibt das Modul - und
    # damit die netzwerkfreie Uebersicht `list` - auch ohne installiertes
    # msmart importier- und nutzbar.
    from msmart.device.AC.device import AirConditioner as AC
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
            print(t("conn_attempt_failed", name, attempt, retries,
                    type(exc).__name__, exc))
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(t("conn_gave_up", name, retries)) from last_exc


async def ensure_ieco(dev_conf: dict, only_if_on: bool) -> bool:
    name = dev_conf["name"]

    try:
        device = await connect_and_refresh(dev_conf)
    except RuntimeError as exc:
        print(t("dev_error", name, exc))
        return False

    try:
        if not device.online:
            print(t("dev_not_online", name))
            return False

        is_on = device.power_state

        # Fruehzeitiger Ausstieg VOR jeder teuren Capability-Abfrage:
        # Ist das Geraet aus und duerfen wir es per --only-if-on nicht
        # einschalten, ist alles gesagt. power_state kommt aus refresh() und ist
        # ohne get_capabilities() korrekt; der ieco-Zustand spielt hier keine
        # Rolle - also kein get_capabilities(), kein apply(), keine Netzwerklast.
        if only_if_on and not is_on:
            print(t("dev_off_only_if_on", name))
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
            print(t("dev_caps_failed", name, type(exc).__name__, exc))
            return False

        print(t("dev_status_before", name, is_on,
                _mode_label(device.operational_mode), device.ieco, device.eco))

        if not device.supports_ieco:
            print(t("dev_no_ieco_capability", name))
            return False

        # Kurzschluss BEWUSST vor dem Modus-Guard: laeuft iECO bereits, ist alles
        # gut - unabhaengig davon, was wir ueber den Modus annehmen. So kann eine
        # zu enge Modus-Liste niemals einen tatsaechlich funktionierenden Zustand
        # als Problem melden.
        if is_on and device.ieco:
            print(t("dev_already_desired", name))
            return True

        # Modus-Guard: iECO ist an den Betriebsmodus gebunden (siehe
        # IECO_CAPABLE_MODE_VALUES). In einem nicht tragenden Modus nimmt das
        # Geraet den Befehl zwar an, verwirft ihn aber still - ein apply() samt
        # Verifikations-Roundtrip waere garantiert vergeblich. Frueher lief das
        # in die generische Meldung "iECO ist laut Geraet weiterhin deaktiviert",
        # die den Grund nicht nannte (gemeldet als Issue #3).
        mode_value = _mode_value(device.operational_mode)
        if mode_value is not None and mode_value not in IECO_CAPABLE_MODE_VALUES:
            mode_text = _mode_label(device.operational_mode)
            print(t("dev_mode_unsupported", name, mode_text,
                    t("mode_names_capable")))
            if only_if_on:
                # Ein bewusst gewaehlter Modus ist kein Fehlerzustand. Im
                # 20-Minuten-Cron wuerde ein Fehlschlag hier taeglich 72 Meldungen
                # und Exit 2 erzeugen - konsistent zum ausgeschalteten Geraet wird
                # das deshalb als "nichts zu tun" gewertet.
                print(t("dev_mode_only_if_on", name))
                return True
            # Beim ausdruecklichen Aufruf wollte der Nutzer iECO. Nichts schalten
            # (auch nicht einschalten - eine halbe Aktion ohne iECO waere nicht das
            # Gewuenschte), aber klar sagen, was zu tun ist, und den Lauf als
            # nicht erfolgreich werten.
            print(t("dev_mode_nothing_switched", name, t("mode_names_capable")))
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
                print(t("dev_apply_attempt_failed", name, attempt, ACTION_RETRIES,
                        type(exc).__name__, exc))
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
                        print(t("dev_reconnect_failed", name,
                                type(exc2).__name__, exc2))
                        break

        if not applied:
            print(t("dev_apply_failed", name, last_exc))
            return False

        await asyncio.sleep(2.0)
        await close_device(device)
        try:
            # with_capabilities=True ist hier zwingend: sonst pollt refresh() die
            # IECO-Property nicht und device.ieco laese faelschlich False - die
            # Ursache der frueher zu Unrecht als Fehlschlag gewerteten Verifikation.
            device = await connect_and_refresh(dev_conf, with_capabilities=True)
        except RuntimeError as exc:
            print(t("dev_verify_failed", name, exc))
            return False

        print(t("dev_status_after", name, device.power_state,
                _mode_label(device.operational_mode), device.ieco, device.eco))

        if not device.ieco:
            print(t("dev_still_disabled", name))
            return False

        print(t("dev_ok", name))
        return True

    finally:
        await close_device(device)


def _device_config_problem(d: dict) -> str | None:
    """Prueft einen (bereits als Objekt bekannten) Geraeteeintrag auf die zum
    Verbinden zwingend noetigen Felder. Gibt einen Klartext-Grund zurueck, wenn
    das Geraet NICHT ansteuerbar ist, sonst None.

    Geprueft werden genau die Felder, die connect_and_refresh AUSSERHALB seines
    try-Blocks liest: die Pflichtfelder name/ip/id (dev_conf["name"] sowie
    AC(ip=..., device_id=int(dev_conf["id"]))) und das optionale port
    (int(dev_conf.get("port", 6444))). Ein fehlendes Pflichtfeld oder eine
    nicht-numerische id/port wuerde dort mit einem ungefangenen KeyError/
    ValueError/TypeError den ganzen Lauf abbrechen. token/key werden hier bewusst
    NICHT geprueft: fehlen sie, meldet der Verbindungsaufbau das ohnehin als
    sauberen Fehlschlag (im try -> RuntimeError -> Exit 2)."""
    for key in ("name", "ip", "id"):
        if key not in d:
            return t("cfgchk_missing_field", key)
    try:
        int(d["id"])
    except (TypeError, ValueError):
        return t("cfgchk_id_not_numeric", repr(d["id"]))
    # port ist optional (Default 6444), wird aber - wenn vorhanden - ebenfalls
    # ausserhalb des try mit int() konvertiert; ein nicht-numerischer/leerer/
    # None-Wert wuerde dort ungefangen abbrechen. Fehlt der Schluessel, greift
    # der Default 6444 - dann gibt es nichts zu pruefen.
    if "port" in d:
        try:
            int(d["port"])
        except (TypeError, ValueError):
            return t("cfgchk_port_not_numeric", repr(d["port"]))
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog=CMD_MAIN,
        description=t("cli_description"),
    )
    parser.add_argument(
        "target",
        nargs="?",
        help=t("cli_help_target"),
    )
    parser.add_argument(
        "--only-if-on",
        action="store_true",
        help=t("cli_help_only_if_on"),
    )
    args = parser.parse_args()

    # Discoverability zuerst und OHNE Netzwerk: kein Argument oder das
    # reservierte 'list' zeigt nur die Uebersicht (Beispiele + konfigurierte
    # Geraete) und endet erfolgreich - bewusst VOR load_config(), damit die
    # Uebersicht auch bei fehlender/kaputter devices.json funktioniert und
    # niemals eine Verbindung aufbaut. --only-if-on ist hier bedeutungslos.
    if args.target is None or args.target == TARGET_LIST:
        print_overview()
        sys.exit(0)

    config = load_config()
    # Nicht-Objekt-Eintraege (nur durch Hand-Edit moeglich) koennen keine
    # Geraete sein: klar melden und ueberspringen, statt spaeter im
    # Steuerungspfad mit einem TypeError abzubrechen (konsistent zur Uebersicht).
    dict_entries = [d for d in config["devices"] if isinstance(d, dict)]
    nondict = len(config["devices"]) - len(dict_entries)
    if nondict:
        print(t("main_skipped_entries", nondict, CONFIG_PATH.name))

    # Objekt-Eintraege ohne die zum Verbinden noetigen Felder (name/ip/id)
    # wuerden spaeter in connect_and_refresh mit einem ungefangenen KeyError/
    # ValueError abbrechen - vorab pruefen, benennen und ueberspringen
    # (symmetrisch zur Uebersicht).
    devices = []
    for d in dict_entries:
        problem = _device_config_problem(d)
        if problem is None:
            devices.append(d)
        else:
            label = d["name"] if isinstance(d.get("name"), str) else t("ov_unnamed")
            print(t("main_incomplete_entry", repr(label), CONFIG_PATH.name, problem))

    if args.target != TARGET_ALL:
        devices = [d for d in devices if d.get("name") == args.target]
        if not devices:
            print(t("main_device_not_found", args.target))
            sys.exit(1)
    elif not devices:
        # 'all' auf leerer Geraeteliste: all([]) waere True und wuerde
        # faelschlich "Gesamtergebnis: OK." (Exit 0) melden und damit eine
        # leere/kaputte devices.json maskieren. Bewusst klar abbrechen -
        # konsistent zum Schwestermodul midea_refresh_tokens.py, das bei leerem
        # --all ebenfalls mit Exit 1 endet -, damit eine Fehlkonfiguration auch
        # im stillen Cron-Lauf sichtbar wird.
        print(t("main_no_devices_configured"))
        sys.exit(1)

    results = []
    for d in devices:
        results.append(await ensure_ieco(d, only_if_on=args.only_if_on))
        await asyncio.sleep(1.0)

    if all(results):
        print(t("main_result_ok"))
        sys.exit(0)
    else:
        print(t("main_result_error"))
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
