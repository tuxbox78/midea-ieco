#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_ieco_ensure.py

Stellt sicher, dass iECO auf einer oder mehreren Midea-Klimaanlagen aktiv ist.

Mit --ensure-isense wird zusaetzlich versucht, iSense (in msmart-ng als
"Follow Me" bezeichnet) aktiv zu halten. Dieser Teil ist bewusst Best Effort:
Nicht jedes Geraet meldet den Zustand verlaesslich zurueck, und es gibt keine
separate Capability dafuer.

Standardverhalten: Schaltet das Geraet bei Bedarf ein UND aktiviert iECO.
Mit --only-if-on: Schaltet NICHTS ein. Nur wenn das Geraet bereits laeuft,
wird iECO bei Bedarf nachgezogen. Ist es aus, wird sofort abgebrochen,
OHNE zusaetzliche Netzwerkabfragen (kein get_capabilities()-Aufruf).

Die Ausgabe ist zweisprachig (Englisch als Default, Deutsch bei deutscher
Locale oder MIDEA_IECO_LANG=de) - siehe midea_i18n.py.

Nutzung:
    python3 midea_ieco_ensure.py <device_name|all>
    python3 midea_ieco_ensure.py <device_name|all> --only-if-on
    python3 midea_ieco_ensure.py <device_name|all> --ensure-isense
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import json
import random
import sys
import logging
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

# Sprachwahl: gemeinsame Mechanik fuer beide Werkzeuge (Reihenfolge und
# englischer Default sind dort dokumentiert). resolve_lang wird mitimportiert,
# damit es weiterhin ueber dieses Modul erreichbar bleibt.
from midea_i18n import (install_excepthook_redaction,
                        install_line_buffered_stdout, install_log_redaction,
                        make_translator, resolve_lang)  # noqa: F401

# Verbindungsabbau: msmart-ng hat dafuer keine oeffentliche API - die Begruendung
# und der gemessene Befund stehen in midea_conn.py. Bis dorthin ausgelagert,
# damit der Griff in fremde Interna an EINER Stelle steht und dort gepruefte
# Waechter hat.
#
# PINNED_MSMART_VERSION kommt aus demselben Modul und wird NICHT hier kopiert:
# es gibt genau eine Fassung, gegen die dieses Projekt nachgemessen hat, und
# zwei Konstanten mit demselben Zweck laufen frueher oder spaeter auseinander.
from midea_conn import PINNED_MSMART_VERSION, close_connection
# Property-only iECO-Write fuer den Fall, dass NICHT eingeschaltet werden muss
# (Anlage laeuft, nur iECO nachziehen): sendet ausschliesslich die iECO-Property,
# kein Zustandsbefehl - power/temp/mode koennen so gar nicht geclobbert werden.
# An echter Hardware belegt (beide Anlagen: Write angenommen, power/mode/target
# unveraendert); faellt bei fehlendem Pfad auf apply() zurueck (siehe midea_apply).
from midea_apply import apply_ieco_only

if TYPE_CHECKING:
    # Nur fuer Typpruefer: der echte Import passiert lazy in
    # connect_and_refresh, damit das Modul - und damit die netzwerkfreie
    # Uebersicht `list` - auch ohne installiertes msmart importierbar bleibt.
    from msmart.device.AC.device import AirConditioner as AC

# Frueher stand hier logging.basicConfig(level=logging.WARNING). Die Ablosung
# steht jetzt in main(): der Root-Logger gehoert dem Prozess, und ein blosser
# Import soll ihn nicht umkonfigurieren (siehe install_log_redaction).

# Modul-Logger fuer den EINEN Fall, der keine Geraetemeldung ist, sondern eine
# Aussage ueber die Bibliothek: bricht msmart-ngs Capability-Vertrag, gehoert
# das in denselben Kanal wie die Warnungen aus midea_conn.py (stderr, ab
# WARNING, durch die Schwaerzung von install_log_redaction) - und nicht in den
# Geraete-Meldungsstrom auf stdout, den Nutzer als Statusbericht lesen.
_LOGGER = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "devices.json"
CONNECT_RETRIES = 3
ACTION_RETRIES = 3
RETRY_DELAY = 3.0

# Additiver Jitter auf RETRY_DELAY: der tatsaechliche Nachlauf zwischen zwei
# Verbindungsversuchen ist RETRY_DELAY + rand[0, RETRY_JITTER]. BEWUSST NUR
# additiv (nie unter RETRY_DELAY): die Untergrenze ist die Ruhe, die eine Anlage
# mit nur EINER lokalen Verbindung zwischen zwei Sitzungen braucht - der Jitter
# darf sie strecken, nie unterschreiten. Zweck ist die Entzerrung, wenn im
# 'all'-Lauf mehrere Geraete gleichzeitig ausfallen (sonst schlafen alle exakt
# RETRY_DELAY und treffen die Wiederverbindung im Gleichtakt) sowie ueber
# Cron-Zyklen hinweg (Best Practice: "always use jitter"). Als Konstante, damit
# sich der Nachlauf - wie SETTLE_DELAY/DEVICE_DELAY - gegen sein Band pruefen
# laesst.
RETRY_JITTER = 1.0

