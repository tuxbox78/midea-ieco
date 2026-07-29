#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_refresh_tokens.py

Holt frische Token/Key-Paare fuer die konfigurierten Geraete und aktualisiert
devices.json. Verifiziert JEDES gefundene Token/Key-Paar mit einer echten
lokalen Verbindung zum Geraet, bevor es gespeichert wird.

Funktionsweise / warum OHNE Cloud-Zugangsdaten:
    Token und Key sind an die UDP-ID des jeweiligen GERAETS gebunden, nicht an
    ein Cloud-Konto. Sie werden ueber `python3 -m midealocal.cli discover
    --host <ip> --debug` bezogen. Die Token-Vergabe laeuft heute ausschliesslich
    ueber die NetHome-Plus-Cloud-API; die getToken-Endpunkte von MSmartHome und
    Meiju hat Midea serverseitig abgeschaltet (sie quittieren errorCode 3004
    "value is illegal" - am 2026-07-11 real gegen ein Geraet verifiziert). Ein
    EIGENES MSmartHome-Konto ist damit fuer den Token-Abruf nutzlos, und auf der
    NetHome-Plus-Cloud existiert es gar nicht. `midealocal` meldet sich deshalb
    mit seinem eingebauten NetHome-Plus-Konto an - genau das tut auch msmart-ng.
    Dieses Skript uebergibt der CLI daher BEWUSST keine Zugangsdaten (die frueher
    abgefragte credentials.json entfiel mit 0.2.0). Der eigentliche Wert liegt
    woanders: jeder Kandidat wird VOR dem Speichern gegen das Geraet verifiziert,
    und bestehende Werte werden nur nach erfolgreicher Verifikation ueberschrieben
    - faellt die Cloud-API eines Tages aus, bleiben die zuletzt gueltigen Tokens
    erhalten und die lokale Steuerung laeuft weiter.
    (Mechanik quellcode-identisch in midea-local 6.6.1 und 6.10.0; real
    verifiziert gegen die gepinnte 6.6.1 am 2026-07-11.)

Nutzung:
    python3 midea_refresh_tokens.py --name Wohnzimmer
    python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
    python3 midea_refresh_tokens.py --all
