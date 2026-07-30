# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_apply.py

Schreibt AUSSCHLIESSLICH die iECO-Property an ein msmart-ng-Geraet, ohne den
vollen Zustandsbefehl.

WARUM ES DIESES MODUL GIBT
--------------------------
``AirConditioner.apply()`` sendet IMMER zuerst den vollen Zustand
(power/target_temperature/operational_mode/fan/swing/eco/... via
``SetStateCommand``) und erst danach - wenn ueberhaupt - die Property-Kommandos
(``msmart/device/AC/device.py``). Soll nur iECO an einer bereits LAUFENDEN Anlage
nachgezogen werden, ist dieser Voll-Zustands-Write unnoetig UND gefaehrlich:
jedes Feld, das auf einem ``__init__``-Default steht, wird zu einem echten
Befehl - genau der Reconnect-Clobber (laufende Anlage aus + 17 C/AUTO), den
dieses Projekt an anderer Stelle bereits abgestellt hat.

msmart-ngs eigener Property-Pfad, ``SetPropertiesCommand``, traegt KEIN
power/temp/mode. Ein reiner iECO-Property-Write kann diese Felder damit gar nicht
erst ueberschreiben - der Schutz ist strukturell, nicht nur durch Waechter.

Einen OEFFENTLICHEN Weg dafuer gibt es nicht: ``apply()`` ist der einzige
oeffentliche Schreiber, und er sendet den Zustandsrahmen immer zuerst. Der
Property-Pfad benutzt deshalb die PRIVATE Kette
``device._apply_properties({...})`` mit den Properties, die msmart genauso aus
``device._PROPERTY_MAP`` und ``device._updated_properties`` baut, wie ``apply()``
es intern fuer seine Property-Runde tut. Dieses Modul kapselt diesen Griff in
fremde Interna an EINER bewachten, versionsgepinnten Stelle - so wie
``midea_conn.py`` den Verbindungsabbau kapselt - und ``tests/test_apply.py``
sichert den Vertrag gegen die ECHTE Bibliothek ab.

BEWUSST NICHT BLIND AKTIVIERT
-----------------------------
OB ein Geraet iECO aus einem reinen Property-Write UEBERNIMMT (manche Firmware
beantwortet Property-Writes mit einem Fehler oder einer anderen Property-ID), ist
eine Geraetefrage - sie laesst sich nur an echter Hardware klaeren. Die Mitnahme
in ``ensure_ieco`` erfolgt deshalb erst, nachdem der Weg on-device belegt ist
(siehe Plan Phase 5). Bis dahin ist ``apply_ieco_only`` die getestete, gegen
msmart-ng vertraglich abgesicherte Bausteinfunktion.

WARUM EIN RUECKGABEWERT
-----------------------
``True``  = der Property-Write wurde ABGESETZT.
``False`` = die private Kette ist nicht (mehr) verfuegbar; der Aufrufer faellt
            dann auf ``device.apply()`` zurueck (der durch den Reconcile in
            ensure_ieco gegen den Clobber abgesichert ist - der Rueckfall kann
            den Fehler also nicht wieder oeffnen).
Netz-/Protokollfehler aus dem Write selbst werden NICHT geschluckt, sondern
durchgereicht: der Aufrufer behandelt sie wie einen fehlgeschlagenen ``apply()``
(schliessen, neu verbinden, neu validieren, wiederholen).

TODO: entfaellt, sobald msmart-ng einen oeffentlichen Weg bietet, eine einzelne
Property ohne vorangehenden Zustandsbefehl zu setzen. Der Vertragstest fordert
den Rueckbau dann ein.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from midea_i18n import make_translator
# PINNED_MSMART_VERSION kommt aus midea_conn und wird NICHT kopiert: es gibt
# genau eine Fassung, gegen die dieses Projekt nachgemessen hat (siehe die
# gleichlautende Begruendung in midea_ieco_ensure.py).
from midea_conn import PINNED_MSMART_VERSION

if TYPE_CHECKING:
    from msmart.device.AC.device import AirConditioner as AC

_LOGGER = logging.getLogger(__name__)


# Die privaten Geraete-Member, aus denen der iECO-Property-Write gebaut wird.
# Als Konstante und nicht fest verdrahtet, damit die Warnung genau DIESE Namen
# nennt und der Vertragstest denselben Satz prueft statt einer Kopie.
PROPERTY_WRITE_MEMBERS = ("_apply_properties", "_PROPERTY_MAP",
                          "_updated_properties")