# Nachlauf zwischen apply() und der Verifikation. Das Geraet uebernimmt den
# gesetzten Zustand nicht sofort; wird zu frueh nachgelesen, meldet es noch den
# alten Wert und der Lauf gaelte faelschlich als fehlgeschlagen.
#
# Die Pause liegt NACH dem Schliessen der Verbindung und VOR dem erneuten
# Verbindungsaufbau (siehe ensure_ieco). Sie ist damit zugleich die Ruhephase
# zwischen zwei Sitzungen - bei einem Geraet, das nur EINE lokale Verbindung
# vertraegt, ist das kein Detail.
SETTLE_DELAY = 2.0

# Pause nach jedem Geraet. Anders als im Schwestermodul (dort CANDIDATE_DELAY/
# DEVICE_DELAY jeweils VOR dem naechsten Zugriff) liegt sie hier bewusst NACH
# jedem Geraet, also auch nach dem letzten: der Lauf endet damit nicht unmittelbar
# auf einem geschlossenen Socket, was dem naechsten Cron-Durchgang bzw. einem
# direkt folgenden manuellen Aufruf zugutekommt. Als Konstante benannt, damit
# sich der Wert - wie im Schwestermodul - gegen eine Untergrenze pruefen laesst.
DEVICE_DELAY = 1.0

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
    "ov_example_isense": (
        "  %s %s --only-if-on --ensure-isense  also keep iSense enabled",
        "  %s %s --only-if-on --ensure-isense  auch iSense aktiv halten"),
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
    # Keine Geraetemeldung, sondern ein Bibliotheksbefund - geht deshalb ueber
    # _LOGGER nach stderr, nicht in den Statusbericht auf stdout.
    "prime_failed": (
        "WARNING: msmart-ng did not accept the iECO capability override "
        "(%s: %s; verified against msmart-ng %s). The verification round falls "
        "back to reporting an unclear result - please report this.",
        "WARNUNG: msmart-ng hat die Uebernahme der iECO-Faehigkeit nicht "
        "angenommen (%s: %s; geprueft gegen msmart-ng %s). Die Verifikation "
        "meldet daraufhin ein unklares Ergebnis - bitte melden."),
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
    "dev_status_before_isense": (
        "[%s] Status before action: power=%s, mode=%s, ieco=%s, isense=%s, "
        "eco=%s",
        "[%s] Status vor Aktion: power=%s, mode=%s, ieco=%s, isense=%s, "
        "eco=%s"),
    # Bewusst "no usable answer" statt "did not answer": die Abfrage kann aus
    # zwei Bloecken bestehen, und geht nur der zweite verloren, HAT die Anlage
    # geantwortet - verwertbar ist das Ergebnis trotzdem nicht. Der Text darf
    # nur behaupten, was in beiden Faellen gilt.
    "dev_caps_no_answer": (
        "[%s] ERROR: the capability query produced no usable answer - whether "
        "the unit supports iECO is unknown. Please try again in a few minutes.",
        "[%s] FEHLER: Die Capability-Abfrage hat keine verwertbare Antwort "
        "ergeben - ob die Anlage iECO kann, ist damit offen. Bitte in einigen "
        "Minuten erneut versuchen."),
    "dev_no_ieco_capability": (
        "[%s] ERROR: Device reports no iECO capability.",
        "[%s] FEHLER: Geraet meldet keine iECO-Faehigkeit."),
    "dev_already_desired": (
        "[%s] Already in the desired state (on, iECO active). No further "
        "queries necessary.",
        "[%s] Bereits im gewuenschten Zustand (an, iECO aktiv). Keine weiteren "
        "Abfragen notwendig."),
    "dev_already_desired_isense": (
        "[%s] Already in the desired state (on, iECO and iSense active). No "
        "further queries necessary.",
        "[%s] Bereits im gewuenschten Zustand (an, iECO und iSense aktiv). "
        "Keine weiteren Abfragen notwendig."),
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
    "dev_mode_isense_only_ok": (
        "[%s] --only-if-on is active: iECO was not changed, not an error.",
        "[%s] --only-if-on aktiv: iECO wurde nicht geaendert, kein Fehler."),
    "dev_mode_isense_only_fail": (
        "[%s] iECO was not switched. Please set the unit to %s and call again.",
        "[%s] iECO wurde nicht geschaltet. Bitte die Anlage auf %s stellen und "
        "erneut aufrufen."),
    "dev_isense_attempted": (
        "[%s] iSense (Follow Me) enable command sent (best effort; readback is "
        "not required).",
        "[%s] iSense-(Follow-Me-)Einschaltbefehl gesendet (Best Effort; eine "
        "Rueckmeldung ist nicht erforderlich)."),
    "dev_isense_attempt_failed": (
        "[%s] WARNING: iSense could not be enabled after retries (%s); "
        "continuing because iSense is best effort.",
        "[%s] WARNUNG: iSense konnte nach den Wiederholungen nicht aktiviert "
        "werden (%s); der Lauf wird fortgesetzt, weil iSense Best Effort ist."),
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
    "dev_verify_no_answer": (
        "[%s] ERROR: the unit sent no state back for the verification - it is "
        "unclear whether iECO was applied. Please try again in a few minutes.",
        "[%s] FEHLER: Die Anlage hat fuer die Verifikation keinen Zustand "
        "zurueckgeliefert - ob iECO uebernommen wurde, ist damit offen. Bitte "
        "in einigen Minuten erneut versuchen."),
    "dev_verify_inconclusive": (
        "[%s] ERROR: the iECO capability could not be carried into the "
        "verification - whether iECO was applied is therefore open. Please try "
        "again in a few minutes.",
        "[%s] FEHLER: Die iECO-Faehigkeit liess sich nicht in die Verifikation "
        "uebernehmen - ob iECO uebernommen wurde, ist damit offen. Bitte in "
        "einigen Minuten erneut versuchen."),
    "dev_status_after": (
        "[%s] Status after action: power=%s, mode=%s, ieco=%s, eco=%s",
        "[%s] Status nach Aktion: power=%s, mode=%s, ieco=%s, eco=%s"),
    "dev_status_after_isense": (
        "[%s] Status after action: power=%s, mode=%s, ieco=%s, isense=%s, "
        "eco=%s",
        "[%s] Status nach Aktion: power=%s, mode=%s, ieco=%s, isense=%s, "
        "eco=%s"),
    "dev_isense_ok": (
        "[%s] iSense is active (reported by the device).",
        "[%s] iSense ist aktiv (vom Geraet gemeldet)."),
    "dev_isense_unconfirmed": (
        "[%s] iSense was requested, but the device does not report it as "
        "active; continuing because iSense is best effort.",
        "[%s] iSense wurde angefordert, das Geraet meldet es jedoch nicht als "
        "aktiv; der Lauf wird fortgesetzt, weil iSense Best Effort ist."),
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
    "cli_help_ensure_isense": (
        "Also try to keep iSense (Follow Me) enabled. Experimental best effort: "
        "requires a full-state write and does not require positive readback.",
        "Zusaetzlich versuchen, iSense (Follow Me) aktiv zu halten. "
        "Experimentelles Best Effort: erfordert einen Vollzustands-Write und "
        "keine positive Rueckmeldung."),
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
    print(t("ov_example_isense", CMD_MAIN, TARGET_ALL))
    print(t("ov_example_list", CMD_MAIN, TARGET_LIST))
    print(t("ov_example_refresh", CMD_REFRESH))
    print(t("ov_example_update", CMD_UPDATE))
    print("")
    print(t("ov_full_options", CMD_MAIN))
    print(t("ov_path_note", CMD_MAIN))