"""

# Wie in midea_ieco_ensure.py und midea_i18n.py: erlaubt PEP-604-Typangaben
# ("str | None") auch dort, wo sie zur Laufzeit ausgewertet wuerden. Ohne diesen
# Import war dieses Modul als einziges der drei nicht importierbar, sobald es
# mit einem aelteren Interpreter angefasst wurde (z.B. Apples System-Python 3.9),
# was eine Fehlersuche unnoetig in die Irre fuehrt. Das Projekt setzt 3.11+
# voraus - die Einheitlichkeit kostet nichts und nimmt die Stolperfalle weg.
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Verbindungsabbau: msmart-ng hat dafuer keine oeffentliche API - die Begruendung
# und der gemessene Befund stehen in midea_conn.py. Bis dorthin ausgelagert,
# damit der Griff in fremde Interna an EINER Stelle steht und dort gepruefte
# Waechter hat.
from midea_conn import close_connection

# Sprachwahl: gemeinsame Mechanik fuer beide Werkzeuge (Reihenfolge und
# englischer Default sind dort dokumentiert). resolve_lang wird mitimportiert,
# damit es weiterhin ueber dieses Modul erreichbar bleibt.
from midea_i18n import (install_excepthook_redaction, install_log_redaction,
                        make_translator, resolve_lang)  # noqa: F401

# Dieses Werkzeug hat bisher gar kein Logging eingerichtet - dann bedient
# logging.lastResort die Records einer Fremdbibliothek, ebenfalls nach stderr
# und ebenfalls ab WARNING, nur ungefiltert. Der eigene Handler wird in main()
# gesetzt, nicht hier: ein blosser Import soll den Root-Logger des Aufrufers
# nicht umkonfigurieren (siehe install_log_redaction).

CONFIG_PATH = Path(__file__).parent / "devices.json"
SUBPROCESS_TIMEOUT = 60

# Zeitlimit fuer die GESAMTE Verifikation eines Kandidaten, also authenticate()
# UND refresh() zusammen.
#
# Bewusst groesser als msmart-ngs eigener Worst Case: LAN.authenticate braucht
# bis zu 5s Verbindungsaufbau + 3 interne Wiederholungen a 2s Lesetimeout = 11s
# im Fehlerfall, plus 1s Nachlauf, wenn der Handshake gelingt (das sleep(1) am
# Ende von LAN.authenticate wird auf dem Fehlerpfad nicht mehr erreicht) - also
# bis zu ~12s. LAN.send fuer refresh() nochmals 3 x 2s = ~6s. Ein kleinerer
# Deckel wuerde msmart-ngs eigene Wiederholungslogik mitten
# im Lauf abschneiden und den Abbruch als "Zeitlimit" ausweisen, obwohl das
# Geraet noch geantwortet haette - ein Falschnegativ, das die Diagnose unten
# zusaetzlich verfaelschen wuerde.
#
# Warum EIN Budget statt zweier: frueher lag um jeden der beiden Aufrufe ein
# eigenes 15s-Limit. Der schlechteste Fall waren damit 30s, ohne dass diese
# Zahl irgendwo stand - das Werkzeug sagte "15s" und konnte doppelt so lange
# brauchen. Der Wert hier ist genau jener bisherige Gesamt-Worst-Case, nur
# jetzt ausgesprochen und tatsaechlich durchgesetzt. Kein Lauf, der bisher
# durchlief, faellt dadurch heraus: pro Schritt ist der Deckel groesser als
# zuvor, in der Summe unveraendert.
VERIFY_TIMEOUT = 30

# Pause zwischen zwei Kandidaten-Verifikationen bzw. zwischen zwei Geraeten.
# Midea-Klimaanlagen halten nur EINE lokale Verbindung gleichzeitig und
# quittieren eine dichte Folge von Verbindungsversuchen mit einer voruebergehenden
# Blockade: sie nehmen die TCP-Verbindung dann zwar noch an, antworten aber nicht
# mehr. Ohne diese Pause kann bereits der eigene Pruefablauf die Ursache dafuer
# sein, dass Kandidat 2 und 3 "nicht antworten" - obwohl nur Kandidat 1 wirklich
# abgelehnt wurde. midea_ieco_ensure.py entzerrt seine Zugriffe aus demselben
# Grund (RETRY_DELAY).
CANDIDATE_DELAY = 5.0
DEVICE_DELAY = 2.0


# Katalog: key -> (englisch, deutsch). Platzhalter im printf-Stil (%s), damit
# beide Sprachfassungen strukturell identisch bleiben und ein fehlender
# Platzhalter beim Testen sofort auffaellt.
_MESSAGES: dict[str, tuple[str, str]] = {
    # --- Konfiguration -----------------------------------------------------
    "cfg_unreadable": (
        "ERROR: %s could not be read (%s: %s).",
        "FEHLER: %s konnte nicht gelesen werden (%s: %s)."),
    "cfg_bad_shape": (
        'ERROR: %s does not have the expected form {"devices": [...]}.',
        'FEHLER: %s hat nicht die erwartete Form {"devices": [...]}.'),
    # --- Geraeteaktualisierung ---------------------------------------------
    "dev_no_ip": (
        "[%s] ERROR: No IP address configured for this device in devices.json.",
        "[%s] FEHLER: Keine IP-Adresse in devices.json fuer dieses Geraet hinterlegt."),
    "dev_fetching": (
        "[%s] Fetching token/key candidates via %s ...",
        "[%s] Hole Token/Key-Kandidaten ueber %s ..."),
    "dev_fetch_failed": (
        "[%s] ERROR while fetching tokens: %s",
        "[%s] FEHLER beim Token-Abruf: %s"),
    "dev_id_mismatch": (
        "[%s] WARNING: devices.json says id=%s, but the cloud reports id=%s. "
        "Existing value NOT overwritten.",
        "[%s] WARNUNG: In devices.json steht id=%s, aber die Cloud meldet id=%s. "
        "Bestehenden Wert NICHT ueberschrieben."),
    "dev_no_id": (
        "[%s] ERROR: No device ID available (neither in devices.json nor reported "
        "by the cloud). Entry will NOT be saved.",
        "[%s] FEHLER: Keine Geraete-ID verfuegbar (weder in devices.json noch von der "
        "Cloud gemeldet). Eintrag wird NICHT gespeichert."),
    "dev_candidates_found": (
        "[%s] %s candidate(s) found, verifying one by one ...",
        "[%s] %s Kandidat(en) gefunden, verifiziere der Reihe nach ..."),
    "dev_candidate_ok": (
        "[%s] Candidate %s/%s verified successfully and saved.",
        "[%s] Kandidat %s/%s erfolgreich verifiziert und gespeichert."),
    # Bewusst neutral formuliert ("failed", nicht "rejected"): nur EINE der
    # moeglichen Ursachen ist tatsaechlich eine Ablehnung durch das Geraet. Bei
    # einer nicht zustande gekommenen Verbindung hat nichts und niemand etwas
    # abgelehnt - die konkrete Ursache steht ohnehin direkt dahinter.
    "dev_candidate_failed": (
        "[%s] Candidate %s/%s failed: %s%s",
        "[%s] Kandidat %s/%s fehlgeschlagen: %s%s"),
    "dev_try_next": (
        ", trying the next one ...",
        ", versuche naechsten ..."),
    "dev_all_failed": (
        "[%s] ERROR: None of the %s candidates was accepted. Existing values in "
        "devices.json remain unchanged.",
        "[%s] FEHLER: Keiner der %s Kandidaten wurde akzeptiert. Bestehende Werte "
        "in devices.json bleiben unveraendert."),
    "dev_hint": (
        "[%s] Hint: %s",
        "[%s] Hinweis: %s"),
    # --- Diagnose eines fehlgeschlagenen Versuchs --------------------------
    "diag_cap": (
        "Time limit reached (%ss) - device is responding unusually slowly",
        "Zeitlimit erreicht (%ss) - Geraet reagiert ungewoehnlich langsam"),
    "diag_no_state": (
        "handshake succeeded, but the device sent no state back - it accepted "
        "the connection and then went quiet",
        "Handshake erfolgreich, aber das Geraet lieferte keinen Zustand zurueck "
        "- es nahm die Verbindung an und verstummte dann"),
    "diag_rejected": (
        "device actively rejected the token (device replied with ERROR)",
        "Geraet hat den Token aktiv abgelehnt (ERROR-Antwort des Geraets)"),
    "diag_silent": (
        "device accepts the connection but does not answer",
        "Geraet nimmt die Verbindung an, antwortet aber nicht"),
    "diag_reset": (
        "connection was dropped by the device",
        "Verbindung wurde vom Geraet abgebrochen"),
    # Bewusst NEUTRAL gehalten. msmart-ng wirft 'Connect failed.' fuer JEDEN
    # OSError aus create_connection (lan.py) - also fuer eine abgewiesene
    # Verbindung ebenso wie fuer einen DNS-Fehler, ein nicht erreichbares Netz
    # oder einen nicht erreichbaren Host. Eine bestimmtere Formulierung (etwa
    # "die Verbindung wurde abgewiesen, es hat also etwas geantwortet") waere fuer
    # die Mehrzahl dieser Faelle schlicht falsch - real geprueft mit DNS-Fehler
    # und ENETUNREACH, beide liefern genau diese Meldung.
    "diag_unreachable": (
        "no connection could be established (address and port reachable?)",
        "es kam keine Verbindung zustande (Adresse und Port erreichbar?)"),
    "diag_unreachable_timeout": (
        "no answer at all while connecting - wrong IP, device switched off, or "
        "blocked by a firewall",
        "keine Antwort beim Verbindungsaufbau - falsche IP, Geraet aus, oder von "
        "einer Firewall verworfen"),
    "diag_bad_key": (
        "device answered, but the key does not decrypt its reply "
        "(token accepted, key wrong)",
        "Geraet hat geantwortet, aber der Key entschluesselt die Antwort nicht "
        "(Token angenommen, Key falsch)"),
    "diag_no_detail": (
        "no further detail",
        "keine naehere Angabe"),
    # --- Gesamthinweise ----------------------------------------------------
    "hint_all_rejected": (
        "The device was reachable and actively rejected EVERY token. Network and "
        "reachability are therefore fine - the tokens do not match this device. "
        "Known causes: firmware that handles local login differently, or a udpId "
        "variant the cloud lookup does not cover.",
        "Das Geraet war erreichbar und hat JEDEN Token aktiv abgelehnt. Netzwerk "
        "und Erreichbarkeit sind damit in Ordnung - die Tokens passen nicht zu "
        "diesem Geraet. Bekannte Ursachen: eine Firmware, die lokale Anmeldung "
        "anders handhabt, oder eine udpId-Variante, die die Cloud-Abfrage nicht "
        "abdeckt."),
    "hint_all_silent": (
        "The device accepts connections but answers none of the attempts. Most "
        "common cause: it holds only ONE local connection and is temporarily "
        "blocked after rapid access - the Midea app on your phone occupies that "
        "same connection. Please close the app, wait a few minutes and retry.",
        "Das Geraet nimmt Verbindungen an, antwortet aber auf keinen Versuch. "
        "Haeufigste Ursache: Es haelt nur EINE lokale Verbindung und ist nach "
        "dichten Zugriffen voruebergehend blockiert - auch die Midea-App auf dem "
        "Handy belegt diese Verbindung. Bitte die App schliessen, einige Minuten "
        "warten und erneut versuchen."),
    "hint_all_unreachable": (
        "No connection could be established at all, so the token could not even "
        "be tried. Please check the IP address in devices.json, that the unit is "
        "powered and on the network, and that port 6444 is reachable "
        "(e.g. 'ping <IP>' and 'nc -zv <IP> 6444').",
        "Es kam ueberhaupt keine Verbindung zustande - der Token konnte also gar "
        "nicht erst geprueft werden. Bitte IP-Adresse in devices.json pruefen, ob "
        "die Anlage mit Strom und im Netz ist, und ob Port 6444 erreichbar ist "
        "(z.B. 'ping <IP>' und 'nc -zv <IP> 6444')."),
    "hint_all_bad_key": (
        "The device answered every handshake, but none of the keys could decrypt "
        "its reply. Network and reachability are fine and the unit is the right "
        "one - the key half of each pair simply does not belong to it. This is "
        "the clearest sign that the stored credentials are stale: re-running the "
        "token retrieval is the right next step.",
        "Das Geraet hat jeden Handshake beantwortet, aber keiner der Keys konnte "
        "die Antwort entschluesseln. Netzwerk und Erreichbarkeit sind in Ordnung, "
        "und es ist auch die richtige Anlage - nur der Key-Teil des jeweiligen "
        "Paares gehoert nicht dazu. Das ist das deutlichste Zeichen fuer veraltete "
        "Zugangsdaten: der Token-Abruf sollte erneut laufen."),
    # Gemischt "abgelehnt" UND "Key passt nicht": beides sind ANTWORTEN des
    # Geraets, nur unterschiedlich weit gekommen. Fuer den Nutzer ist die
    # Schlussfolgerung dieselbe wie bei den beiden reinen Faellen - und sie ist
    # die klarste, die dieses Werkzeug ueberhaupt ziehen kann. Bis 0.2.x fiel
    # genau diese Kombination stumm durch, obwohl sie mehr Gewissheit traegt als
    # jeder Einzelfall.
    "hint_all_answered": (
        "The device answered every attempt: some tokens it rejected outright, "
        "for others its reply could not be decrypted with the matching key. "
        "Network and reachability are therefore fine - the stored credentials "
        "simply do not belong to this unit. Re-running the token retrieval is "
        "the right next step.",
        "Das Geraet hat jeden Versuch beantwortet: einige Tokens hat es "
        "abgelehnt, bei anderen liess sich seine Antwort nicht mit dem "
        "zugehoerigen Key entschluesseln. Netzwerk und Erreichbarkeit sind "
        "damit in Ordnung - die gespeicherten Zugangsdaten gehoeren schlicht "
        "nicht zu dieser Anlage. Der Token-Abruf sollte erneut laufen."),
    "hint_all_reset": (
        "The device dropped every connection. Most often this is the "
        "single-connection limit: it holds only ONE local connection at a time, "
        "and the Midea app on your phone occupies that same one. Please close the "
        "app, wait a few minutes and retry.",
        "Das Geraet hat jede Verbindung abgebrochen. Meist steckt die "
        "Einzelverbindungs-Grenze dahinter: Es haelt nur EINE lokale Verbindung "
        "gleichzeitig, und die Midea-App auf dem Handy belegt genau diese. Bitte "
        "die App schliessen, einige Minuten warten und erneut versuchen."),
    # Der Text nennt BEIDE Antwortarten (Ablehnung und nicht entschluesselbare
    # Antwort), weil _ANSWERED_CODES beide umfasst. Die fruehere Fassung sprach
    # nur von "actively rejected" und behauptete damit bei [bad_key, silent] eine
    # Ablehnung, die nie stattgefunden hat.
    "hint_mixed": (
        "Notable pattern: the device answered at first - a token was rejected, "
        "or its reply could not be decrypted - and stopped answering "
        "afterwards. Most likely it accepted no further connection after that, "
        "so the later candidates are NOT meaningful. Please wait a few minutes "
        "and retry.",
        "Auffaelliges Muster: das Geraet hat zunaechst geantwortet - ein Token "
        "wurde abgelehnt, oder seine Antwort liess sich nicht entschluesseln - "
        "und danach gar nicht mehr. Sehr wahrscheinlich hat es keine weitere "
        "Verbindung mehr angenommen; die spaeteren Kandidaten sind damit NICHT "
        "aussagekraeftig. Bitte einige Minuten warten und erneut versuchen."),
    # --- main() ------------------------------------------------------------
    "main_msmart_missing": (
        "ERROR: msmart-ng is not installed in the active Python interpreter. "
        "Is the venv active? Install it e.g. with: venv/bin/pip install msmart-ng",
        "FEHLER: msmart-ng ist im aktiven Python-Interpreter nicht installiert. "
        "Ist die venv aktiv? Installation z.B. mit: venv/bin/pip install msmart-ng"),
    "main_skipped_entries": (
        "WARNING: %s unexpected entry/entries in %s skipped (not an object).",
        "WARNUNG: %s unerwartete(r) Eintrag/Eintraege in %s uebersprungen "
        "(kein Objekt)."),
    "main_no_devices": (
        "devices.json does not contain any devices yet. Use --name/--host for a "
        "new device.",
        "devices.json enthaelt noch keine Geraete. Nutze --name/--host fuer ein "
        "neues Geraet."),
    "main_new_needs_host": (
        "Device '%s' is new. Please also pass --host.",
        "Geraet '%s' ist neu. Bitte zusaetzlich --host angeben."),
    "main_new_not_saved": (
        "New device '%s' was NOT saved because the lookup failed.",
        "Neues Geraet '%s' wurde NICHT gespeichert, da der Abruf fehlgeschlagen ist."),
    "main_write_failed": (
        "ERROR: devices.json could not be written (%s: %s).",
        "FEHLER: devices.json konnte nicht geschrieben werden (%s: %s)."),
    "main_updated": (
        "devices.json updated: %s",
        "devices.json aktualisiert: %s"),
    # --- Fehler des discover-Unterprozesses -------------------------------
    # Diese Texte werden als RuntimeError geworfen UND ueber "dev_fetch_failed"
    # an den Nutzer ausgegeben - sie muessen daher genauso uebersetzt sein wie
    # jeder print(). Sie waren zunaechst deutsch fest verdrahtet, sodass ein
    # englischsprachiger Nutzer im WAHRSCHEINLICHSTEN Fehlerfall deutschen Text
    # zu lesen bekam (aufgefallen bei der Nachpruefung zu Issue #2).
    "err_tempdir": (
        "Could not create the temporary working directory for discover (%s: %s).",
        "Temporaeres Arbeitsverzeichnis fuer discover konnte nicht angelegt "
        "werden (%s: %s)."),
    "err_isolation_config": (
        "Could not write the isolation config for discover (%s: %s).",
        "Isolations-Konfig fuer discover konnte nicht geschrieben werden (%s: %s)."),
    "err_discover_timeout": (
        "The discover command did not respond within %ss (is the device at %s "
        "reachable?)",
        "discover-Befehl hat nach %ss nicht reagiert (Geraet unter %s erreichbar?)"),
    "err_midealocal_missing": (
        "midealocal is not installed in the current Python interpreter (wrong "
        "venv active?)",
        "midealocal ist im aktuellen Python-Interpreter nicht installiert "
        "(falsches venv aktiv?)"),
    "err_discover_start": (
        "The discover command could not be started (%s: %s).",
        "discover-Befehl konnte nicht gestartet werden (%s: %s)."),
    "err_discover_exit": (
        "The discover command exited with code %s. Last output: %s",
        "discover-Befehl endete mit Exit-Code %s. Letzte Ausgabe: %s"),
    "err_no_tokenlist": (
        "No tokenlist entry found in the output. Last output: %s",
        "Kein tokenlist-Eintrag in der Ausgabe gefunden. Letzte Ausgabe: %s"),
    "err_no_output": (
        "(no output)",
        "(keine Ausgabe)"),
    "dev_unknown_name": (
        "unknown",
        "unbekannt"),
    # --- CLI ---------------------------------------------------------------
    "cli_description": (
        "Fetches fresh Midea token/key pairs via discover --debug, verifies "
        "them, and updates devices.json.",
        "Holt frische Midea Token/Key-Paare per discover --debug, verifiziert "
        "sie, und aktualisiert devices.json."),
    "cli_help_all": (
        "Update all devices from devices.json",
        "Alle Geraete aus devices.json aktualisieren"),
    "cli_help_name": (
        "Name of the device (new or existing)",
        "Name des Geraets (neu oder bestehend)"),
    "cli_help_host": (
        "IP address (only together with --name, for NEW devices)",
        "IP-Adresse (nur zusammen mit --name fuer NEUE Geraete)"),
}


t = make_translator(_MESSAGES)

# Extraktion der (key, token)-Paare aus der rohen --debug-Ausgabe der Cloud.
# Beispielformat (einzeilig):
#   response: b'{"result": {"tokenlist": [{"udpId": "..", "key": "HEX", "token": "HEX"}]}...}'
# Annahmen (entsprechen dem beobachteten Format): tokenlist ist ein Array
# FLACHER Objekte (keine verschachtelten Klammern); key/token sind Hex-Strings
# und koennen innerhalb eines Eintrags in BELIEBIGER Reihenfolge stehen.
# Mehrere Eintraege und mehrere tokenlist-Arrays werden alle beruecksichtigt.
_TOKENLIST_ARRAY_RE = re.compile(r'"tokenlist"\s*:\s*\[(.*?)\]', re.DOTALL)
_ENTRY_RE = re.compile(r"\{(.*?)\}", re.DOTALL)
_KEY_RE = re.compile(r'"key"\s*:\s*"([0-9a-fA-F]+)"')
_TOKEN_RE = re.compile(r'"token"\s*:\s*"([0-9a-fA-F]+)"')
APPLIANCE_ID_RE = re.compile(r"applianceCodes['\"]?\s*[:=]\s*['\"]?(\d+)")


def extract_token_key_pairs(text: str) -> list[tuple[str, str]]:
    """Liefert ALLE (key, token)-Paare aus allen tokenlist-Arrays der rohen
    Cloud-Antwort - reihenfolgeerhaltend und dedupliziert. Beruecksichtigt
    beliebige Feldreihenfolge innerhalb eines Eintrags sowie mehrere Eintraege
    bzw. mehrere tokenlist-Arrays (siehe Formatannahmen oben). Die alte,
    strikt 'key vor token'-erwartende Extraktion war eine echte Teilmenge
    dieser hier."""
    pairs: list[tuple[str, str]] = []
    for array_body in _TOKENLIST_ARRAY_RE.findall(text):
        for entry_body in _ENTRY_RE.findall(array_body):
            key = _KEY_RE.search(entry_body)
            token = _TOKEN_RE.search(entry_body)
            if key and token:
                pairs.append((key.group(1), token.group(1)))
    return list(dict.fromkeys(pairs))


def load_config() -> dict:
    """Liest devices.json. Fehlt die Datei, wird eine leere Geraeteliste
    zurueckgegeben (normaler Erstlauf). Ist die Datei hingegen unlesbar, nicht
    als UTF-8 dekodierbar, kein gueltiges JSON oder hat sie nicht die erwartete
    Form ({"devices": [...]}),
    wird mit klarer Meldung auf stderr abgebrochen - ein verstaendlicher
    Hinweis ist im woechentlichen Cron-Lauf deutlich nuetzlicher als ein roher
    Traceback."""
    if not CONFIG_PATH.exists():
        return {"devices": []}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError (nicht-UTF-8-Datei) ab
        print(t("cfg_unreadable", CONFIG_PATH, type(exc).__name__, exc),
              file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        print(t("cfg_bad_shape", CONFIG_PATH), file=sys.stderr)
        sys.exit(1)
    return data


def _atomic_write_json(path: Path, data: object) -> None:
    """Schreibt ``data`` als JSON atomar und mit Rechten 0600 nach ``path``.

    Ablauf: in eine temporaere Datei IM SELBEN Verzeichnis schreiben (damit
    os.replace auf demselben Dateisystem bleibt), Rechte auf 0600 setzen, auf
    Platte zwingen (flush + fsync) und erst dann per os.replace an den
    endgueltigen Namen ruecken. Das garantiert zweierlei:
      (a) kein Zeitfenster, in dem die Datei world-readable waere - mkstemp
          legt sie von vornherein nur fuer den aktuellen Nutzer lesbar an;
      (b) kein zerstoerter Torso - bricht das Schreiben ab, bleibt die
          bisherige ``path``-Datei unveraendert; zurueck bleibt hoechstens
          eine harmlose, git-ignorierte .tmp-Waise.
    os.replace ist laut POSIX eine atomare Operation."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort-Aufraeumen der temporaeren Datei; das Original bleibt
        # in jedem Fall unangetastet. Fehler wird weitergereicht.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def save_config(config: dict) -> None:
    """Schreibt devices.json atomar und mit Rechten 0600 (siehe
    _atomic_write_json). Bei einem Schreibfehler bleibt die bisherige Datei
    unveraendert; der Fehler (OSError) wird an den Aufrufer weitergereicht."""
    _atomic_write_json(CONFIG_PATH, config)


