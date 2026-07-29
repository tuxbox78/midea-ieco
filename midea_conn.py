# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_conn.py

Gibt die LAN-Verbindung eines msmart-ng-Geraets wieder frei.

WARUM ES DIESES MODUL GIBT
--------------------------
msmart-ng 2026.7.0 bietet KEINEN oeffentlichen Weg, eine aufgebaute
Geraeteverbindung zu schliessen. Gemessen an der gepinnten Fassung:
``AirConditioner`` besitzt weder ``close`` noch ``disconnect`` noch ``stop``
(alle drei ``getattr``-Aufrufe liefern None), und weder ``AirConditioner`` noch
``msmart.lan.LAN`` sind Kontextmanager. Der einzige vorhandene Weg ist die
PRIVATE Kette ``device._lan._disconnect()``.

Genau hier lag ein realer Fehler: die frueheren Aufraeum-Helfer suchten der
Reihe nach nach ``close``/``disconnect``/``stop``, fanden nichts und taten
folglich NICHTS. Das sah defensiv aus, war aber eine vollstaendige Attrappe -
gemeldet von einem Nutzer in Issue #2, danach hier nachgemessen: nach dem
angeblichen Schliessen blieb die Verbindung offen, und die anschliessende
Verifikationsrunde baute eine ZWEITE gleichzeitige Verbindung auf. Midea-Geraete
halten nur EINE lokale Verbindung; die zweite laeuft ins Leere, und das Geraet
schweigt danach eine Weile. Das Werkzeug konnte also selbst erzeugen, was es
anschliessend als Geraetefehler meldete.

Die Tests konnten das nicht finden: saemtliche Fake-Geraete der Suite
definierten ein ``close()``, das das echte Objekt nie hatte. Der Test pruefte
damit einen Zweig, der in der Wirklichkeit unerreichbar ist. Dagegen steht jetzt
``tests/test_conn.py`` (Konformitaet der Attrappen + echter Verbindungsnachweis
ueber einen Loopback-Server) und ein Vertragstest gegen die ECHTE Bibliothek,
der in der CI im Job ``install-smoke`` laeuft - in der normalen Suite ist msmart
gestubbt, dort waere er wirkungslos.

WARUM SYNCHRON
--------------
``LAN._disconnect`` ist synchron (``def _disconnect(self) -> None``), NICHT
awaitable - ein ``await`` darauf wirft TypeError. Das ist hier ein Vorteil und
kein Zufall: ohne ``await`` gibt es keinen Abbruchpunkt, also kann dieses
Aufraeumen weder unterbrochen werden noch haengen bleiben - auch nicht, wenn es
aus einem ``finally`` heraus laeuft, waehrend die umgebende Koroutine gerade
abgebrochen wird.

WAS ES BEWUSST NICHT TUT
------------------------
Kein Kontextmanager: ``midea_ieco_ensure.ensure_ieco`` bindet ``device``
mehrfach neu (Wiederholversuch, Verifikationsrunde), ein ``async with`` passte
dort nicht und haette zwei konkurrierende Idiome im selben Projekt erzeugt.
Keine eigene Abkuehlpause: beide Werkzeuge haben dafuer bereits benannte
Konstanten (SETTLE_DELAY/RETRY_DELAY/DEVICE_DELAY in ensure, CANDIDATE_DELAY/
DEVICE_DELAY im Refresh); eine weitere waere eine sechste Stelle, an die man
denken muesste.
Keine Sperre: beide Werkzeuge arbeiten Geraete streng nacheinander ab - der
Fehler war kein Nebenlaeufigkeitsproblem, eine Sperre haette ihn nicht
verhindert.

TODO: https://github.com/mill1000/midea-msmart/issues - dieses Modul entfaellt,
sobald msmart-ng ein oeffentliches ``Device.disconnect()`` anbietet. Der
Vertragstest in tests/test_conn.py schlaegt dann absichtlich fehl und fordert
den Rueckbau ein.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from midea_i18n import make_translator

if TYPE_CHECKING:
    # Nur fuer Typpruefer: dieses Modul importiert msmart NIE zur Laufzeit. Es
    # bekommt das Geraeteobjekt uebergeben und bleibt dadurch auch ohne
    # installierte Abhaengigkeit importierbar - so wie die aufrufenden Module.
    from msmart.device.AC.device import AirConditioner as AC

_LOGGER = logging.getLogger(__name__)


# Die private Kette, ueber die msmart-ng seine Verbindung freigibt. Als Konstante
# und nicht als fest verdrahteter Attributzugriff, damit der Vertragstest genau
# DIESEN Pfad pruefen kann statt einer Kopie davon - eine Kopie wuerde bei einer
# Aenderung hier stillschweigend weiterlaufen.
TEARDOWN_PATH = ("_lan", "_disconnect")