def prime_ieco_property(device: "AC") -> bool:
    """Traegt die iECO-Faehigkeit direkt in ein frisches Geraeteobjekt ein,
    statt sie erneut beim Geraet zu erfragen.

    WOZU
    ----
    msmart-ngs refresh() pollt die IECO-Property nur, wenn sie in
    _supported_properties steht - und dort landet sie normalerweise erst durch
    get_capabilities(). Geht dieser Roundtrip verloren, liefert device.ieco den
    Default False, OHNE dass irgendetwas gescheitert waere: get_capabilities()
    wirft nicht, es loggt und kehrt zurueck (msmart/device/AC/device.py:602-611).
    Die Verifikationsrunde meldete daraufhin "iECO ist laut Geraet weiterhin
    deaktiviert" - eine Ursachenaussage ueber ein Geraet, das dazu nichts gesagt
    hat, und das ueber eine Einstellung, die es zuvor angenommen hatte.

    override_capabilities() ist msmart-ngs eigener, oeffentlicher Weg dafuer;
    die AC-Fassung ruft intern _update_supported_properties() (device.py:785-791)
    und traegt PropertyId.IECO damit genauso ein wie eine geglueckte
    Capability-Abfrage. Das folgende refresh() pollt die Property dann
    unabhaengig davon, ob ein Capability-Austausch durchkommt - der Fehlerfall
    verschwindet strukturell statt erkannt zu werden, und die Runde spart
    zugleich einen bis zwei Roundtrips auf einer Anlage, die genau EINE lokale
    Verbindung vertraegt.

    WANN ES ERLAUBT IST
    -------------------
    NUR nachdem das Geraet im selben Lauf selbst gemeldet hat, dass es iECO
    kann. In ensure_ieco ist das gesichert: der supports_ieco-Waechter bricht
    vorher ab, die Verifikationsrunde wird sonst gar nicht erreicht. Vor jenem
    Waechter waere die Mitnahme eine Behauptung statt einer Erinnerung - dort
    muss weiterhin get_capabilities() gefragt werden.

    merge=True ist tragend: mit merge=False ersetzte der Aufruf den gesamten
    Flag-Satz durch IECO allein und loeschte damit Capability.DEFAULT.

    Rueckgabe:
        True, wenn die Faehigkeit danach tatsaechlich gesetzt ist.

    Geprueft wird der EFFEKT, nicht der Aufruf. Ein override_capabilities, das
    durchlaeuft ohne zu wirken - etwa weil eine kuenftige Fassung
    _update_supported_properties() nicht mehr ausloest -, waere genau die stille
    Wirkungslosigkeit, gegen die midea_conn.py angetreten ist. Der Aufrufer darf
    den Wert ignorieren; ensure_ieco liest stattdessen device.supports_ieco, weil
    das dieselbe Frage am Objekt selbst stellt.

    Wirft nie: die Funktion laeuft innerhalb der Wiederholschleife von
    connect_and_refresh, und eine Ausnahme von hier wuerde dort als
    Verbindungsfehler gedeutet - der Lauf meldete "Verbindung fehlgeschlagen
    nach 3 Versuchen" und damit erneut eine falsche Ursache."""
    try:
        device.override_capabilities(
            {"additional_capabilities": ["IECO"]}, merge=True)
    except Exception as exc:  # noqa: BLE001 - siehe Docstring: nie eskalieren
        _LOGGER.warning(t("prime_failed", type(exc).__name__, exc,
                          PINNED_MSMART_VERSION))
        return False
    return bool(getattr(device, "supports_ieco", False))