def _run_discover(host: str) -> subprocess.CompletedProcess:
    """Fuehrt `midealocal.cli discover --host <host> --debug` in einem privaten,
    pro Aufruf frisch angelegten Temp-Verzeichnis aus und gibt das Ergebnis
    zurueck.

    Bewusst OHNE --username/--password: die Token-Vergabe laeuft ohnehin ueber
    midealocals eingebautes NetHome-Plus-Konto (siehe Modul-Docstring), eigene
    Zugangsdaten waeren wirkungslos. Damit taucht auch garantiert kein Passwort
    in der Prozess-argv auf.

    In das Temp-Verzeichnis wird eine LEERE midea-local.json ({}) geschrieben und
    zum CWD des Unterprozesses gemacht. Grund: get_config_file_path() der CLI
    bevorzugt eine midea-local.json im aktuellen Verzeichnis vor der nutzer-
    globalen Konfiguration (~/.config/midea-local/midea-local.json). Ohne diesen
    Guard koennte eine dort hinterlegte Config (z.B. mit einem cloud_name wie
    "SmartHome") den Abruf auf eine serverseitig abgeschaltete Cloud-API umleiten
    und so je nach Host unterschiedlich scheitern. Die leere {}-Datei
    ueberschreibt NICHTS (der Merge der CLI fuellt nur FEHLENDE Namespace-Felder),
    macht das Verhalten aber deterministisch und unabhaengig von der Host-
    Umgebung. Ein eigenes Temp-Verzeichnis je Aufruf isoliert zudem gleichzeitige
    Laeufe (Wochen-Cron trifft manuellen Aufruf) gegeneinander. Es wird in jedem
    Fall wieder entfernt.

    Wirft RuntimeError bei jedem Ausfuehrungsfehler (Temp-Verzeichnis oder
    Isolations-Konfig nicht anlegbar, Timeout, midealocal nicht installiert,
    sonstiger Subprozess-Startfehler)."""
    cmd = [sys.executable, "-m", "midealocal.cli", "discover", "--host", host, "--debug"]
    try:
        tmpdir = tempfile.mkdtemp(prefix="midea-local-discover-")
    except OSError as exc:
        raise RuntimeError(t("err_tempdir", type(exc).__name__, exc)) from exc
    try:
        try:
            _atomic_write_json(Path(tmpdir) / "midea-local.json", {})
        except OSError as exc:
            raise RuntimeError(t("err_isolation_config", type(exc).__name__, exc)) from exc
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=SUBPROCESS_TIMEOUT, cwd=tmpdir)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(t("err_discover_timeout", SUBPROCESS_TIMEOUT, host)) from exc
        except FileNotFoundError as exc:
            # FileNotFoundError ist eine OSError-Unterklasse und MUSS vor dem
            # generischen 'except OSError' stehen, damit hier die spezifische
            # Meldung ("midealocal nicht installiert") greift.
            raise RuntimeError(t("err_midealocal_missing")) from exc
        except OSError as exc:
            # Jeder sonstige Startfehler des Unterprozesses (z.B. PermissionError)
            # wird ebenfalls als RuntimeError gewrappt - update_device faengt nur
            # RuntimeError; ohne dieses Wrapping schluege ein solcher Fehler als
            # roher Traceback durch und beendete einen ganzen 'all'-Lauf.
            raise RuntimeError(t("err_discover_start", type(exc).__name__, exc)) from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _parse_discover_output(result: subprocess.CompletedProcess) -> tuple[list[tuple[str, str]], str | None]:
    """Extrahiert (key, token)-Kandidaten und Appliance-ID aus einem discover-
    Ergebnis. Wirft RuntimeError bei Nicht-Null-Exit oder fehlender tokenlist."""
    combined_output = result.stdout + result.stderr
    if result.returncode != 0:
        tail = combined_output[-800:] if combined_output else t("err_no_output")
        raise RuntimeError(t("err_discover_exit", result.returncode, tail))
    matches = extract_token_key_pairs(combined_output)
    if not matches:
        tail = combined_output[-800:] if combined_output else t("err_no_output")
        raise RuntimeError(t("err_no_tokenlist", tail))
    appliance_ids = APPLIANCE_ID_RE.findall(combined_output)
    return matches, (appliance_ids[0] if appliance_ids else None)


