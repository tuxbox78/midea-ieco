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
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Verbindungsabbau: msmart-ng hat dafuer keine oeffentliche API - die Begruendung
# und der gemessene Befund stehen in midea_conn.py. Bis dorthin ausgelagert,
# damit der Griff in fremde Interna an EINER Stelle steht und dort gepruefte
# Waechter hat.
from midea_conn import close_connection

# Sprachwahl: gemeinsame Mechanik fuer beide Werkzeuge (Reihenfolge und
# englischer Default sind dort dokumentiert). resolve_lang wird mitimportiert,
# damit es weiterhin ueber dieses Modul erreichbar bleibt.
from midea_i18n import (install_excepthook_redaction,
                        install_line_buffered_stdout, install_log_redaction,
                        make_translator, resolve_lang)  # noqa: F401

# Dieses Werkzeug hat bisher gar kein Logging eingerichtet - dann bedient
# logging.lastResort die Records einer Fremdbibliothek, ebenfalls nach stderr
# und ebenfalls ab WARNING, nur ungefiltert. Der eigene Handler wird in main()
# gesetzt, nicht hier: ein blosser Import soll den Root-Logger des Aufrufers
# nicht umkonfigurieren (siehe install_log_redaction).

CONFIG_PATH = Path(__file__).parent / "devices.json"

# Buchhaltung des Token-Refresh: wann zuletzt ein vollstaendiger Lauf BEGONNEN
# hat und wann zuletzt einer vollstaendig GELUNGEN ist. Genau wie CONFIG_PATH am
# MODULVERZEICHNIS aufgehaengt, nicht am Arbeitsverzeichnis: Cron-Lauf und
# Wrapper starten mit beliebigem cwd, ein relativer Pfad legte je nach Aufrufort
# eine zweite Datei an - und der Nachhol-Lauf saehe nie einen Vorlauf.
#
# Die Datei enthaelt KEINE Geheimnisse (nur Zeitstempel), bekommt aber dieselben
# Rechte 0600 wie devices.json: sie liegt daneben, entsteht ueber denselben
# Schreibweg, und eine Ausnahme von "was dieses Werkzeug schreibt, gehoert dem
# Nutzer allein" braeuchte einen Grund, den es hier nicht gibt.
STATE_PATH = Path(__file__).parent / "refresh_state.json"
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

# Ab welchem Alter des letzten VOLLSTAENDIGEN Erfolgs ein Nachhol-Lauf
# (--only-if-due) faellig ist. Bewusst etwas unter dem Wochentakt des regulaeren
# Cron-Jobs (0 3 * * 0): ein Rechner, der kurz VOR dem Slot startet, soll
# nachholen statt ihn knapp zu verfehlen.
REFRESH_DUE_AFTER_SECONDS = 6 * 24 * 3600