def _retry_delay() -> float:
    """Nachlauf zwischen zwei Verbindungsversuchen: RETRY_DELAY plus additiver
    Jitter (siehe RETRY_JITTER). Als eigene Funktion, damit beide Retry-Stellen
    exakt dieselbe Berechnung nutzen und Tests den Wert gegen sein Band pruefen
    koennen."""
    return RETRY_DELAY + random.uniform(0.0, RETRY_JITTER)


async def connect_and_refresh(dev_conf: dict, retries: int = CONNECT_RETRIES,
                              prime_ieco: bool = False) -> AC:
    """Verbindet, authentifiziert und liest den Live-Status.

    Standardmaessig OHNE jede Capability-Behandlung - fuer eine reine Status-/
    Power-Abfrage (z.B. den --only-if-on-Schnellpfad) ist das unnoetig.

    Der Hintergrund, warum es den prime_ieco-Schalter unten ueberhaupt braucht:
    msmart-ngs refresh() pollt nur Properties aus _supported_properties, und die
    IECO-Property landet dort erst, wenn die Faehigkeit bekannt ist. Fehlt sie, pollt
    refresh() die Property NICHT, und device.ieco liefert immer den Default
    False - selbst wenn iECO am Geraet aktiv ist. Genau das liess die
    Verifikation frueher faelschlich fehlschlagen.

    Mit prime_ieco=True wird die bereits bekannte iECO-Faehigkeit vor refresh()
    in das frische Objekt eingetragen (siehe prime_ieco_property) - ohne
    Netzzugriff. Das ist der Weg der Verifikationsrunde: dort ist die Faehigkeit
    im selben Lauf schon belegt, ein zweiter Capability-Roundtrip waere nur eine
    weitere Gelegenheit, verloren zu gehen.

    Einen with_capabilities-Schalter gab es hier frueher ebenfalls. Er ist
    entfallen, als die Verifikation auf die Mitnahme umgestellt wurde: der
    einzige verbliebene Ort, an dem wirklich GEFRAGT werden muss, ist der
    Initial-Read in ensure_ieco - und der braucht den Rueckgabewert von
    device.online zwischen get_capabilities() und refresh(), den diese Funktion
    nicht durchreichen kann. Ein Schalter, den niemand mehr setzt, waere ein
    Zweig, den Tests gruen halten und den nie jemand ausfuehrt."""
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
            # Die Mitnahme steht bewusst VOR authenticate() und damit vor jedem
            # Netzzugriff: sie ist eine rein lokale Eintragung, kann also nicht
            # scheitern, weil das Geraet schweigt. Sie muss INNERHALB der
            # Schleife liegen, weil jeder Versuch ein frisches AC-Objekt baut -
            # und damit auch dessen leere Capability-Flags neu entstehen.
            if prime_ieco:
                prime_ieco_property(device)
            await device.authenticate(dev_conf["token"], dev_conf["key"])
            await device.refresh()
            return device
        except Exception as exc:
            last_exc = exc
            close_connection(device)
            print(t("conn_attempt_failed", name, attempt, retries,
                    type(exc).__name__, exc))
            if attempt < retries:
                await asyncio.sleep(_retry_delay())
    raise RuntimeError(t("conn_gave_up", name, retries)) from last_exc


class _Prep(enum.Enum):
    """Ausgang von _validate_and_arm."""
    READY = "ready"          # iECO (+ optional iSense) -> apply + verify
    ISENSE_ONLY_OK = "isense_only_ok"      # nur iSense schreiben -> True
    ISENSE_ONLY_FAIL = "isense_only_fail"  # nur iSense schreiben -> False
    DONE_OK = "done_ok"      # terminaler Erfolg, Meldung gedruckt -> True
    DONE_FAIL = "done_fail"  # terminaler Fehlschlag, Meldung gedruckt -> False


class _ArmResult(NamedTuple):
    prep: _Prep
    #: Nur bei READY aussagekraeftig: True, wenn ein voller Zustandsrahmen
    #: noetig ist. Das gilt beim Einschalten UND fuer iSense, weil Follow Me im
    #: klassischen SetStateCommand steckt und keine eigene Property besitzt.
    #: Nur ein reines iECO-Nachziehen darf den property-only-Pfad nehmen.
    needs_full_state: bool = False