def fetch_candidate_credentials(host: str) -> tuple[list[tuple[str, str]], str | None]:
    """Ruft discover --debug auf und gibt ALLE gefundenen (key, token)-
    Kandidaten zurueck, sowie die gemeldete Appliance-ID (falls vorhanden).
    Wirft RuntimeError bei einem klaren Ausfuehrungsfehler (Timeout, kein
    tokenlist-Eintrag in der Ausgabe, midealocal nicht installiert).

    Es gibt genau EINEN Weg (siehe _run_discover): der discover-Aufruf ohne
    Zugangsdaten. Ein frueherer Kommandozeilen-Fallback (Passwort via argv)
    entfiel mit 0.2.0, weil eigene Zugangsdaten fuer den Token-Abruf ohnehin
    wirkungslos sind - siehe Modul-Docstring."""
    result = _run_discover(host)
    return _parse_discover_output(result)


# ---------------------------------------------------------------------------
# Diagnose eines fehlgeschlagenen Verifikationsversuchs
#
# msmart-ng meldet JEDEN dieser Faelle als denselben Typ (msmart.lan.
# AuthenticationError) - unterscheidbar sind sie ausschliesslich am
# Meldungstext. Die folgenden Textmarken wurden gegen msmart-ng 2026.7.0 real
# gemessen (Fake-Geraet je Fall):
#   'Error packet received.'          <- Geraet sendet einen ERROR-Frame (0x8370.. type 0xF)
#   'No response from host.'          <- Geraet nimmt an, antwortet aber nicht
#   'Transport is closing or closed.' <- Verbindung abgebrochen/zurueckgesetzt
#   'Connect failed.'                 <- Verbindung aktiv abgewiesen/nicht routbar
#   'Connect timeout.'                <- Verbindungsaufbau lief in msmart-ngs 5s-Limit
# Aendert msmart-ng die Formulierung, greift der Rueckfall VERIFY_OTHER, der
# den Originaltext ungekuerzt weiterreicht - es geht dann Einordnung verloren,
# aber nie Information.
# ---------------------------------------------------------------------------
VERIFY_REJECTED = "rejected"        # Geraet lehnt den Token aktiv ab
VERIFY_BAD_KEY = "bad_key"          # Geraet antwortet, aber der KEY entschluesselt nicht
VERIFY_SILENT = "silent"            # Verbindung steht, keine Antwort
VERIFY_RESET = "reset"              # Verbindung abgebrochen
VERIFY_UNREACHABLE = "unreachable"  # Host/Port gar nicht erreichbar
VERIFY_CAP = "cap"                  # unser eigenes VERIFY_TIMEOUT hat gegriffen
VERIFY_OTHER = "other"              # nicht eingeordnet - Originaltext bleibt erhalten

