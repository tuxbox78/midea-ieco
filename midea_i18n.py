#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_i18n.py

Gemeinsame Sprachwahl fuer die Python-Werkzeuge von midea-ieco.

Warum ein eigenes Modul: midea_ieco_ensure.py und midea_refresh_tokens.py
brauchen beide dieselbe Aufloesung. Zwei Kopien wuerden frueher oder spaeter
auseinanderlaufen - eine korrigierte Locale-Sonderform in der einen Datei, die
in der anderen fehlt. Die Kataloge bleiben bewusst bei den jeweiligen Modulen,
geteilt wird nur die Mechanik.

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

import os
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


def make_translator(catalog: Mapping[str, tuple[str, str]]) -> Callable[..., str]:
    """Liefert eine ``t(key, *args)``-Funktion fuer den uebergebenen Katalog.

    Der Katalog bildet ``key -> (englisch, deutsch)`` ab; Platzhalter im
    printf-Stil (%s) werden mit den uebergebenen Argumenten gefuellt.

    Die Sprache wird bei JEDEM Aufruf neu bestimmt und nicht beim Import
    zwischengespeichert: das haelt die Funktion frei von verstecktem Zustand und
    macht sie in Tests ohne Modul-Reload umschaltbar. Ein unbekannter Schluessel
    loest bewusst einen KeyError aus - ein Tippfehler soll im Test auffallen und
    nicht als leere Zeile im Log landen."""

    def t(key: str, *args: object) -> str:
        english, german = catalog[key]
        text = german if resolve_lang() == LANG_DE else english
        return text % args if args else text

    return t
