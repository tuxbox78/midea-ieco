#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_i18n.py

Gemeinsame Sprachwahl und Ausgabe-Hygiene fuer die Python-Werkzeuge von
midea-ieco.

Warum ein eigenes Modul: midea_ieco_ensure.py und midea_refresh_tokens.py
brauchen beide dieselbe Aufloesung. Zwei Kopien wuerden frueher oder spaeter
auseinanderlaufen - eine korrigierte Locale-Sonderform in der einen Datei, die
in der anderen fehlt. Die Kataloge bleiben bewusst bei den jeweiligen Modulen,
geteilt wird nur die Mechanik. Dasselbe Argument gilt fuer redact_hex(): auch
das Schwaerzen langer Hex-Werte darf nicht in zwei Fassungen auseinanderlaufen.

Die Aufloesung uebernimmt die REIHENFOLGE von resolve_lang() aus install.sh,
damit Installer und Laufzeit dieselbe Sprache sprechen:

    MIDEA_IECO_LANG  >  LC_ALL  >  LC_MESSAGES  >  LANG  >  Englisch

In vier Randfaellen ist diese Fassung bewusst grosszuegiger als die Shell-
Variante (jeweils zugunsten von Deutsch, nie umgekehrt), weil sie Werte
trimmt und einen zusaetzlichen Praefix kennt:

    LANG=de.UTF-8                 -> hier de, in install.sh en
    MIDEA_IECO_LANG=" de "        -> hier de, in install.sh en
    MIDEA_IECO_LANG="   " + LANG=de_DE  -> hier de, in install.sh en
    LC_ALL="   " + LANG=de_DE     -> hier de, in install.sh en

Alle regulaeren Faelle stimmen ueberein. Wer exakte Gleichheit braucht, setzt
MIDEA_IECO_LANG sauber auf 'de' oder 'en' - dann verhalten sich beide identisch.

Der Default ist ENGLISCH. Grund: das Projekt hat englischsprachige Nutzer, die
sonst deutsche Meldungen bekommen, die sie nicht lesen koennen - genau das ist
in Issue #2 passiert. Wer eine deutsche Locale hat (LANG=de_DE.UTF-8), bekommt
weiterhin Deutsch, ohne etwas konfigurieren zu muessen.

Hinweis fuer Cron: Cron-Jobs laufen oft ohne gesetzte Locale, dort greift also
der englische Default. Wer die Logs auf Deutsch moechte, setzt in der Crontab
MIDEA_IECO_LANG=de.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import sys
import traceback
from typing import Callable, Mapping

# Sprachkuerzel, die als Deutsch gelten. Als Praefixe gepruegt wird zusaetzlich
# 'de_', 'de-' und 'de.' (also de_DE.UTF-8, de-AT, de.UTF-8) - NICHT aber ein
# blosses 'de' am Wortanfang, sonst wuerde z.B. das daenische 'da_DK' oder ein
# hypothetisches 'dem...' faelschlich als Deutsch gelten.
_GERMAN_EXACT = frozenset({"de", "german", "deutsch"})
_GERMAN_PREFIXES = ("de_", "de-", "de.")

LANG_DE = "de"
LANG_EN = "en"


def resolve_lang() -> str:
    """Ermittelt die Ausgabesprache: 'de' oder 'en' (Default).

    Reihenfolge: MIDEA_IECO_LANG > LC_ALL > LC_MESSAGES > LANG > 'en'.
    Eine leere oder nur aus Leerzeichen bestehende Variable gilt als nicht
    gesetzt und faellt auf die naechste Stufe durch."""
    raw = (os.environ.get("MIDEA_IECO_LANG") or "").strip()
    if not raw:
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            value = (os.environ.get(name) or "").strip()
            if value:
                raw = value
                break
    raw = raw.lower()
    if raw in _GERMAN_EXACT or raw.startswith(_GERMAN_PREFIXES):
        return LANG_DE
    return LANG_EN