async def _validate_and_arm(device: "AC", only_if_on: bool, name: str,
                            ensure_isense: bool) -> _ArmResult:
    """Prueft ein frisch verbundenes Geraet und schaltet es fuer apply() scharf.

    DIESELBE Sequenz laeuft beim Erstversuch UND bei jedem Reconnect im
    apply-Retry. Damit kann der Wiederholpfad nie einen ungeprueften (womoeglich
    auf __init__-Defaults stehenden) Zustand anwenden - der Kern der Reparatur:
    'auf jedem Versuch frisch lesen und neu validieren' statt eines abgespeckten
    Re-Arms (Muster reconcile / RetryOnConflict: refetch-on-retry).

    Alle Geraetemeldungen werden HIER gedruckt; der Aufrufer wertet nur den
    _Prep-Wert aus (kein Doppeldruck). Bei READY werden device.power_state,
    device.ieco und optional device.follow_me mutiert. iSense ist bewusst NICHT
    capability-gated: genau diese Capability gibt es in msmart-ng nicht, und
    ein False ist laut Issue #5 nicht von "nicht gemeldet" zu unterscheiden.
    Wir versuchen den vorhandenen Follow-Me-Schreiber deshalb auf Wunsch
    einfach und werten nur iECO streng.

    Wirft nicht ausser bei einem Dekodierfehler in get_capabilities()/refresh(),
    der - wie zuvor an der Aufrufstelle - als DONE_FAIL abgefangen wird."""
    if not device.online:
        print(t("dev_not_online", name))
        return _ArmResult(_Prep.DONE_FAIL)

    is_on = device.power_state

    # Fruehzeitiger Ausstieg VOR jeder teuren Capability-Abfrage:
    # Ist das Geraet aus und duerfen wir es per --only-if-on nicht
    # einschalten, ist alles gesagt. power_state kommt aus refresh() und ist
    # ohne get_capabilities() korrekt; der ieco-Zustand spielt hier keine
    # Rolle - also kein get_capabilities(), kein apply(), keine Netzwerklast.
    if only_if_on and not is_on:
        print(t("dev_off_only_if_on", name))
        return _ArmResult(_Prep.DONE_OK)

    # Ab hier brauchen wir den ECHTEN ieco-Zustand (fuer die Statusanzeige,
    # den 'schon aktiv'-Kurzschluss und spaeter die Verifikation). refresh()
    # pollt die IECO-Property aber nur nach get_capabilities() (das
    # _supported_properties befuellt) - sonst liest device.ieco immer den
    # Default False. Also Capabilities abfragen und danach erneut refreshen.
    try:
        await device.get_capabilities()
        # Der Erfolg der Capability-Abfrage MUSS hier festgehalten werden,
        # vor dem refresh(): msmart-ng setzt _online pro Kommando-Batch neu
        # (device.py:573), das folgende refresh() ueberschreibt also ein
        # False der Capability-Runde wieder mit True. Danach ist nicht mehr
        # feststellbar, ob die Abfrage jemals beantwortet wurde.
        #
        # Der except-Block darunter faengt diesen Fall NICHT: bei
        # Paketverlust wirft get_capabilities() nicht, es loggt "Failed to
        # query capabilities" und kehrt zurueck (device.py:602-611). Er
        # bleibt trotzdem noetig - er deckt Dekodierfehler ab, die msmart
        # nicht abfaengt (nachgemessen: ein pruefsummen- und CRC-gueltiges,
        # aber zu kurzes Capabilities-Frame ergibt einen IndexError).
        caps_answered = device.online
        await device.refresh()
    except Exception as exc:
        print(t("dev_caps_failed", name, type(exc).__name__, exc))
        return _ArmResult(_Prep.DONE_FAIL)

    # Dieser eine Fall MUSS vor der Statuszeile abbiegen: ohne verwertbare
    # Capability-Antwort ist device.ieco der Default eines frischen Objekts
    # (Capability.DEFAULT enthaelt IECO nicht, device.py:149-154), und die
    # Statuszeile meldete einen Wert, den die Anlage nie genannt hat -
    # ausgerechnet in der Zeile direkt ueber der Fehlermeldung.
    #
    # Alle ANDEREN Faelle bekommen die Statuszeile: meldet die Anlage
    # schlicht kein iECO, sind power/mode/eco gemessene Werte und als
    # Diagnose wertvoll - und ihr ieco=False ist dann kein Platzhalter,
    # sondern die zutreffende Aussage ueber ein Geraet ohne diese Faehigkeit.
    if not device.supports_ieco and not caps_answered:
        print(t("dev_caps_no_answer", name))
        return _ArmResult(_Prep.DONE_FAIL)

    isense_active = bool(getattr(device, "follow_me", False))
    needs_isense = ensure_isense and not isense_active
    if ensure_isense:
        print(t("dev_status_before_isense", name, is_on,
                _mode_label(device.operational_mode), device.ieco,
                isense_active, device.eco))
    else:
        print(t("dev_status_before", name, is_on,
                _mode_label(device.operational_mode), device.ieco, device.eco))

    if not device.supports_ieco:
        # Die Anlage HAT geantwortet und iECO nicht gemeldet - erst jetzt
        # ist die Aussage ueber ihre Faehigkeiten gedeckt.
        print(t("dev_no_ieco_capability", name))
        return _ArmResult(_Prep.DONE_FAIL)

    # Kurzschluss BEWUSST vor dem Modus-Guard: laeuft iECO bereits, ist alles
    # gut - unabhaengig davon, was wir ueber den Modus annehmen. So kann eine
    # zu enge Modus-Liste niemals einen tatsaechlich funktionierenden Zustand
    # als Problem melden.
    if is_on and device.ieco:
        if not needs_isense:
            if ensure_isense:
                print(t("dev_already_desired_isense", name))
            else:
                print(t("dev_already_desired", name))
            return _ArmResult(_Prep.DONE_OK)

        # iECO ist bereits sicher im Soll; der einzige ausstehende Write ist
        # iSense. Er bekommt deshalb denselben Best-Effort-Ausgang wie der
        # iSense-Nachzug im nicht-iECO-faehigen Modus: weder fehlender Readback
        # noch ein Sendefehler darf aus dem bereits erfolgreichen iECO-Zustand
        # einen Gesamtfehler machen.
        device.follow_me = True
        return _ArmResult(_Prep.ISENSE_ONLY_OK, needs_full_state=True)

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

        # iSense ist nicht an den iECO-Modus gekoppelt. Bei einer bereits
        # laufenden Anlage senden wir das Follow-Me-Bit deshalb auch dann, wenn
        # iECO in diesem Modus unmoeglich ist. Das Gesamtergebnis bleibt aber
        # identisch zur bisherigen iECO-Semantik (Cron: OK, expliziter Aufruf:
        # Fehler), und iSense wird nicht verifiziert.
        if needs_isense and is_on:
            device.follow_me = True
            prep = (_Prep.ISENSE_ONLY_OK if only_if_on
                    else _Prep.ISENSE_ONLY_FAIL)
            return _ArmResult(prep, needs_full_state=True)

        if only_if_on:
            # Ein bewusst gewaehlter Modus ist kein Fehlerzustand. Im
            # 20-Minuten-Cron wuerde ein Fehlschlag hier taeglich 72 Meldungen
            # und Exit 2 erzeugen - konsistent zum ausgeschalteten Geraet wird
            # das deshalb als "nichts zu tun" gewertet.
            print(t("dev_mode_only_if_on", name))
            return _ArmResult(_Prep.DONE_OK)
        # Beim ausdruecklichen Aufruf wollte der Nutzer iECO. Nichts schalten
        # (auch nicht einschalten - eine halbe Aktion ohne iECO waere nicht das
        # Gewuenschte), aber klar sagen, was zu tun ist, und den Lauf als
        # nicht erfolgreich werten.
        print(t("dev_mode_nothing_switched", name, t("mode_names_capable")))
        return _ArmResult(_Prep.DONE_FAIL)

    was_off = not is_on
    if was_off:
        device.power_state = True
    if not device.ieco:
        device.ieco = True
    if needs_isense:
        device.follow_me = True
    return _ArmResult(_Prep.READY,
                      needs_full_state=was_off or needs_isense)