# Untere Schranke zwischen zwei Nachhol-VERSUCHEN, unabhaengig vom Ausgang.
# Ohne sie liefe ein Rechner, der oft neu startet, bei jedem Start erneut gegen
# die Geraete - und '@reboot' heisst "der cron-Daemon wurde gestartet", nicht
# "das System wurde gebootet": ein Paket-Update des cron-Dienstes reicht dafuer
# bereits aus. Dieselbe Ruecksicht wie bei CANDIDATE_DELAY/DEVICE_DELAY, nur
# eine Ebene hoeher: eine dichte Folge von Verbindungsaufbauten ist genau das,
# was ein Midea-Geraet mit voruebergehender Funkstille quittiert.
REFRESH_RETRY_AFTER_SECONDS = 24 * 3600


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
        "[%s] WARNING: devices.json says id=%s, but the device reports id=%s. "
        "Existing value NOT overwritten.",
        "[%s] WARNUNG: In devices.json steht id=%s, aber das Geraet meldet id=%s. "
        "Bestehenden Wert NICHT ueberschrieben."),
    "dev_no_id": (
        "[%s] ERROR: No device ID available (neither in devices.json nor reported "
        "by the device). Entry will NOT be saved.",
        "[%s] FEHLER: Keine Geraete-ID verfuegbar (weder in devices.json noch vom "
        "Geraet gemeldet). Eintrag wird NICHT gespeichert."),
    # Ein von Hand in devices.json vertippter (o statt 0), als null oder (beim
    # port) als Infinity eingetragener id/port-Wert. Ohne diese beiden Meldungen
    # faellt er als ungefangener ValueError/TypeError/OverflowError durch und
    # reisst den ganzen --all-Lauf ab (siehe die Guards in update_device). Der
    # Rohwert steht per repr() sichtbar drin, damit die Fehlersuche den Schuldigen
    # findet - symmetrisch zu cfgchk_id_not_numeric/cfgchk_port_not_numeric im
    # Schwestermodul.
    "dev_id_not_numeric": (
        "[%s] ERROR: The configured device id (%s) is not a number - this device "
        "cannot be refreshed and is left unchanged.",
        "[%s] FEHLER: Die konfigurierte Geraete-id (%s) ist keine Zahl - dieses "
        "Geraet kann nicht aufgefrischt werden und bleibt unveraendert."),
    "dev_port_not_numeric": (
        "[%s] ERROR: The configured port (%s) is not a number - this device "
        "cannot be refreshed and is left unchanged.",
        "[%s] FEHLER: Der konfigurierte Port (%s) ist keine Zahl - dieses Geraet "
        "kann nicht aufgefrischt werden und bleibt unveraendert."),
    "dev_candidates_found": (
        "[%s] %s candidate(s) found, verifying one by one ...",
        "[%s] %s Kandidat(en) gefunden, verifiziere der Reihe nach ..."),
    # Bewusst NICHT "and saved": an dieser Stelle ist der Token erst IM SPEICHER
    # gesetzt; geschrieben wird devices.json EINMAL nach der Geraeteschleife
    # (save_config -> main_updated). "and saved" behauptete einen Schreibvorgang,
    # der beim Abbruch eines spaeteren Geraets nie stattfaende. Die echte
    # Schreibbestaetigung ist main_updated ganz am Ende.
    "dev_candidate_ok": (
        "[%s] Candidate %s/%s verified successfully.",
        "[%s] Kandidat %s/%s erfolgreich verifiziert."),
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
    "main_host_not_applied": (
        "Note: --host was not applied. It sets the IP of a SINGLE existing device "
        "selected by name (or creates a new one); with --all or a duplicated name "
        "every device keeps its stored IP.",
        "Hinweis: --host wurde nicht angewendet. Es setzt die IP eines EINZELNEN "
        "per Name gewaehlten bestehenden Geraets (oder legt ein neues an); bei "
        "--all oder einem doppelten Namen behaelt jedes Geraet seine gespeicherte "
        "IP."),
    "main_host_override": (
        "Updating the stored IP of '%s' from %s to %s "
        "(kept only if the refresh below succeeds).",
        "Aktualisiere die gespeicherte IP von '%s' von %s auf %s "
        "(wird nur behalten, wenn der Refresh unten gelingt)."),
    "main_new_not_saved": (
        "New device '%s' was NOT saved because the lookup failed.",
        "Neues Geraet '%s' wurde NICHT gespeichert, da der Abruf fehlgeschlagen ist."),
    "main_write_failed": (
        "ERROR: devices.json could not be written (%s: %s).",
        "FEHLER: devices.json konnte nicht geschrieben werden (%s: %s)."),
    "main_updated": (
        "devices.json updated: %s",
        "devices.json aktualisiert: %s"),
    # --- Nachhol-Lauf (--only-if-due) --------------------------------------
    "due_needs_all": (
        "ERROR: --only-if-due only works together with --all. The stored state "
        "records when ALL devices were last refreshed, so the question 'is it "
        "due?' has no meaning for a single device.",
        "FEHLER: --only-if-due funktioniert nur zusammen mit --all. Der "
        "gespeicherte Zustand haelt fest, wann ALLE Geraete zuletzt aufgefrischt "
        "wurden - fuer ein einzelnes Geraet hat die Frage 'ist es faellig?' "
        "keinen Bezug."),
    "due_skipped": (
        "Token refresh is not due yet - nothing done (last full refresh: %s, "
        "last attempt: %s).",
        "Token-Refresh ist noch nicht faellig - nichts getan (letzter "
        "vollstaendiger Lauf: %s, letzter Versuch: %s)."),
    "due_state_unreadable": (
        "WARNING: refresh_state.json could not be read (%s: %s) - treating the "
        "token refresh as due.",
        "WARNUNG: refresh_state.json konnte nicht gelesen werden (%s: %s) - der "
        "Token-Refresh gilt daher als faellig."),
    "due_state_bad_shape": (
        "WARNING: refresh_state.json does not contain a JSON object - treating "
        "the token refresh as due.",
        "WARNUNG: refresh_state.json enthaelt kein JSON-Objekt - der "
        "Token-Refresh gilt daher als faellig."),
    "state_write_failed": (
        "WARNING: refresh_state.json could not be written (%s: %s). The refresh "
        "itself is unaffected; only the catch-up run loses its record of it.",
        "WARNUNG: refresh_state.json konnte nicht geschrieben werden (%s: %s). "
        "Der Refresh selbst ist davon unberuehrt; nur dem Nachhol-Lauf fehlt "
        "spaeter der Vermerk darueber."),
    "state_never": (
        "never",
        "nie"),
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
        "IP address for --name: sets it for a new device, or updates a single "
        "existing one (on a successful refresh)",
        "IP-Adresse zu --name: setzt sie fuer ein neues Geraet oder aktualisiert "
        "ein einzelnes bestehendes (bei erfolgreichem Refresh)"),
    "cli_help_only_if_due": (
        "Only refresh if the last full refresh is overdue (for the @reboot "
        "catch-up job; requires --all)",
        "Nur auffrischen, wenn der letzte vollstaendige Lauf ueberfaellig ist "
        "(fuer den @reboot-Nachholer; erfordert --all)"),
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