# Die Fassung, gegen die TEARDOWN_PATH nachgemessen wurde. Erscheint in der
# Warnung, damit man beim Lesen des Logs sofort sieht, worauf sich die Erwartung
# bezog - eine Warnung ohne diesen Bezug laesst offen, ob die Bibliothek sich
# geaendert hat oder die Erwartung nie stimmte.
PINNED_MSMART_VERSION = "2026.7.0"


# Katalog: key -> (englisch, deutsch). Gleiche Mechanik wie in beiden
# Werkzeugen; die Platzhalterzahl wird in tests/test_conn.py gegengeprueft.
_MESSAGES: dict[str, tuple[str, str]] = {
    "teardown_path_gone": (
        "WARNING: msmart-ng no longer offers the internal teardown path "
        "device.%s (it broke at '%s'; verified against msmart-ng %s). "
        "The device connection stays open - please report this.",
        "WARNUNG: msmart-ng bietet den internen Aufraeum-Pfad device.%s nicht "
        "mehr (abgebrochen bei '%s'; geprueft gegen msmart-ng %s). Die "
        "Geraeteverbindung bleibt offen - bitte melden."),
    "teardown_failed": (
        "WARNING: closing the device connection failed (%s: %s). "
        "The connection may stay open until this process exits.",
        "WARNUNG: Das Schliessen der Geraeteverbindung ist fehlgeschlagen "
        "(%s: %s). Die Verbindung bleibt moeglicherweise offen, bis dieser "
        "Prozess endet."),
    "teardown_became_async": (
        "WARNING: msmart-ng's internal teardown device.%s is now a coroutine "
        "(verified as synchronous against msmart-ng %s). It is deliberately "
        "NOT awaited here - the device connection stays open. Please report "
        "this.",
        "WARNUNG: msmart-ngs interner Aufraeum-Pfad device.%s ist jetzt eine "
        "Koroutine (geprueft als synchron gegen msmart-ng %s). Er wird hier "
        "bewusst NICHT awaited - die Geraeteverbindung bleibt offen. Bitte "
        "melden."),
}

t = make_translator(_MESSAGES)


class TeardownUnavailable(RuntimeError):
    """Die private Aufraeum-Kette von msmart-ng ist nicht (mehr) auffindbar.

    Rein modulintern: geworfen von _resolve_teardown, gefangen und in eine
    Warnung uebersetzt von close_connection. Ohne fuehrenden Unterstrich, weil
    sie im Traceback eines Fremdaufrufs lesbar bleiben soll - nicht, weil
    ausserhalb jemand auf sie faengt."""


def _resolve_teardown(device: object) -> object:
    """Loest TEARDOWN_PATH auf dem uebergebenen Geraet auf.

    Rueckgabe: das aufrufbare Aufraeum-Objekt (``device._lan._disconnect``).
    Wirft TeardownUnavailable und benennt dabei das Glied, an dem die Kette
    gerissen ist - ohne diese Angabe waere die Warnung im Log nicht
    handlungsleitend.

    Ein FEHLENDES Attribut und ein Attribut mit dem Wert None werden bewusst
    gleich behandelt: in msmart-ng 2026.7.0 legt ``Device.__init__`` ``_lan``
    immer an, ein None dort waere also ebenso unerwartet wie ein fehlendes
    Attribut. Ein Sentinel, der beide Faelle trennt, wuerde eine Unterscheidung
    behaupten, die es in der Bibliothek nicht gibt."""
    current: object = device
    for hop in TEARDOWN_PATH:
        current = getattr(current, hop, None)
        if current is None:
            raise TeardownUnavailable(hop)
    if not callable(current):
        raise TeardownUnavailable(TEARDOWN_PATH[-1])
    return current