# Ein Token ist 128, ein Key 64 Hex-Zeichen lang. Die Schranke von 32 liegt
# darunter, damit auch ein am Schnittrand halbierter Wert noch faellt, und weit
# ueber allem, was hier sonst vorkommt: Geraete-IDs sind dezimal und rund 15
# Stellen lang, Appliance-Codes ebenso. Ein Rest von weniger als 32 Zeichen
# bleibt stehen - von einem 128er-Token fehlen dann mehr als 96, was ihn
# wertlos macht.
_HEX_RUN_RE = re.compile(r"[0-9a-fA-F]{32,}")


def redact_hex(text: str) -> str:
    """Ersetzt lange Hex-Laeufe durch '[hex:<laenge>]'.

    Warum ueberhaupt: die Cron-Zeilen leiten stderr per '2>&1' in dieselbe
    Logdatei, und die Logs entstehen mit der normalen umask (also meist 0644) -
    anders als devices.json, das ausdruecklich 0600 bekommt. Ein Token, das in
    einer Fehlermeldung landet, steht damit schwaecher geschuetzt als dasselbe
    Token in der Konfiguration. Betroffen sind vor allem zwei Wege: die rohe
    --debug-Ausgabe des discover-Aufrufs, die die tokenlist der Cloud enthaelt,
    und der unveraenderte Meldungstext einer Fremdbibliothek, die wir direkt
    nach authenticate(token, key) aufrufen.

    Die LAENGE bleibt sichtbar, weil sie das Einzige an einem Token ist, was
    bei der Fehlersuche hilft: '[hex:128]' in einer Meldung "kein tokenlist-
    Eintrag gefunden" sagt genau das Richtige - die Cloud hat etwas
    Token-foermiges geliefert, unsere Extraktion hat es nur nicht erkannt.

    Bewusst NICHT an die tokenlist-Regex gekoppelt: der teuerste Fall ist
    gerade der, in dem jene Regex versagt hat (Formatdrift) und deshalb der
    rohe Text ins Log geht. Eine Schwaerzung, die dieselbe Annahme teilt,
    versagte an genau derselben Stelle.

    Die Ausgabe ist idempotent - '[hex:128]' enthaelt selbst keinen Hex-Lauf
    von 32 Zeichen."""
    return _HEX_RUN_RE.sub(lambda m: "[hex:%d]" % len(m.group(0)), text)