async def ensure_ieco(dev_conf: dict, only_if_on: bool,
                      ensure_isense: bool = False) -> bool:
    name = dev_conf["name"]

    try:
        device = await connect_and_refresh(dev_conf)
    except RuntimeError as exc:
        print(t("dev_error", name, exc))
        return False

    try:
        result = await _validate_and_arm(device, only_if_on, name,
                                         ensure_isense)
        applied = False
        last_exc = None
        for attempt in range(1, ACTION_RETRIES + 1):
            # Terminale Ergebnisse der Validierung - egal ob Erstlauf oder
            # Reconnect-Neuvalidierung; die Meldung ist in _validate_and_arm
            # bereits gedruckt. DONE_OK heisst 'nichts (mehr) zu tun' (bereits
            # im Sollzustand, oder unter --only-if-on aus).
            if result.prep is _Prep.DONE_OK:
                return True
            if result.prep is _Prep.DONE_FAIL:
                return False

            # READY schreibt iECO (+ optional iSense) und verifiziert danach
            # iECO. ISENSE_ONLY_* entsteht, wenn der aktuelle Modus iECO nicht
            # tragen kann: iSense wird trotzdem best-effort geschrieben, ohne
            # das bestehende Ergebnis fuer diesen iECO-Fall zu veraendern.
            try:
                # Muss NICHT eingeschaltet werden (Anlage laeuft, es ist nur iECO
                # nachzuziehen)? Dann iECO als REINEN Property-Write senden -
                # power/temp/mode sind physisch nicht im Frame und koennen nicht
                # geclobbert werden (an beiden Anlagen on-device belegt). Liefert
                # apply_ieco_only False (Pfad nicht verfuegbar), nehmen wir das
                # volle apply() - durch den Reconcile ohnehin gegen den Clobber
                # abgesichert. needs_full_state=True (Anlage war aus ODER
                # iSense muss gesetzt werden) braucht den Zustandsbefehl sowieso
                # (kurzschluss: apply_ieco_only wird dann gar nicht aufgerufen).
                isense_only = result.prep in {
                    _Prep.ISENSE_ONLY_OK, _Prep.ISENSE_ONLY_FAIL}
                if (isense_only or result.needs_full_state
                        or not await apply_ieco_only(device)):
                    await device.apply()
                applied = True
                break
            except Exception as exc:
                last_exc = exc
                print(t("dev_apply_attempt_failed", name, attempt, ACTION_RETRIES,
                        type(exc).__name__, exc))
                if attempt < ACTION_RETRIES:
                    # Wie bei der Verifikation unten: ERST schliessen, DANN
                    # warten. So liegt die Pause zwischen den Sitzungen und
                    # nicht innerhalb einer noch offenen.
                    close_connection(device)
                    await asyncio.sleep(_retry_delay())
                    # Fuer den naechsten Versuch eine FRISCHE Verbindung
                    # aufbauen (ein fehlgeschlagener Versuch kann das AC-Objekt
                    # mit defektem Socket-Zustand hinterlassen). Scheitert schon
                    # der Reconnect selbst, ist ein weiterer Versuch zwecklos -
                    # das Retry-Budget steckt bereits in connect_and_refresh
                    # (drei interne Versuche); hier sauber abbrechen.
                    try:
                        device = await connect_and_refresh(dev_conf)
                    except Exception as exc2:
                        last_exc = exc2
                        print(t("dev_reconnect_failed", name,
                                type(exc2).__name__, exc2))
                        break
                    # RECONCILE - der Kern der Reparatur: das FRISCHE Objekt
                    # vollstaendig neu lesen, validieren und scharfschalten,
                    # statt eines abgespeckten Re-Arms auf womoeglich
                    # __init__-Defaults. So greift u.a. die --only-if-on-Aus-
                    # Weiche auch nach einem Reconnect, dessen refresh() den
                    # Zustand nicht gefuellt hat (dann is_on=False -> DONE_OK,
                    # kein apply, kein power_on=False an eine laufende Anlage);
                    # ein bereits gesetzter Zustand wird als 'schon erreicht'
                    # erkannt statt blind erneut geschrieben. Ein Dekodierfehler
                    # in get_capabilities()/refresh() kommt als DONE_FAIL zurueck
                    # (kein Traceback), sodass ein Geraet den 'all'-Lauf nicht
                    # abbricht.
                    result = await _validate_and_arm(
                        device, only_if_on, name, ensure_isense)

        if not applied:
            if result.prep is _Prep.ISENSE_ONLY_OK:
                print(t("dev_isense_attempt_failed", name, last_exc))
                return True
            print(t("dev_apply_failed", name, last_exc))
            return False

        if result.prep in {_Prep.ISENSE_ONLY_OK, _Prep.ISENSE_ONLY_FAIL}:
            # Kein Settle-/Verify-Roundtrip: Issue #5 verlangt ausdruecklich
            # Best Effort, weil False sowohl "aus" als auch "nicht gemeldet"
            # bedeuten kann. Der erfolgreich abgesetzte Vollzustandsbefehl ist
            # hier das Ende der iSense-Arbeit; die Rueckgabe bildet weiterhin
            # ausschliesslich die bisherige iECO-Modus-Semantik ab.
            print(t("dev_isense_attempted", name))
            if result.prep is _Prep.ISENSE_ONLY_OK:
                print(t("dev_mode_isense_only_ok", name))
                return True
            print(t("dev_mode_isense_only_fail", name,
                    t("mode_names_capable")))
            return False

        # Reihenfolge ist hier tragend: ERST schliessen, DANN warten, dann neu
        # verbinden. Damit faellt die Nachlaufpause in eine Zeit, in der das
        # Geraet gar keine Verbindung haelt - genau die Ruhe, die eine Anlage
        # braucht, die nur EINE lokale Verbindung vertraegt.
        #
        # Vorher stand das Schliessen NACH der Pause, und weil es zudem
        # wirkungslos war (siehe midea_conn.py), lief die Verifikation unten in
        # eine ZWEITE gleichzeitige Verbindung, waehrend die erste noch offen
        # war. Das Werkzeug konnte damit selbst das Verstummen erzeugen, das es
        # anschliessend als Geraetefehler meldete.
        close_connection(device)
        await asyncio.sleep(SETTLE_DELAY)
        try:
            # refresh() muss die IECO-Property pollen, sonst laese device.ieco
            # faelschlich False - die Ursache der frueher zu Unrecht als
            # Fehlschlag gewerteten Verifikation. Dafuer genuegt hier die
            # MITNAHME statt einer erneuten Abfrage: dass die Anlage iECO kann,
            # hat sie in diesem Lauf bereits gesagt (sonst haette der
            # supports_ieco-Waechter oben abgebrochen). Ein zweiter
            # Capability-Roundtrip waere nur eine weitere Gelegenheit, verloren
            # zu gehen - und genau darueber meldete der Lauf dann "iECO ist laut
            # Geraet weiterhin deaktiviert", obwohl das Setzen geglueckt war.
            device = await connect_and_refresh(dev_conf, prime_ieco=True)
        except RuntimeError as exc:
            print(t("dev_verify_failed", name, exc))
            return False

        # Dass connect_and_refresh zurueckkam, heisst NICHT, dass das Geraet
        # geantwortet hat: msmart-ng reicht Netzfehler aus refresh() und
        # get_capabilities() nicht nach oben, sondern faengt sie in
        # Device._send_command ab und liefert eine leere Antwortliste. Ohne
        # diese Pruefung stuenden hier die DEFAULTS des frischen AC-Objekts, und
        # der Lauf meldete "iECO ist laut Geraet weiterhin deaktiviert" - eine
        # Ursachenaussage ueber ein Geraet, das gar nichts gesagt hat.
        # device.online ist das beobachtbare Gegenstueck (msmart-ng setzt es auf
        # 'len(responses) > 0'), dasselbe Signal, das oben schon vor der Aktion
        # geprueft wird.
        if not device.online:
            print(t("dev_verify_no_answer", name))
            return False

        # Nach geglueckter Mitnahme ist supports_ieco per Konstruktion True
        # (prime_ieco_property prueft genau das nach). Ein False kann hier
        # deshalb NUR bedeuten, dass die Mitnahme nicht gewirkt hat - dann wurde
        # die IECO-Property nie gepollt und device.ieco traegt wieder den
        # Default. Ohne diesen Waechter fiele der Lauf still in genau die
        # Falschaussage zurueck, die die Mitnahme beseitigen soll.
        if not device.supports_ieco:
            print(t("dev_verify_inconclusive", name))
            return False

        if ensure_isense:
            isense_active = bool(getattr(device, "follow_me", False))
            print(t("dev_status_after_isense", name, device.power_state,
                    _mode_label(device.operational_mode), device.ieco,
                    isense_active, device.eco))
            if isense_active:
                print(t("dev_isense_ok", name))
            else:
                print(t("dev_isense_unconfirmed", name))
        else:
            print(t("dev_status_after", name, device.power_state,
                    _mode_label(device.operational_mode), device.ieco,
                    device.eco))

        if not device.ieco:
            print(t("dev_still_disabled", name))
            return False

        print(t("dev_ok", name))
        return True

    finally:
        close_connection(device)