_MESSAGES: dict[str, tuple[str, str]] = {
    "prop_path_gone": (
        "WARNING: msmart-ng no longer offers the internal property-write path "
        "(device.%s missing or of an unexpected type; verified against "
        "msmart-ng %s). Falling back to a full apply() - please report this.",
        "WARNUNG: msmart-ng bietet den internen Property-Write-Pfad nicht mehr "
        "(device.%s fehlt oder hat einen unerwarteten Typ; geprueft gegen "
        "msmart-ng %s). Rueckfall auf ein volles apply() - bitte melden."),
    "prop_path_sync": (
        "WARNING: msmart-ng's internal device._apply_properties is no longer a "
        "coroutine (verified as a coroutine against msmart-ng %s). Falling back "
        "to a full apply() - please report this.",
        "WARNUNG: msmart-ngs internes device._apply_properties ist keine "
        "Koroutine mehr (als Koroutine geprueft gegen msmart-ng %s). Rueckfall "
        "auf ein volles apply() - bitte melden."),
}

t = make_translator(_MESSAGES)


async def apply_ieco_only(device: "AC") -> bool:
    """Setzt NUR die bereits am Objekt scharfgeschalteten Property-Werte (im
    --only-if-on-Nudge ist das genau iECO), ohne den vollen Zustandsbefehl.

    Voraussetzung: der Aufrufer hat den gewuenschten Property-Zustand bereits am
    Objekt gesetzt (z.B. ``device.ieco = True``), sodass msmart die Property in
    ``device._updated_properties`` gefuehrt hat - genauso, wie es der Erstpfad
    vor ``apply()`` tut.

    Rueckgabe:
        True  - der Property-Write wurde abgesetzt.
        False - die private Kette ist nicht verfuegbar (Warnung ist geloggt),
                oder es ist keine property-basierte Aenderung scharfgeschaltet;
                der Aufrufer faellt dann auf apply() zurueck.

    Wirft NICHT aus dem Aufloesen der Kette (das wuerde in der Retry-Schleife als
    Verbindungsfehler fehlgedeutet). Ein Netz-/Protokollfehler aus dem Write
    selbst wird dagegen bewusst DURCHGEREICHT - der Aufrufer behandelt ihn wie
    einen fehlgeschlagenen apply()."""
    apply_props = getattr(device, "_apply_properties", None)
    prop_map = getattr(device, "_PROPERTY_MAP", None)
    updated = getattr(device, "_updated_properties", None)

    # Ein FEHLENDES Glied und ein Glied vom falschen Typ werden gleich behandelt:
    # beides heisst, dass der aus msmart nachgemessene Pfad nicht mehr traegt.
    if (not callable(apply_props) or not isinstance(prop_map, Mapping)
            or updated is None):
        _LOGGER.warning(t("prop_path_gone", ".".join(PROPERTY_WRITE_MEMBERS),
                          PINNED_MSMART_VERSION))
        return False

    # Muss awaitbar sein - dieses Modul ruft mit await auf. Wuerde msmart daraus
    # eine synchrone Funktion machen, liefe der Write ins Leere (ein nicht
    # erwartetes Rueckgabeobjekt, nichts gesendet), und der einzige Hinweis waere
    # eine RuntimeWarning ausserhalb jeder Logging-/Schwaerzungskette. Also
    # lieber laut zurueckfallen.
    if not inspect.iscoroutinefunction(apply_props):
        _LOGGER.warning(t("prop_path_sync", PINNED_MSMART_VERSION))
        return False

    # Nur die property-basierten unter den scharfgeschalteten Aenderungen - exakt
    # die Menge, die apply() fuer seine Property-Runde bildet
    # (_updated_properties & _PROPERTY_MAP.keys()). Im --only-if-on-Nudge ist das
    # {IECO}; power/temp/mode sind Zustandsfelder und per Konstruktion NICHT
    # dabei, koennen hier also gar nicht ueberschrieben werden.
    try:
        keys = set(updated) & set(prop_map)
    except TypeError:
        _LOGGER.warning(t("prop_path_gone", ".".join(PROPERTY_WRITE_MEMBERS),
                          PINNED_MSMART_VERSION))
        return False

    if not keys:
        # Nichts property-basiert Schreibbares scharfgeschaltet: der Aufrufer
        # muss den vollen apply()-Weg nehmen (der z.B. auch power setzen kann).
        return False

    props = {key: prop_map[key](device) for key in keys}
    await apply_props(props)
    return True