# Textmarke -> (Diagnose-Code, Katalog-Schluessel). Bewusst der SCHLUESSEL und
# nicht der fertige Text: so wird die Sprache erst beim Aufruf aufgeloest.
#
# 'connect timeout' und 'connect failed' teilen sich den Code VERIFY_UNREACHABLE
# (beide heissen "es kam gar keine Verbindung zustande" und fuehren zum selben
# Gesamthinweis), bekommen aber UNTERSCHIEDLICHE Klartexte: abgewiesen bedeutet,
# dass etwas geantwortet hat - eine Zeitueberschreitung dagegen, dass unter der
# Adresse niemand ist. Das ist fuer die Fehlersuche ein realer Unterschied.
# Die Marken sind disjunkt (keine ist Teilzeichenkette einer anderen), die
# Reihenfolge dieser Tabelle ist daher nicht bedeutungstragend.
_FAILURE_MARKERS = (
    ("error packet", VERIFY_REJECTED, "diag_rejected"),
    # Der Handshake kam zurueck, liess sich mit diesem Key aber nicht
    # entschluesseln (msmart-ng vergleicht den SHA256 der entschluesselten
    # Antwort). Das ist die praeziseste Aussage, die das Protokoll hergibt:
    # Geraet erreichbar, Token angenommen, KEY falsch. Fiel bisher stumm auf
    # VERIFY_OTHER und damit ganz ohne Hinweis durch.
    ("digest do not match", VERIFY_BAD_KEY, "diag_bad_key"),
    ("no response from host", VERIFY_SILENT, "diag_silent"),
    ("transport is closing", VERIFY_RESET, "diag_reset"),
    # Ein echtes TCP-RST kommt nicht als 'Transport is closing', sondern als
    # durchgereichter OSError-Text an ('[Errno 54] Connection reset by peer' auf
    # macOS, Errno 104 auf Linux - der Wortlaut ist auf beiden gleich).
    ("connection reset by peer", VERIFY_RESET, "diag_reset"),
    ("connect failed", VERIFY_UNREACHABLE, "diag_unreachable"),
    ("connect timeout", VERIFY_UNREACHABLE, "diag_unreachable_timeout"),
)