def _device_config_problem(d: dict) -> str | None:
    """Prueft einen (bereits als Objekt bekannten) Geraeteeintrag auf die zum
    Verbinden zwingend noetigen Felder. Gibt einen Klartext-Grund zurueck, wenn
    das Geraet NICHT ansteuerbar ist, sonst None.

    Geprueft werden genau die Felder, die connect_and_refresh AUSSERHALB seines
    try-Blocks liest: die Pflichtfelder name/ip/id (dev_conf["name"] sowie
    AC(ip=..., device_id=int(dev_conf["id"]))) und das optionale port
    (int(dev_conf.get("port", 6444))). Ein fehlendes Pflichtfeld oder eine
    nicht-numerische id/port wuerde dort mit einem ungefangenen KeyError/
    ValueError/TypeError/OverflowError den ganzen Lauf abbrechen. token/key
    werden hier bewusst NICHT geprueft: fehlen sie, meldet der Verbindungsaufbau
    das ohnehin als sauberen Fehlschlag (im try -> RuntimeError -> Exit 2)."""
    for key in ("name", "ip", "id"):
        if key not in d:
            return t("cfgchk_missing_field", key)
    # int() auf dem ROHwert: Liste/dict/None -> TypeError, "abc" -> ValueError,
    # und weil Pythons json das blanke Literal Infinity/-Infinity klaglos als
    # float('inf') einliest, Infinity -> OverflowError (KEIN Subtyp von
    # ValueError). Alle drei muessen gefangen werden - sonst reisst genau der
    # OverflowError diese Vorab-Pruefung (und damit den ganzen Lauf) ab, statt das
    # Geraet sauber zu melden.
    try:
        int(d["id"])
    except (TypeError, ValueError, OverflowError):
        return t("cfgchk_id_not_numeric", repr(d["id"]))
    # port ist optional (Default 6444), wird aber - wenn vorhanden - ebenfalls
    # ausserhalb des try mit int() konvertiert; ein nicht-numerischer/leerer/
    # None-Wert (TypeError/ValueError) oder ein Infinity (-> OverflowError, siehe
    # id oben) wuerde dort ungefangen abbrechen. Fehlt der Schluessel, greift
    # der Default 6444 - dann gibt es nichts zu pruefen.
    if "port" in d:
        try:
            int(d["port"])
        except (TypeError, ValueError, OverflowError):
            return t("cfgchk_port_not_numeric", repr(d["port"]))
    return None