def close_connection(device: "AC | None") -> bool:
    """Schliesst die LAN-Verbindung des Geraets.

    Argument:
        device: das msmart-ng-Geraeteobjekt, oder None (dann ist nichts zu tun).

    Rueckgabe:
        True, wenn der Aufraeum-Aufruf tatsaechlich ausgefuehrt wurde.
        False, wenn nichts geschlossen werden konnte - kein Geraet uebergeben,
        die private Kette ist gerissen oder zur Koroutine geworden, oder der
        Aufruf selbst schlug fehl.

    Wirft keine Exception - weder aus dem Aufloesen der Kette noch aus dem
    Aufruf selbst. Das ist tragend, weil diese Funktion aus finally-Bloecken
    gerufen wird: eine Ausnahme von hier wuerde die urspruengliche verdraengen
    und den echten Fehler unsichtbar machen. Auch das Aufloesen ist abgesichert,
    denn getattr() faengt nur AttributeError ab - ein Deskriptor, der etwas
    anderes wirft, kaeme sonst durch.

    Was BEWUSST durchschlaegt, sind die BaseException-Abbrueche: KeyboardInterrupt
    und SystemExit (der Nutzer bzw. das Programm will beenden) sowie
    CancelledError (die Koroutine soll wirklich abbrechen). Sie zu schlucken
    hiesse, einen Abbruch zu unterdruecken, statt Aufraeumfehler zu daempfen -
    entstehen koennen sie hier ohnehin nur aus fremdem Code, der Aufruf selbst
    enthaelt kein await.

    Warum ueberhaupt ein Rueckgabewert: ohne ihn ist von aussen nicht
    beantwortbar, OB der Aufraeum-Weg ueberhaupt beschritten wurde - und genau
    diese Frage konnte beim Vorgaenger niemand stellen, weshalb seine
    Wirkungslosigkeit ueber Monate unbemerkt blieb. Der Wert ist die
    Zusicherung, an der die Tests haengen; die Aufrufer duerfen ihn ignorieren.

    Praezise gelesen sagt True: "der Aufraeum-Aufruf wurde ausgefuehrt und hat
    nicht geworfen". Es sagt NICHT "es war vorher eine Verbindung offen" - beim
    zweiten Schliessen desselben Geraets kommt ebenfalls True zurueck, weil
    ``LAN._disconnect`` dann folgenlos durchlaeuft. Fuer die Frage "wurde eine
    bestehende Verbindung wirklich geschlossen" gibt es keinen Rueckgabewert,
    sondern den Loopback-Zaehltest in tests/test_conn.py.

    Der Aufruf ist idempotent: ``LAN._disconnect`` prueft intern auf ein
    vorhandenes Protokoll und tut ohne Verbindung nichts. Zweimaliges Schliessen
    desselben Geraets ist damit unbedenklich - im Wiederverbindungspfad von
    ensure_ieco kommt genau das vor.

    Es wird bei JEDEM Fehlschlag gewarnt, nicht nur beim ersten. Das ist
    Absicht: ist die Kette gerissen, bleibt PRO GERAET eine Verbindung offen,
    und die Zahl der Warnungen entspricht dann genau dem angerichteten Schaden.
    Eine Unterdrueckung nach der ersten Meldung wuerde Zustand in dieses Modul
    holen und den Umfang des Problems verschleiern."""
    if device is None:
        return False

    try:
        teardown = _resolve_teardown(device)
    except TeardownUnavailable as exc:
        _LOGGER.warning(t("teardown_path_gone", ".".join(TEARDOWN_PATH),
                          exc.args[0], PINNED_MSMART_VERSION))
        return False
    except Exception as exc:  # noqa: BLE001 - siehe Docstring: nie eskalieren
        # getattr() faengt nur AttributeError ab. Ist eines der Glieder eine
        # Property (oder greift ein __getattr__), kann das Aufloesen selbst
        # etwas ganz anderes werfen - und diese Funktion laeuft in finally-
        # Bloecken, wo das die urspruengliche Ausnahme verdraengen wuerde.
        _LOGGER.warning(t("teardown_failed", type(exc).__name__, exc))
        return False

    if inspect.iscoroutinefunction(teardown):
        # Waere der Aufraeum-Pfad eine Koroutine, gaebe der Aufruf unten nur ein
        # nicht-erwartetes Koroutinen-Objekt zurueck: es wuerde NICHTS
        # geschlossen, die Funktion meldete trotzdem Erfolg, und der einzige
        # Hinweis waere eine RuntimeWarning ausserhalb jeder Logging- und
        # Schwaerzungskette. Genau diese Art stiller Wirkungslosigkeit soll
        # dieses Modul beenden - also lieber laut scheitern. Bewusst NICHT
        # awaiten: close_connection ist synchron, damit es in einem finally
        # keinen Abbruchpunkt erzeugt (siehe Modul-Docstring).
        _LOGGER.warning(t("teardown_became_async", ".".join(TEARDOWN_PATH),
                          PINNED_MSMART_VERSION))
        return False

    try:
        teardown()
    except Exception as exc:  # noqa: BLE001 - Aufraeumen darf nie eskalieren
        # Exception, nicht BaseException - die Begruendung fuer diese Grenze
        # steht im Docstring: Aufraeumfehler daempfen, Abbrueche durchlassen.
        _LOGGER.warning(t("teardown_failed", type(exc).__name__, exc))
        return False

    return True