def classify_verify_failure(exc: BaseException) -> tuple[str, str]:
    """Ordnet eine Ausnahme aus verify_credentials einem Diagnose-Code und einem
    kurzen Klartext zu. Rueckgabe: (code, text).

    Wichtig fuer die Fehlersuche: 'Token abgelehnt' und 'Geraet antwortet nicht'
    sind voellig verschiedene Ursachen (falsche Zugangsdaten vs. Netzwerk bzw.
    voruebergehende Geraeteblockade), sehen fuer den Nutzer aber identisch aus,
    wenn man - wie bisher - nur 'hat nicht funktioniert' ausgibt. Sie sind
    zusaetzlich am Zeitverhalten unterscheidbar: eine ERROR-Antwort scheitert
    praktisch sofort, ein stummes Geraet erst nach mehreren Sekunden.

    Ein bereits von uns gesetztes Zeitlimit (asyncio.timeout -> TimeoutError)
    wird bewusst getrennt ausgewiesen, damit es nicht mit einem stummen Geraet
    verwechselt wird. Das gilt nur, solange das Limit waehrend authenticate()
    zuschlaegt - waehrend refresh() verschluckt msmart-ng die Ursache, dort
    entscheidet stattdessen die online-Pruefung in verify_credentials."""
    text = str(exc).strip()
    haystack = text.lower()

    # Textmarken ZUERST, Ausnahmetyp erst danach. Grund: msmart-ng benennt seine
    # Ursachen im Meldungstext, wrappt sie aber nicht immer gleich - 'Connect
    # timeout.' etwa entsteht als TimeoutError und wird in Device.authenticate zu
    # einem AuthenticationError umgehaengt, in anderen Pfaden aber nicht. Wuerde
    # der isinstance-Test zuerst greifen, landete eine benannte Ursache je nach
    # Aufrufweg mal richtig eingeordnet und mal pauschal als "unser Zeitlimit" -
    # also genau die Vermengung, die diese Funktion beseitigen soll.
    for marker, code, message_key in _FAILURE_MARKERS:
        if marker in haystack:
            return code, t(message_key)

    # Erst jetzt der eigene Deckel: asyncio.timeout wirft einen TimeoutError
    # OHNE Meldungstext, der oben folglich auf keine Marke passt.
    #
    # asyncio.TimeoutError wird ausdruecklich MIT aufgefuehrt, obwohl es ab
    # Python 3.11 nur ein Alias des eingebauten TimeoutError ist (und das Projekt
    # 3.11+ voraussetzt): auf aelteren Interpretern sind es zwei verschiedene
    # Klassen, und dann liefe genau dieser Zweig ins Leere - der Abbruch waere
    # dort als "unklassifiziert" gemeldet worden statt als unser Zeitlimit.
    # Die Nennung kostet nichts und nimmt eine stille Versionsannahme aus einer
    # Funktion, deren einzige Aufgabe das korrekte Einordnen ist.
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return VERIFY_CAP, t("diag_cap", VERIFY_TIMEOUT)

    # Rueckfall: der Originaltext von msmart-ng bleibt unveraendert erhalten -
    # er ist ohnehin englisch und wird daher NICHT uebersetzt.
    detail = text or t("diag_no_detail")
    return VERIFY_OTHER, f"{type(exc).__name__}: {detail}"


# Fehlerarten, bei denen das Geraet nachweislich GEANTWORTET hat - die
# Zugangsdaten stimmen dann nicht, das Netz ist in Ordnung.
#
# WICHTIG bei jeder Aenderung dieser beiden Mengen: die Hinweistexte unten
# behaupten etwas ueber ihren Inhalt ("das Geraet hat geantwortet", "danach nicht
# mehr"). Wird hier ein Code hinzugenommen oder entfernt, muss die Wahrheitstabelle
# von summarize_failure_hint ueber den vollstaendigen Eingaberaum NEU gerechnet und
# der Text nachgezogen werden. Genau dieser Schritt unterblieb, als VERIFY_BAD_KEY
# aufgenommen wurde: der Text sprach weiterhin nur von einer Ablehnung.
_ANSWERED_CODES = frozenset({VERIFY_REJECTED, VERIFY_BAD_KEY})

# Fehlerarten, die bedeuten "das Geraet hat auf diesen Versuch nicht mehr
# brauchbar reagiert" - im Unterschied zu _ANSWERED_CODES. Nur diese koennen ein
# Verstummen NACH einer Antwort sein.
_BLOCKING_CODES = frozenset({VERIFY_SILENT, VERIFY_RESET, VERIFY_UNREACHABLE,
                             VERIFY_CAP})