async def main() -> None:
    # stdout auf Zeilenpufferung, BEVOR irgendetwas ausgegeben wird: unter Cron
    # (>> log 2>&1) ist stdout sonst blockgepuffert und die eigene Statusausgabe
    # erschiene gesammelt am Prozessende, hinter der laufenden stderr-Diagnostik
    # der Bibliothek - Ursache und Wirkung vertauscht. Warum nur stdout, warum
    # hier: Docstring von install_line_buffered_stdout().
    install_line_buffered_stdout()
    # Beide Waechter erst hier, nicht beim Import: Root-Logger und
    # sys.excepthook gelten prozessweit, und ein blosser Import dieses Moduls
    # soll das Verhalten des Aufrufers nicht aendern. Sie decken die zwei Wege
    # ab, die der Meldungskatalog nicht sieht: was eine Fremdbibliothek ueber
    # logging schreibt (msmart-ng gibt auf WARNING rohe Empfangspuffer aus),
    # und eine zweite Ausnahme in einem except-Block, die die ganze Kette samt
    # der urspruenglichen Bibliotheksmeldung druckt.
    install_log_redaction(logging.WARNING)
    install_excepthook_redaction()
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
    parser.add_argument(
        "--ensure-isense",
        action="store_true",
        help=t("cli_help_ensure_isense"),
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
        # Den neuen Parameter nur bei gesetzter Option weiterreichen. Damit
        # bleibt der bisherige Aufrufvertrag fuer Einbettungen erhalten, die
        # ensure_ieco mit einer zweiparametrigen Test-/Adapterfunktion ersetzen;
        # der Produktions-Default ist ohnehin False.
        if args.ensure_isense:
            result = await ensure_ieco(
                d, only_if_on=args.only_if_on, ensure_isense=True)
        else:
            result = await ensure_ieco(d, only_if_on=args.only_if_on)
        results.append(result)
        # Entzerrung nach JEDEM Geraet (siehe DEVICE_DELAY) - auch nach dem
        # letzten, bewusst.
        await asyncio.sleep(DEVICE_DELAY)

    if all(results):
        print(t("main_result_ok"))
        sys.exit(0)
    else:
        print(t("main_result_error"))
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