# Geraete-ID (device_id) aus derselben --debug-Ausgabe. midea-local loggt sie in
# discover.py als Python-dict-repr:
#   [midealocal.discover] Found a supported device: {'device_id': N, 'type': .., ..}
# NICHT als "applianceCodes" - dieses Feld (Plural) erzeugt die Bibliothek
# NIRGENDS (grep ueber midea-local 6.6.1: null Treffer). Die fruehere Regex
# darauf lieferte daher IMMER None und machte 'discover'-basiertes Hinzufuegen
# eines neuen Geraets ('--name X --host Y') strukturell unmoeglich.
# Das Literal 'device_id' kommt in der Ausgabe nur in genau dieser Zeile vor:
# die get_keys-Zeile nennt 'appliance_id', die Cloud-Rohantwort 'udpId'/'userId',
# und die Hex-Dumps koennen 'device_id' gar nicht buchstabieren (Hex-Alphabet).
# Die Extraktion ist damit kollisionsfrei (Guard B). Das '['\"]?'-Muster deckt
# sowohl die dict-repr-Form ('device_id': N) als auch eine etwaige JSON-Form
# ("device_id": N) ab.
DEVICE_ID_RE = re.compile(r"device_id['\"]?\s*[:=]\s*['\"]?(\d+)")


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


# =============================================================================
# Nachhol-Lauf: Zustand lesen, bewerten, fortschreiben
#
# Wozu das Ganze: der regulaere Cron-Job frischt die Tokens woechentlich auf
# (0 3 * * 0). Ein Rechner, der zu dieser Zeit ausgeschaltet war, ueberspringt
# den Termin STILL - und abgelaufene Tokens sind der haeufigste Weg, auf dem
# dieses Werkzeug im Feld ausfaellt. Eine zusaetzliche '@reboot'-Zeile holt den
# Lauf beim naechsten Start nach; damit sie nicht bei JEDEM Start gegen die
# Geraete arbeitet, entscheidet refresh_is_due anhand der beiden hier
# gefuehrten Zeitstempel.
# =============================================================================