def summarize_failure_hint(codes: list[str]) -> str | None:
    """Leitet aus den Diagnose-Codes ALLER gescheiterten Kandidaten einen
    handlungsleitenden Hinweis ab, oder None, wenn sich nichts Belastbares
    sagen laesst. Bewusst zurueckhaltend formuliert: der Hinweis benennt die
    wahrscheinlichste Ursache, behauptet sie aber nicht als gesichert.

    ``codes`` ist eine REIHENFOLGETREUE Liste in Kandidatenreihenfolge - das ist
    fuer den gemischten Fall wesentlich und darf nicht zu einer Menge
    zusammengefasst werden (siehe unten)."""
    if not codes:
        return None
    unique = set(codes)

    if unique == {VERIFY_REJECTED}:
        return t("hint_all_rejected")

    if unique == {VERIFY_BAD_KEY}:
        return t("hint_all_bad_key")

    if unique == {VERIFY_SILENT}:
        return t("hint_all_silent")

    if unique == {VERIFY_UNREACHABLE}:
        return t("hint_all_unreachable")

    if unique == {VERIFY_RESET}:
        return t("hint_all_reset")

    # Gemischt abgelehnt/Key-falsch: das Geraet hat JEDEN Versuch beantwortet -
    # es ist erreichbar, es ist die richtige Anlage, und die Zugangsdaten passen
    # trotzdem nicht. Diese Kombination ist der belastbarste Befund ueberhaupt,
    # blieb aber bisher ohne Hinweis, weil sie keiner der reinen Mengen gleicht.
    #
    # Bewusst NACH allen Einzelmengen-Zweigen: eine Teilmengenpruefung vor ihnen
    # haette stillschweigend vorausgesetzt, dass jedes Mitglied von
    # _ANSWERED_CODES weiter oben schon einen eigenen Zweig hat. Diese Annahme
    # waere bei der naechsten Erweiterung der Menge falsch geworden, ohne dass
    # hier etwas darauf hindeutet.
    if unique <= _ANSWERED_CODES:
        return t("hint_all_answered")

    # Bewusst KEIN Sammelhinweis fuer lauter VERIFY_CAP: unser Zeitlimit liegt
    # oberhalb von msmart-ngs eigenem Worst Case (authenticate ~12s, refresh
    # ~6s - refresh meldet Netzwerkfehler ohnehin nicht nach oben), es kann
    # praktisch also gar nicht greifen. Fuer einen Zustand, der nicht eintritt,
    # wird kein Ratschlag erfunden; die Einzelmeldung je Kandidat bleibt korrekt.
    #
    # Gemischter Fall "erst geantwortet, DANACH verstummt". Der Text macht zwei
    # Aussagen, und BEIDE muessen wahr sein, sonst schweigen wir:
    #
    #   (1) "zuerst hat es geantwortet" -> die erste Antwort muss vor der ersten
    #       Blockade liegen. Bei [nicht erreichbar, abgelehnt] hat das Geraet am
    #       Ende sehr wohl geantwortet; eine reine Mengenbetrachtung kann das
    #       nicht unterscheiden und behauptete frueher beides gleichermassen.
    #   (2) "danach nicht mehr" -> nach der LETZTEN Antwort darf nichts
    #       Beantwortetes mehr folgen, und der Schwanz darf nicht leer sein.
    #       Ohne diese Bedingung behauptete der Hinweis bei
    #       [abgelehnt, stumm, abgelehnt] ein Verstummen, obwohl der letzte
    #       Kandidat sehr wohl beantwortet wurde - Bedingung (1) allein sieht das
    #       nicht, weil sie nur den Anfang prueft.
    #
    # Ein unklassifizierter Code (VERIFY_OTHER) im Schwanz genuegt, um zu
    # schweigen: ueber ihn ist per Definition nichts bekannt, er kann also auch
    # kein Verstummen belegen. Trifft eine der Bedingungen nicht zu, wird bewusst
    # NICHTS gesagt - die Einzelzeilen pro Kandidat nennen den jeweiligen Grund
    # ohnehin praezise.
    #
    # VOR der ersten Antwort ist derselbe Code dagegen unschaedlich und bleibt
    # bewusst zugelassen: bei [unklassifiziert, abgelehnt, stumm] sind beide
    # Aussagen des Hinweises wahr - es WURDE geantwortet, und danach nicht mehr.
    # Der Kopf ist damit absichtlich grosszuegiger als der Schwanz, wo derselbe
    # Code das behauptete Ende entkraeften wuerde. Betrifft 64 der 2801 Folgen
    # bis Laenge vier; festgehalten in tests/KNOWN_GAPS.md, damit die Asymmetrie
    # nicht spaeter fuer ein Versehen gehalten und "korrigiert" wird.
    answered_at = [i for i, code in enumerate(codes) if code in _ANSWERED_CODES]
    blocking_at = [i for i, code in enumerate(codes) if code in _BLOCKING_CODES]
    if answered_at and blocking_at and answered_at[0] < blocking_at[0]:
        after_last_answer = codes[answered_at[-1] + 1:]
        if after_last_answer and all(code in _BLOCKING_CODES
                                     for code in after_last_answer):
            return t("hint_mixed")

    return None


async def verify_credentials(ip: str, port: int, device_id: int, key: str,
                             token: str) -> tuple[bool, str, str]:
    """Testet ein Token/Key-Paar mit einer echten, minimalen Verbindung,
    BEVOR es in devices.json gespeichert wird. Verhindert, dass ein
    falscher Kandidat (z.B. 'method 2' statt 'method 1') unbemerkt
    gespeichert wird.

    Rueckgabe: (ok, code, text). Bei Erfolg ("", "") als Diagnose. Im
    Fehlerfall liefern code/text die Einordnung aus classify_verify_failure -
    frueher ging der Grund hier ersatzlos verloren (nur True/False), sodass
    'falscher Token' und 'Geraet nicht erreichbar' fuer den Nutzer
    ununterscheidbar waren."""
    # Lazy-Import: haelt das Modul auch ohne installiertes msmart importierbar
    # (z.B. fuer isolierte Unit-Tests der Zugangsdaten-Logik).
    from msmart.device.AC.device import AirConditioner as AC

    device = AC(ip=ip, port=port, device_id=device_id)
    try:
        # asyncio.timeout statt zweier wait_for-Aufrufe: EIN Budget spannt sich
        # ueber beide Schritte, sodass der Deckel das ist, was er verspricht
        # (siehe VERIFY_TIMEOUT). asyncio.timeout ist seit Python 3.11 die
        # empfohlene Form; das Projekt setzt 3.11 voraus. Bei Ablauf wirft es
        # denselben TimeoutError wie zuvor wait_for - classify_verify_failure
        # ordnet ihn unveraendert als VERIFY_CAP ein.
        async with asyncio.timeout(VERIFY_TIMEOUT):
            await device.authenticate(token, key)
            await device.refresh()

        # Ein erfolgreiches refresh() ist KEIN Beweis: msmart-ng meldet
        # Netzfehler an dieser Stelle nicht nach oben. Device._send_command
        # faengt ProtocolError und TimeoutError, protokolliert sie und liefert
        # eine leere Antwortliste; refresh() kehrt dann voellig normal zurueck.
        # Ein Geraet, das den Handshake besteht und danach schweigt, sah fuer
        # diese Funktion deshalb aus wie ein geglueckter Test - der Kandidat
        # galt als verifiziert und sein Token wurde gespeichert. Das hebelte
        # genau die Zusage aus, fuer die es diese Funktion gibt.
        #
        # device.online ist das beobachtbare Gegenstueck dazu: msmart-ng setzt
        # es in _send_commands_get_responses auf 'len(responses) > 0', also
        # genau dann auf False, wenn nichts zurueckkam. Es ist damit der einzige
        # Weg, einen verschluckten Fehler von aussen zu bemerken.
        #
        # VERIFY_SILENT und kein eigener Code: die Aussage "Verbindung steht,
        # das Geraet antwortet nicht" trifft hier woertlich zu, der Code liegt
        # bereits in _BLOCKING_CODES, und die Wahrheitstabelle von
        # summarize_failure_hint bleibt damit unberuehrt.
        if not device.online:
            return False, VERIFY_SILENT, t("diag_no_state")

        return True, "", ""
    except Exception as exc:
        code, text = classify_verify_failure(exc)
        return False, code, text
    finally:
        # Schliessen ist hier besonders wichtig: die Kandidaten werden
        # NACHEINANDER gegen dasselbe Geraet geprueft, und die Anlage haelt nur
        # EINE lokale Verbindung. Eine offen gebliebene Verbindung aus Kandidat
        # 1 laesst Kandidat 2 und 3 ins Leere laufen - der Lauf meldete dann
        # "antwortet nicht", obwohl er die Stille selbst erzeugt hat.
        close_connection(device)