class RedactingStreamHandler(logging.StreamHandler):
    """Ein StreamHandler, der seine fertige Zeile durch redact_hex schickt.

    Warum ueberhaupt ein eigener Handler: der Meldungskatalog deckt nur die
    Ausgaben der Werkzeuge selbst ab. Was eine Fremdbibliothek ueber das
    logging-Modul schreibt, geht daran vorbei - und msmart-ng 2026.7.0 gibt auf
    WARNING, also der hier eingestellten Stufe, in vier Faellen einen rohen
    Empfangspuffer der Geraeteverbindung aus ("Buffer: <hex>"). Per Cron landet
    das mit 2>&1 in einer Datei, die mit der normalen umask entsteht.

    Warum an dieser Stelle und nicht als Filter: ein logging.Filter am
    ROOT-Logger greift nicht - Records eines Kindloggers erreichen zwar die
    Handler des Roots, durchlaufen dessen Filter aber nicht (gemessen). Ein
    Filter am Handler wiederum muesste record.msg/record.args veraendern und
    damit ein Objekt, das sich alle Handler teilen. Und er liefe VOR emit():
    Handler.filter() steht ausserhalb von dessen try, eine Ausnahme dort - etwa
    aus record.getMessage() bei einem fehlerhaften Log-Aufruf der Bibliothek -
    beendet den ganzen Cron-Lauf. format() dagegen laeuft INNERHALB von emit(),
    laesst msg und args des geteilten Records unangetastet - das ist, was andere
    Handler lesen - und rendert zusaetzlich exc_info mit, faengt also den
    Traceback gleich mit ab (alles gemessen). Dass dabei record.message und
    record.exc_text entstehen, tut jeder Formatter; entscheidend ist, dass
    nichts umgeschrieben wird, worauf ein fremder Handler baut."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_hex(super().format(record))

    def handleError(self, record: logging.LogRecord) -> None:
        # Die Standardfassung schreibt bei einem fehlerhaften Log-Aufruf
        # record.msg und record.args ROH auf stderr - ein eigener Weg am
        # Formatter vorbei, gemessen mit einem Token darin.
        #
        # Statt eine eigene Meldung zu bauen, wird die der Standardfassung
        # abgefangen und geschwaerzt: sie enthaelt den 'Call stack' mit Datei
        # und Zeile des schuldigen Aufrufs, und genau die braucht ein
        # Fehlerbericht gegen die Bibliothek. Eine selbst formulierte Kurzform
        # hatte das verloren.
        #
        # Der ganze Block darf unter keinen Umstaenden werfen: er laeuft
        # bereits in der Fehlerbehandlung.
        if not logging.raiseExceptions or sys.stderr is None:
            return
        try:
            puffer = io.StringIO()
            with contextlib.redirect_stderr(puffer):
                super().handleError(record)
            sys.stderr.write(redact_hex(puffer.getvalue()))
        except Exception:
            pass


def install_log_redaction(level: int = logging.WARNING) -> None:
    """Haengt einen RedactingStreamHandler an den Root-Logger.

    Ersetzt logging.basicConfig() in den Werkzeugen. Zwei Gruende dafuer, dass
    BEIDE Werkzeuge das aufrufen muessen: ohne eigenen Handler bedient
    logging.lastResort die Records (ebenfalls stderr, ebenfalls ab WARNING,
    aber ungefiltert), und ein Handler, den wir selbst setzen, nimmt einer
    Bibliothek die Moeglichkeit, per eigenem basicConfig() zuerst zu kommen.

    Wird aus main() gerufen, NICHT beim Import - genau wie
    install_excepthook_redaction() und aus demselben Grund: der Root-Logger
    gehoert dem Prozess, nicht diesem Modul. Beim Import gerufen klemmte das
    Level einer einbettenden Anwendung von DEBUG auf WARNING herunter und
    verdoppelte ihre Zeilen (gemessen); logging.basicConfig(), das hier
    abgeloest wurde, tut bei vorhandenen Handlern bewusst gar nichts.

    Das Format ist ausdruecklich logging.BASIC_FORMAT, damit die Zeilen so
    aussehen wie bisher unter basicConfig - der Default eines blanken Handlers
    waere nur "%(message)s" und liesse Level und Loggernamen verschwinden.

    Fremde Handler werden NICHT entfernt: wer main() aus eigenem Code ruft,
    behaelt seine Ausgabe. Der ehrliche Preis dafuer, gemessen: er bekommt einen
    ZWEITEN Handler (Zeilen ab WARNING erscheinen doppelt) und seine Root-Stufe
    wird auf den hier uebergebenen Wert gesetzt. Das ist bewusst so - wer main()
    ruft, startet das Werkzeug, und dessen Waechter sollen dann auch greifen.
    Die Stufe wird nur beim ERSTEN Aufruf gesetzt, damit ein zweiter nicht eine
    zwischenzeitlich gewaehlte Stufe ueberschreibt."""
    root = logging.getLogger()
    if any(isinstance(h, RedactingStreamHandler) for h in root.handlers):
        return
    handler = RedactingStreamHandler()
    handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)


def install_excepthook_redaction() -> None:
    """Schwaerzt den Traceback einer Ausnahme, die niemand faengt.

    Der Weg ist ein anderer als der ueber logging und deshalb eigens noetig:
    scheitert eine zweite Ausnahme INNERHALB eines except-Blocks, druckt Python
    die ganze Kette samt der urspruenglichen Bibliotheksmeldung - also genau den
    Text, den der Meldungskatalog gerade geschwaerzt hat (gemessen).

    Wird bewusst erst in main() gesetzt, nicht beim Import: sys.excepthook ist
    prozessweit, und ein Import soll das Verhalten des Aufrufers nicht aendern.
    Schlaegt die Schwaerzung selbst fehl, uebernimmt der vorherige Hook - ein
    Traceback darf nie ganz verloren gehen, auch nicht bei einem Fehler hier."""
    if getattr(sys.excepthook, "_midea_redacting", False):
        return
    vorheriger = sys.excepthook

    def _hook(typ, wert, spur):  # noqa: ANN001  (Signatur von sys.excepthook)
        try:
            sys.stderr.write(redact_hex(
                "".join(traceback.format_exception(typ, wert, spur))))
        except Exception:
            vorheriger(typ, wert, spur)

    _hook._midea_redacting = True  # type: ignore[attr-defined]
    sys.excepthook = _hook


def _redact_arg(arg: object) -> object:
    """Schwaerzt ein einzelnes Platzhalter-Argument.

    Gibt das ORIGINAL zurueck, wenn nichts zu schwaerzen war: so bleibt der Typ
    erhalten und ein spaeter eingefuehrtes '%d' funktioniert weiter (heute
    enthalten beide Kataloge ausschliesslich '%s' - geprueft). Nur wenn ein
    Hex-Lauf wirklich getroffen wurde, wird aus dem Argument ein String, und
    ein 32-stelliger Hex-Wert in einem Zahlenplatzhalter waere ohnehin ein
    Fehler."""
    text = arg if isinstance(arg, str) else str(arg)
    redacted = redact_hex(text)
    return redacted if redacted != text else arg


def make_translator(catalog: Mapping[str, tuple[str, str]]) -> Callable[..., str]:
    """Liefert eine ``t(key, *args)``-Funktion fuer den uebergebenen Katalog.

    Der Katalog bildet ``key -> (englisch, deutsch)`` ab; Platzhalter im
    printf-Stil (%s) werden mit den uebergebenen Argumenten gefuellt.

    Die Sprache wird bei JEDEM Aufruf neu bestimmt und nicht beim Import
    zwischengespeichert: das haelt die Funktion frei von verstecktem Zustand und
    macht sie in Tests ohne Modul-Reload umschaltbar. Ein unbekannter Schluessel
    loest bewusst einen KeyError aus - ein Tippfehler soll im Test auffallen und
    nicht als leere Zeile im Log landen.

    JEDES eingesetzte Argument laeuft durch redact_hex(). Das geschieht
    absichtlich hier und nicht an den einzelnen Aufrufstellen: die Werkzeuge
    geben an ueber zwanzig Stellen fremden Text aus (Meldungen von
    msmart-ng, die rohe discover-Ausgabe, Ausnahmetexte), und eine Liste von
    Stellen, an die man denken muss, ist genau die Art Schutz, die beim
    naechsten neuen Aufruf vergessen wird. Der Katalogtext selbst wird NICHT
    angefasst - er stammt aus dem Repo und enthaelt keine Geheimnisse.

    Das deckt den Katalog ab, nicht die ganze Logdatei. Die beiden anderen Wege
    dorthin haben eigene Waechter: install_log_redaction() fuer das, was eine
    Fremdbibliothek ueber den ROOT-Logger schreibt (samt asyncio), und
    install_excepthook_redaction() fuer einen Traceback, den niemand faengt.
    Beide gehoeren zu diesem Modul und werden von beiden Werkzeugen in main()
    gesetzt - anders als diese Schwaerzung hier, die ohne Einrichtung wirkt.
    Ein Bibliotheks-Logger, der sich mit propagate=False abkoppelt und keinen
    eigenen Handler hat, erreicht den Root-Handler nicht und bleibt ungefiltert
    (tests/KNOWN_GAPS.md, Luecke 11).

    Was in KEINEM der drei Waechter faellt, ist derselbe Wert in einer anderen
    Darstellung: mit Trennzeichen, in Gruppen oder als bytes-Repr. Erkannt wird
    ZUSAMMENHAENGENDES Hex - die Form, in der bytes.hex() und die Cloud-Antwort
    es liefern. tests/KNOWN_GAPS.md (Luecke 11) fuehrt das aus."""

    def t(key: str, *args: object) -> str:
        english, german = catalog[key]
        text = german if resolve_lang() == LANG_DE else english
        return text % tuple(_redact_arg(a) for a in args) if args else text

    return t