def read_refresh_state() -> dict:
    """Liest refresh_state.json und liefert IMMER ein Dict.

    Anders als load_config() bricht das hier NICHT ab: die Datei ist reine
    Buchhaltung dieses Werkzeugs, kein Konfigurationsdokument des Nutzers. Jeder
    Zweifelsfall - unlesbar, kein JSON, kein Objekt - ergibt einen LEEREN
    Zustand, und ein leerer Zustand gilt weiter unten als "noch nie gelaufen".
    Das ist die sichere Richtung: der Nachhol-Lauf arbeitet dann einmal zu viel
    (harmlos, und durch REFRESH_RETRY_AFTER_SECONDS gedeckelt), statt
    stillschweigend auszusetzen - Letzteres ist genau der Fehler, gegen den
    dieses Feature gebaut ist.

    Eine FEHLENDE Datei ist der Normalfall vor dem ersten vollstaendigen Lauf
    und bleibt deshalb still. Gemeldet wird nur eine vorhandene, aber
    unbrauchbare."""
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError ab
        print(t("due_state_unreadable", type(exc).__name__, exc), file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(t("due_state_bad_shape"), file=sys.stderr)
        return {}
    return data


def _state_age_seconds(state: dict, key: str, now: float) -> float | None:
    """Alter des Zeitstempels ``key`` in Sekunden - oder None, wenn unbrauchbar.

    Unbrauchbar heisst: Schluessel fehlt, Wert ist kein int/float, ist ein bool
    (isinstance(True, int) ist in Python WAHR - ein von Hand eingetragenes
    ``true`` darf nicht als Epoche 1 durchgehen) oder ist nicht endlich (Pythons
    json-Modul liest ``NaN`` und ``Infinity`` klaglos ein).

    Ein NEGATIVES Ergebnis - Zeitstempel in der Zukunft, weil die Uhr
    zurueckgestellt wurde oder der Rechner ohne RTC erst nach dem Booten eine
    richtige Zeit bekommt - wird hier bewusst NICHT aufgeloest: die beiden
    Aufrufer in refresh_is_due brauchen darauf gegenlaeufige Antworten."""
    value = state.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Erst nach float wandeln, DANN auf Endlichkeit pruefen. math.isfinite auf
    # einem riesigen int (json.load liest beliebig lange Ganzzahlliterale
    # klaglos) wandelt intern selbst nach float und wirft dabei OverflowError -
    # ungefangen sonst genau im --only-if-due-Pfad, also bei jedem Start. Eine
    # Zahl, die sich nicht als float darstellen laesst, ist als Epoche ohnehin
    # unbrauchbar; unbrauchbar heisst hier None, und None gilt oben als
    # "faellig"/"erlaubt" - die sichere Richtung.
    try:
        fv = float(value)
    except OverflowError:
        return None
    if not math.isfinite(fv):
        return None
    return now - fv


def refresh_is_due(now: float, state: dict) -> bool:
    """Soll jetzt ein Nachhol-Refresh laufen?

    Rein: keine Uhr, kein Dateisystem, keine Ausgabe - jeder Grenzfall ist damit
    ohne Wartezeit und ohne Wanduhr pruefbar.

    Zwei Bedingungen muessen BEIDE erfuellt sein:

      1. Faelligkeit   - der letzte VOLLSTAENDIGE Erfolg liegt mindestens
                         REFRESH_DUE_AFTER_SECONDS zurueck (oder ist unbekannt).
      2. Sturmwaechter - der letzte VERSUCH liegt mindestens
                         REFRESH_RETRY_AFTER_SECONDS zurueck (oder ist unbekannt).

    Ohne (2) liefe ein oft neu startender Rechner bei jedem Start erneut gegen
    die Geraete, denn (1) allein bliebe erfuellt, solange kein Lauf GELINGT -
    also gerade im Stoerungsfall, in dem das am meisten schadet.

    Unbrauchbare oder unplausible Werte werden GEGENLAEUFIG aufgeloest, jeweils
    zugunsten der Geraete:

      * last_success unbrauchbar oder in der Zukunft -> faellig. Einem Wert, dem
        man nicht trauen kann, darf kein Refresh geopfert werden.
      * last_attempt unbrauchbar -> erlaubt; sonst blockierte ein kaputter
        Zustand den Nachholer dauerhaft.
      * last_attempt bis zu REFRESH_RETRY_AFTER_SECONDS in der Zukunft ->
        blockiert, als waere eben ein Versuch gelaufen. So bleibt der
        Sturmwaechter wirksam, wenn die Uhr NACH einem echten Lauf ein Stueck
        zurueckspringt (NTP-Korrektur).
      * last_attempt WEITER als REFRESH_RETRY_AFTER_SECONDS in der Zukunft ->
        erlaubt. Ein so weit vorausliegender Stempel ist nicht "eben gelaufen",
        sondern unplausibel (Rechner ohne RTC, dessen Uhr beim Booten weit hinter
        einem frueher mit korrekter Zeit geschriebenen Stempel liegt). Ihn
        blockieren zu lassen hiesse, den Nachholer auf genau dem Rechnertyp
        dauerhaft stillzulegen, fuer den er gebaut ist - die fruehere Fassung tat
        das.

    Das Blockfenster fuer last_attempt ist damit symmetrisch: [-RETRY, +RETRY).
    Der bewusste Tausch dahinter: die fruehere Fassung schwieg bei einem
    weit-zukuenftigen Stempel FUER IMMER (die schlimmste Richtung); diese laeuft
    stattdessen. Der Preis steht in die andere Richtung und ist NICHT immer
    selbstheilend: setzt sich die Uhr auf einen festen falschen Wert, blockiert
    der nun geschriebene last_attempt die folgenden Starts wie vorgesehen; SPRINGT
    die Uhr dagegen bei jedem Boot erneut um mehr als einen Wiederhol-Abstand,
    laeuft der Nachholer bei jedem Boot (statt ewig zu schweigen). Beides ist dem
    Dauerschweigen vorzuziehen, aber die Every-Boot-Verbindung ist ein realer Rand
    und steht in KNOWN_GAPS - ohne vertrauenswuerdige Zeitquelle beim '@reboot'
    ist er nicht sauber von einem echten kuerzlichen Versuch zu trennen.

    Entschieden wird ausschliesslich ueber die ``*_epoch``-Zahlen; die
    ``*_utc``-Zeichenketten daneben sind reine Lesehilfe."""
    success_age = _state_age_seconds(state, "last_success_epoch", now)
    if success_age is not None and 0 <= success_age < REFRESH_DUE_AFTER_SECONDS:
        return False
    attempt_age = _state_age_seconds(state, "last_attempt_epoch", now)
    if (attempt_age is not None
            and -REFRESH_RETRY_AFTER_SECONDS <= attempt_age < REFRESH_RETRY_AFTER_SECONDS):
        return False
    return True


def _stamp(state: dict, prefix: str, now: float) -> None:
    """Setzt ``<prefix>_epoch`` und ``<prefix>_utc`` auf ``now``.

    Zwei Felder mit Absicht: entschieden wird ueber die Epoche (eine Zahl, ohne
    Zeitzonen- und Parser-Randfaelle), die UTC-Zeichenkette steht rein zum
    Mitlesen daneben - im Log und bei einer Fehlersuche.

    Die Zeichenkette wird defensiv gebildet: bei einer voellig unsinnigen
    Systemzeit wirft datetime.fromtimestamp (OverflowError/OSError/ValueError).
    Dann bleibt das Feld leer und wird als "nie" angezeigt - die ENTSCHEIDUNG
    haengt an der Epoche und bleibt davon unberuehrt. Ein Refresh darf nicht an
    der Formatierung seines eigenen Protokolls scheitern."""
    state[f"{prefix}_epoch"] = int(now)
    try:
        state[f"{prefix}_utc"] = datetime.fromtimestamp(
            now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        state[f"{prefix}_utc"] = ""


def _write_refresh_state(state: dict) -> None:
    """Schreibt den Zustand atomar und mit Rechten 0600 (_atomic_write_json).

    Ein Schreibfehler wird GEMELDET, aber nicht weitergereicht: diese Datei ist
    Buchhaltung. Ein gelungener Token-Refresh darf nicht daran scheitern, dass
    sein Vermerk nicht abgelegt werden konnte - der Exit-Code des Laufs bleibt
    exakt der, den er ohne dieses Feature haette. Gefangen wird NUR OSError;
    alles andere (etwa ein nicht serialisierbarer Wert) waere ein Fehler in
    diesem Modul und soll in den Tests laut scheitern statt zu verschwinden."""
    try:
        _atomic_write_json(STATE_PATH, state)
    except OSError as exc:
        print(t("state_write_failed", type(exc).__name__, exc), file=sys.stderr)


def record_refresh_attempt(state: dict, now: float) -> None:
    """Haelt fest, dass JETZT ein vollstaendiger Lauf BEGINNT.

    Bewusst VOR der Geraeteschleife aufgerufen, nicht danach: ein Lauf, der
    unterwegs scheitert (kein Netz kurz nach dem Booten, ein dauerhaft
    unerreichbares Geraet), hinterliesse sonst nichts - und der Sturmwaechter
    haette bei jedem Neustart wieder freie Bahn. Genau dieser Fall ist der
    Grund, dass es neben last_success ueberhaupt ein last_attempt gibt.

    ``state`` wird ERGAENZT, nie ersetzt: ein hier neu gebautes Dict wuerde ein
    vorhandenes last_success ueberschreiben - der Nachhol-Lauf hielte sich dann
    dauerhaft fuer faellig und liefe alle REFRESH_RETRY_AFTER_SECONDS."""
    _stamp(state, "last_attempt", now)
    _write_refresh_state(state)


def record_refresh_success(state: dict, now: float) -> None:
    """Haelt fest, dass gerade ein vollstaendiger Lauf GELUNGEN ist.

    "Vollstaendig" heisst: alle Zielgeraete erfolgreich UND devices.json
    geschrieben. Ein Teilerfolg schreibt hier bewusst nichts - er belegt nicht,
    dass die Flotte frische Tokens hat, und duerfte den naechsten Nachholer
    daher nicht unterdruecken.

    Arbeitet auf demselben Dict wie record_refresh_attempt, ohne die Datei
    erneut zu lesen, damit ein Lauf sich nicht selbst ueberholt. Folge fuer zwei
    gleichzeitig laufende Prozesse: der spaetere Schreiber gewinnt. Das ist
    hinnehmbar - beide haben dann tatsaechlich gerade aufgefrischt."""
    _stamp(state, "last_success", now)
    _write_refresh_state(state)


def _state_stamp_text(state: dict, key: str) -> str:
    """Lesbare Fassung eines Zeitstempels fuer die Ausgabe, "nie" wenn keine
    brauchbare vorliegt. Rein informativ - entschieden wird ueber die Epoche."""
    value = state.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return t("state_never")


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
    """Extrahiert (key, token)-Kandidaten und die Geraete-ID (device_id) aus einem
    discover-Ergebnis. Wirft RuntimeError bei Nicht-Null-Exit oder fehlender
    tokenlist. Die device_id ist None, wenn die Ausgabe keine (nicht-null)
    Geraete-ID enthaelt (siehe DEVICE_ID_RE, Guard A)."""
    combined_output = result.stdout + result.stderr
    if result.returncode != 0:
        tail = combined_output[-800:] if combined_output else t("err_no_output")
        raise RuntimeError(t("err_discover_exit", result.returncode, tail))
    matches = extract_token_key_pairs(combined_output)
    if not matches:
        tail = combined_output[-800:] if combined_output else t("err_no_output")
        raise RuntimeError(t("err_no_tokenlist", tail))
    # Guard A: ERSTER NICHT-NULL-Treffer. midea-locals discover.py liefert im
    # Protokoll-1-Pfad (get_id_from_response) device_id=0, wenn das Geraet keine
    # ID meldet; ein gespeichertes id=0 waere ein nie verifizierbarer Bogus-
    # Eintrag. Kein Treffer -> None -> der Aufrufer meldet sauber "keine ID".
    device_ids = DEVICE_ID_RE.findall(combined_output)
    return matches, next((d for d in device_ids if int(d) != 0), None)


def fetch_candidate_credentials(host: str) -> tuple[list[tuple[str, str]], str | None]:
    """Ruft discover --debug auf und gibt ALLE gefundenen (key, token)-
    Kandidaten zurueck, sowie die per UDP-discovery gemeldete Geraete-ID
    (device_id, falls vorhanden).
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


def update_device(dev_conf: dict, host_override: str | None = None) -> bool:
    """Frischt Token/Key EINES Geraets auf: holt Kandidaten per credential-freiem
    discover, verifiziert sie der Reihe nach gegen das echte Geraet und speichert
    das erste funktionierende (key, token)-Paar in-place in dev_conf. Rueckgabe
    True bei Erfolg, sonst False. Token, Key und (bei host_override) die IP werden
    NUR bei erfolgreicher Verifikation geschrieben - schlaegt alles fehl, bleiben
    sie unveraendert (kein kaputter Wert nach einem Fehlversuch). EINZIGE Ausnahme:
    ein noch leeres 'id'-Feld wird mit der von discover gemeldeten Geraete-ID
    gefuellt, AUCH wenn die Verifikation danach scheitert - diese ID stammt von
    einem Geraet, das geantwortet hat, ist also kein kaputter Platzhalter, sondern
    beim naechsten Lauf direkt nutzbar.

    host_override (optional): eine per '--host' explizit vorgegebene IP fuer ein
    BESTEHENDES Geraet. Sie wird fuer discover+verify verwendet und - konsistent
    mit Token/Key - NUR bei erfolgreicher Verifikation nach dev_conf['ip']
    uebernommen. Schlaegt der Refresh an der neuen IP fehl, bleibt die bisher
    gespeicherte IP unveraendert (keine vertippte, unerreichbare IP in
    devices.json)."""
    name = dev_conf.get("name", t("dev_unknown_name"))
    host = host_override or dev_conf.get("ip")
    if not host:
        print(t("dev_no_ip", name))
        return False

    print(t("dev_fetching", name, host))
    try:
        candidates, appliance_id = fetch_candidate_credentials(host)
    except RuntimeError as exc:
        print(t("dev_fetch_failed", name, exc))
        return False

    # appliance_id ist die vom Geraet per UDP-discovery gemeldete device_id
    # (siehe DEVICE_ID_RE / _parse_discover_output). Der Name bleibt bewusst
    # appliance_id, weil weiter unten bereits ein lokales 'device_id' existiert.
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
    # devices.json wird laut beiden READMEs von Hand gepflegt; eine ~15-stellige
    # dezimale id (o statt 0, l statt 1) ist dabei der realistischste Vertipper.
    # device_id_str ist per Konstruktion (Zeile oben: str(...).strip()) bereits
    # ein String -> int(str) wirft ausschliesslich ValueError, nie TypeError.
    # Ohne diesen Guard reisst eine einzige nicht-numerische id als ungefangener
    # ValueError den GANZEN --all-Lauf ab und verwirft die schon im Speicher
    # aufgefrischten Token aller anderen Geraete (save_config laeuft erst nach der
    # Schleife). Bewusst return False, nicht raise: ein vertippter Eintrag ist ein
    # Geraetefehler wie 'keine IP' weiter oben - er faellt sauber durch (-> Exit 2)
    # und laesst die uebrigen Geraete weiterlaufen und speichern. Das Schwester-
    # modul schuetzt exakt diese Felder vorab (_device_config_problem); hier sitzt
    # derselbe Schutz an der Konversionsstelle. repr() auf dem ROHwert (nicht auf
    # device_id_str) haelt 'null' (-> None) von der Zeichenkette "None"
    # unterscheidbar und zeigt den schuldigen Wert in der Meldung.
    try:
        device_id = int(device_id_str)
    except ValueError:
        print(t("dev_id_not_numeric", name, repr(dev_conf.get("id"))))
        return False
    # port: der Default 6444 greift NUR bei FEHLENDEM Schluessel - "port": null
    # liefert None (nicht 6444) und damit int(None) -> TypeError; ein vertippter
    # "64o4" -> ValueError. Und weil Pythons json die blanken Literale Infinity/
    # -Infinity klaglos als float('inf') einliest, wirft int() darauf einen
    # OverflowError - der ist KEIN Subtyp von ValueError. Alle drei muessen
    # daher gefangen werden; damit deckt der Guard jeden aus JSON moeglichen Wert
    # ab. Anders als bei der id oben, deren Rohwert hier schon zum String
    # normalisiert wurde und die daher nur ValueError liefern kann.
    try:
        port = int(dev_conf.get("port", 6444))
    except (TypeError, ValueError, OverflowError):
        print(t("dev_port_not_numeric", name, repr(dev_conf.get("port"))))
        return False

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
            # Eine per --host vorgegebene neue IP erst JETZT uebernehmen - genau
            # wie Token/Key nur bei bewiesener Erreichbarkeit, nie nach einem
            # Fehlversuch (sonst landete eine vertippte IP dauerhaft in der Datei).
            if host_override is not None:
                dev_conf["ip"] = host_override
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
    Erfolg, 2 = mindestens ein Geraet fehlgeschlagen (auch ein Eintrag mit
    nicht-numerischer id/port zaehlt als Fehlschlag), 1 = Nutzungs-/Konfig-Fehler
    (msmart fehlt, leere Geraeteliste bei --all, neues Geraet ohne --host,
    --only-if-due ohne --all, Schreibfehler).

    Ein --all-Lauf fuehrt zusaetzlich refresh_state.json fort: den Versuch vor
    der Geraeteschleife, den Erfolg ganz am Ende. Mit --only-if-due wird dieser
    Zustand vorher ausgewertet und der Lauf ggf. mit Exit 0 uebersprungen (siehe
    refresh_is_due)."""
    # stdout auf Zeilenpufferung, BEVOR irgendetwas ausgegeben wird: unter Cron
    # (>> log 2>&1) ist stdout sonst blockgepuffert und die eigene Statusausgabe
    # erschiene gesammelt am Prozessende, hinter der laufenden stderr-Diagnostik
    # der Bibliothek - Ursache und Wirkung vertauscht. Warum nur stdout, warum
    # hier: Docstring von install_line_buffered_stdout().
    install_line_buffered_stdout()
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
    parser.add_argument("--only-if-due", action="store_true",
                        help=t("cli_help_only_if_due"))
    args = parser.parse_args()

    # --only-if-due ist eine Aussage ueber die GESAMTE Flotte: der Zustand haelt
    # fest, wann zuletzt ALLE Geraete aufgefrischt wurden. Mit --name haette die
    # Frage "ist es faellig?" keinen Bezug - lieber klar ablehnen als eine
    # unklare Bedeutung anbieten.
    #
    # Exit 1 (Nutzungsfehler) statt parser.error(), das mit 2 endet: 2 ist in
    # diesem Werkzeug fuer "mindestens ein Geraet fehlgeschlagen" vergeben, und
    # ein Monitoring, das darauf achtet, soll einen Tippfehler in der Crontab
    # nicht als Geraeteausfall melden.
    if args.only_if_due and not args.all:
        print(t("due_needs_all"), file=sys.stderr)
        sys.exit(1)

    # Zustand nur bei --all lesen und spaeter fortschreiben: ein Lauf auf ein
    # einzelnes Geraet sagt nichts ueber die Flotte und darf den Vermerk daher
    # weder auswerten noch veraendern.
    refresh_state = read_refresh_state() if args.all else {}

    # Der Ausstieg liegt VOR der msmart-Pruefung und vor load_config(): ein
    # nicht faelliger Lauf soll so wenig wie moeglich tun - kein msmart-Import,
    # kein Lesen von devices.json, kein Netzkontakt. refresh_state.json wurde
    # oben bereits gelesen; ohne das gaebe es nichts zu entscheiden. Exit 0,
    # damit cron ihn nicht als Fehler meldet; er ist ja das erwartete Ergebnis.
    if args.only_if_due and not refresh_is_due(time.time(), refresh_state):
        print(t("due_skipped",
                _state_stamp_text(refresh_state, "last_success_utc"),
                _state_stamp_text(refresh_state, "last_attempt_utc")))
        sys.exit(0)

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

    # --host beim Anlegen eines NEUEN Geraets ist bereits in new_entry["ip"]
    # eingeflossen. Fuer ein BESTEHENDES, eindeutig per Name gewaehltes Geraet
    # aendert --host die gespeicherte IP (host_override; uebernommen aber nur bei
    # erfolgreichem Refresh, siehe update_device). Bei --all oder einem
    # mehrdeutigen (doppelten) Namen laesst sich EINE IP nicht sinnvoll zuordnen -
    # dann wird --host sichtbar NICHT angewendet.
    host_override = None
    if args.host and new_entry is None:
        if not args.all and len(targets) == 1:
            existing = targets[0]
            if str(args.host) != str(existing.get("ip", "")):
                host_override = args.host
                print(t("main_host_override", existing.get("name", args.name),
                        existing.get("ip", ""), args.host))
            # gleiche IP wie gespeichert: nichts zu tun, still
        else:
            print(t("main_host_not_applied"))

    # Ab hier wird tatsaechlich gegen die Geraete gearbeitet - den Versuch JETZT
    # vermerken, nicht erst am Ende (Begruendung in record_refresh_attempt).
    # Erst hier und nicht frueher: ein --all auf eine leere devices.json bricht
    # oben mit Exit 1 ab, ohne ein Geraet beruehrt zu haben, und waere als
    # "Versuch" gezaehlt eine Sperre ohne Anlass.
    if args.all:
        record_refresh_attempt(refresh_state, time.time())

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
        # host_override ist per Konstruktion nur bei GENAU einem Zielgeraet
        # gesetzt (Einzel-Bestand mit abweichender --host-IP); der bedingte
        # Aufruf haelt den Normalpfad bei der bisherigen 1-Argument-Signatur.
        if host_override is not None:
            success = update_device(dev, host_override=host_override)
        else:
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

    # Erst hier: "vollstaendig gelungen" heisst ALLE Zielgeraete erfolgreich UND
    # devices.json geschrieben. Ein Schreibfehler verlaesst main() oben bereits
    # mit Exit 1, ein Teilerfolg ist durch 'ok' ausgeschlossen. Eigener
    # Zeitstempel statt des Entscheidungs-"jetzt" von oben: das Feld
    # protokolliert, WANN der Lauf gelungen ist.
    if args.all and ok:
        record_refresh_success(refresh_state, time.time())

    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