def update_device(dev_conf: dict) -> bool:
    """Frischt Token/Key EINES Geraets auf: holt Kandidaten per credential-freiem
    discover, verifiziert sie der Reihe nach gegen das echte Geraet und speichert
    das erste funktionierende (key, token)-Paar in-place in dev_conf. Rueckgabe
    True bei Erfolg, sonst False. Bestehende Werte werden NUR bei erfolgreicher
    Verifikation ueberschrieben - schlaegt alles fehl, bleibt dev_conf
    unveraendert (kein kaputter Eintrag nach einem Fehlversuch)."""
    name = dev_conf.get("name", t("dev_unknown_name"))
    host = dev_conf.get("ip")
    if not host:
        print(t("dev_no_ip", name))
        return False

    print(t("dev_fetching", name, host))
    try:
        candidates, appliance_id = fetch_candidate_credentials(host)
    except RuntimeError as exc:
        print(t("dev_fetch_failed", name, exc))
        return False

    existing_id = str(dev_conf.get("id", "")).strip()
    if appliance_id is not None:
        if existing_id and existing_id != appliance_id:
            print(t("dev_id_mismatch", name, existing_id, appliance_id))
        elif not existing_id:
            dev_conf["id"] = int(appliance_id)

    device_id_str = str(dev_conf.get("id", "")).strip()
    if not device_id_str:
        print(t("dev_no_id", name))
        return False
    device_id = int(device_id_str)
    port = int(dev_conf.get("port", 6444))

    total = len(candidates)
    print(t("dev_candidates_found", name, total))
    failure_codes: list[str] = []
    for idx, (key, token) in enumerate(candidates, start=1):
        # Entzerrung VOR jedem weiteren Versuch (siehe CANDIDATE_DELAY): das
        # Geraet vertraegt nur eine Verbindung und blockiert nach dichten
        # Zugriffen voruebergehend. Ohne die Pause koennte der Pruefablauf die
        # spaeteren Kandidaten selbst um ihre Aussagekraft bringen.
        if idx > 1:
            time.sleep(CANDIDATE_DELAY)
        ok, code, detail = asyncio.run(
            verify_credentials(host, port, device_id, key, token))
        if ok:
            dev_conf["token"] = token
            dev_conf["key"] = key
            dev_conf.setdefault("port", 6444)
            print(t("dev_candidate_ok", name, idx, total))
            return True
        failure_codes.append(code)
        suffix = t("dev_try_next") if idx < total else ""
        print(t("dev_candidate_failed", name, idx, total, detail, suffix))

    print(t("dev_all_failed", name, total))
    hint = summarize_failure_hint(failure_codes)
    if hint:
        print(t("dev_hint", name, hint))
    return False


def main() -> None:
    """CLI-Einstieg: wertet --all bzw. --name/--host aus, prueft die msmart-
    Verfuegbarkeit VOR jedem Cloud-Kontakt, frischt die betroffenen Geraete auf
    (update_device) und schreibt devices.json atomar zurueck. Exit-Code: 0 =
    Erfolg, 2 = mindestens ein Geraet fehlgeschlagen, 1 = Nutzungs-/Konfig-Fehler
    (msmart fehlt, leere Geraeteliste bei --all, neues Geraet ohne --host,
    Schreibfehler)."""
    # Beide Waechter erst hier, nicht beim Import: Root-Logger und
    # sys.excepthook gelten prozessweit. Sie decken die zwei Wege ab, die der
    # Meldungskatalog nicht sieht - was eine Fremdbibliothek ueber logging
    # schreibt, und eine zweite Ausnahme in einem except-Block, die die ganze
    # Kette samt der urspruenglichen Bibliotheksmeldung druckt.
    install_log_redaction(logging.WARNING)
    install_excepthook_redaction()
    parser = argparse.ArgumentParser(
        description=t("cli_description")
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help=t("cli_help_all"))
    group.add_argument("--name", help=t("cli_help_name"))
    parser.add_argument("--host", help=t("cli_help_host"))
    args = parser.parse_args()

    # Fruehzeitige, klare Meldung statt eines rohen Tracebacks mitten im Lauf,
    # falls msmart-ng im aktiven Interpreter fehlt (verify_credentials importiert
    # es erst spaet per Lazy-Import). Bewusst ERST nach parse_args, damit --help
    # weiterhin ohne installiertes msmart funktioniert; und VOR jedem Cloud-Kontakt.
    try:
        import msmart  # noqa: F401  (reine Verfuegbarkeitspruefung)
    except ImportError:
        print(t("main_msmart_missing"), file=sys.stderr)
        sys.exit(1)

    config = load_config()
    devices = config.setdefault("devices", [])

    # Nicht-Objekt-Eintraege (nur durch Hand-Edit moeglich) koennen keine
    # Geraete sein: ueberspringen und melden, statt spaeter mit einem
    # AttributeError (d.get auf einem Nicht-Objekt) abzubrechen. Sie bleiben in
    # config erhalten - save_config schreibt sie unveraendert zurueck.
    valid_devices = [d for d in devices if isinstance(d, dict)]
    skipped = len(devices) - len(valid_devices)
    if skipped:
        print(t("main_skipped_entries", skipped, CONFIG_PATH.name))

    if args.all:
        if not valid_devices:
            print(t("main_no_devices"))
            sys.exit(1)
        targets = valid_devices
        new_entry = None
    else:
        targets = [d for d in valid_devices if d.get("name") == args.name]
        new_entry = None
        if not targets:
            if not args.host:
                print(t("main_new_needs_host", args.name))
                sys.exit(1)
            new_entry = {"name": args.name, "ip": args.host, "port": 6444, "id": "", "token": "", "key": ""}
            targets = [new_entry]

    ok = True
    successful_new_entry = False
    for idx, dev in enumerate(targets):
        # Auch zwischen zwei GERAETEN kurz entzerren (siehe DEVICE_DELAY).
        # Geraete desselben Modells haengen oft am selben Access Point; eine
        # ununterbrochene Folge von Verbindungsaufbauten belastet den unnoetig.
        # midea_ieco_ensure.py haelt aus demselben Grund eine Pause zwischen
        # den Geraeten ein.
        if idx > 0:
            time.sleep(DEVICE_DELAY)
        success = update_device(dev)
        ok = ok and success
        if dev is new_entry and success:
            successful_new_entry = True

    # Ein neues Geraet nur dann dauerhaft speichern, wenn der Abruf
    # tatsaechlich erfolgreich UND verifiziert war. Verhindert kaputte
    # Platzhalter-Eintraege in devices.json nach einem fehlgeschlagenen Versuch.
    if new_entry is not None and successful_new_entry:
        devices.append(new_entry)
    elif new_entry is not None and not successful_new_entry:
        print(t("main_new_not_saved", args.name))

    if new_entry is None or successful_new_entry:
        try:
            save_config(config)
        except OSError as exc:
            print(t("main_write_failed", type(exc).__name__, exc), file=sys.stderr)
            sys.exit(1)
        print(t("main_updated", CONFIG_PATH))

    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
