#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_conn.py (stdlib unittest, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_conn  (aus dem Repo-Root)

Drei Schichten, weil KEINE davon allein den Fehler gefunden haette, den dieses
Modul behebt:

1. Verhalten (CloseConnectionTests): ein echter Loopback-Server zaehlt
   Verbindungen. Damit ist "wurde wirklich geschlossen?" beobachtbar statt
   geglaubt. Enthaelt eine Mutationsklammer, die die ALTE Fassung
   nachstellt - sie muss an derselben Zusicherung scheitern.
2. Formtreue (FakeFidelityTests): keine Geraete-Attrappe der Suite darf einen
   Aufraeum-Namen anbieten, den das echte Objekt nicht hat. Genau diese
   Umkehrung - Attrappe reicher als die Wirklichkeit - hielt den toten Pfad
   gruen.
3. Abhaengigkeitsvertrag (MsmartContractTests): die private Kette existiert,
   ist synchron und parameterlos. Laeuft im SUBPROZESS gegen die echte
   Bibliothek, weil in dieser Suite ein msmart-Stub registriert ist - ein
   Vertragstest im selben Interpreter pruefte sonst unbemerkt die Attrappe.
"""
import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import midea_conn  # noqa: E402

# Ausgabesprache auf Englisch pinnen (den Default) - sonst haengen die
# Textzusicherungen an der Locale des Ausfuehrenden.
_LANG_PATCHER = None


def setUpModule():
    global _LANG_PATCHER
    _LANG_PATCHER = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"})
    _LANG_PATCHER.start()


def tearDownModule():
    if _LANG_PATCHER is not None:
        _LANG_PATCHER.stop()


# --------------------------------------------------------------------------
# Hilfsmittel: ein Zaehl-Server und formtreue Geraete-Attrappen
# --------------------------------------------------------------------------

class _ConnectionLedger(asyncio.Protocol):
    """Protokoll-Fabrik, die Auf- und Abbau echter Verbindungen mitzaehlt.

    Bewusst ereignisgesteuert (asyncio.Event) statt ueber Wartezeiten: eine
    Zusicherung, die auf sleep() beruht, ist entweder langsam oder flatterhaft."""

    opened = 0
    closed = 0
    accepted: asyncio.Event
    dropped: asyncio.Event

    @classmethod
    def reset(cls):
        cls.opened = 0
        cls.closed = 0
        cls.accepted = asyncio.Event()
        cls.dropped = asyncio.Event()

    def connection_made(self, transport):
        type(self).opened += 1
        type(self).accepted.set()

    def connection_lost(self, exc):
        type(self).closed += 1
        type(self).dropped.set()


class _StubLAN:
    """Bildet msmart.lan.LAN so weit nach, wie das Aufraeumen es braucht:
    ein SYNCHRONES _disconnect(), das den echten Transport schliesst."""

    def __init__(self, transport):
        self._transport = transport

    def _disconnect(self) -> None:
        self._transport.close()


class _StubDevice:
    """Formtreue Geraete-Attrappe: KEIN close/disconnect/stop (das echte
    msmart-Objekt hat sie nicht), dafuer die private Kette _lan._disconnect."""

    def __init__(self, transport):
        self._lan = _StubLAN(transport)


async def _legacy_close_device(device):
    """Die ALTE, wirkungslose Fassung - woertlich, als Mutationsklammer.

    Sie sucht drei oeffentliche Namen, die msmart-ngs AirConditioner nie hatte,
    findet nichts und tut folglich nichts. Steht hier, damit der Nachweis
    'die neue Fassung schliesst wirklich' einen Gegenpol hat: ohne ihn koennte
    die Zusicherung auch aus einem anderen Grund gruen sein."""
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


class _LoopbackCase(unittest.IsolatedAsyncioTestCase):
    """Basis: startet einen Zaehl-Server auf einem freien Port und baut auf
    Wunsch eine echte Verbindung dorthin auf."""

    async def asyncSetUp(self):
        _ConnectionLedger.reset()
        self._transports = []
        loop = asyncio.get_running_loop()
        self.server = await loop.create_server(_ConnectionLedger, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        # Erst ALLE Verbindungen schliessen, dann auf den Server warten. Seit
        # CPython 3.12.1 (gh-79033) wartet Server.wait_closed() auch auf die
        # noch offenen Verbindungen - ein Test, der absichtlich eine offen
        # laesst (die Mutationsklammer unten), haengt sonst ohne jede Ausgabe
        # und nimmt die ganze Suite mit. Das zusaetzliche Zeitlimit sorgt
        # dafuer, dass ein solcher Fehler als roter Test endet und nicht als
        # stehengebliebener Lauf.
        for transport in self._transports:
            transport.close()
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), 5)
        except asyncio.TimeoutError:
            self.fail("Der Loopback-Server wurde nicht geschlossen - es ist "
                      "noch eine Verbindung offen.")

    async def _connect(self) -> _StubDevice:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_connection(
            asyncio.Protocol, "127.0.0.1", self.port)
        self._transports.append(transport)
        await asyncio.wait_for(_ConnectionLedger.accepted.wait(), 5)
        return _StubDevice(transport)


# --------------------------------------------------------------------------
# 1. Verhalten
# --------------------------------------------------------------------------

class CloseConnectionTests(_LoopbackCase):

    async def test_a_real_connection_is_really_closed(self):
        device = await self._connect()
        self.assertEqual(_ConnectionLedger.opened, 1)
        self.assertEqual(_ConnectionLedger.closed, 0)

        self.assertTrue(midea_conn.close_connection(device))

        await asyncio.wait_for(_ConnectionLedger.dropped.wait(), 5)
        self.assertEqual(_ConnectionLedger.closed, 1)

    async def test_the_previous_implementation_fails_this_very_assertion(self):
        # Mutationsklammer: dieselbe Lage, dieselbe Zusicherung - nur mit der
        # alten Fassung. Sie MUSS die Verbindung offen lassen. Wird dieser Test
        # gruen, misst der Test darueber nicht mehr, was er zu messen behauptet.
        device = await self._connect()
        await _legacy_close_device(device)

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(_ConnectionLedger.dropped.wait(), 0.25)
        self.assertEqual(_ConnectionLedger.closed, 0)

    async def test_a_second_close_is_harmless(self):
        # Kommt im Wiederverbindungspfad von ensure_ieco tatsaechlich vor.
        device = await self._connect()
        self.assertTrue(midea_conn.close_connection(device))
        self.assertTrue(midea_conn.close_connection(device))
        await asyncio.wait_for(_ConnectionLedger.dropped.wait(), 5)
        self.assertEqual(_ConnectionLedger.closed, 1)

    async def test_closing_survives_being_called_while_cancelled(self):
        # Der Aufraeum-Aufruf ist synchron und damit kein Abbruchpunkt. Genau
        # deshalb funktioniert er auch in einem finally, waehrend die umgebende
        # Koroutine abgebrochen wird - das ist der Fall, in dem eine
        # await-basierte Variante haengen bleiben koennte.
        device = await self._connect()
        closed_in_finally = []

        async def worker():
            try:
                await asyncio.sleep(10)
            finally:
                closed_in_finally.append(midea_conn.close_connection(device))

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(worker(), 0.1)

        self.assertEqual(closed_in_finally, [True])
        await asyncio.wait_for(_ConnectionLedger.dropped.wait(), 5)


class TeardownGuardTests(unittest.TestCase):
    """Die Waechter rund um den eigentlichen Aufruf."""

    def test_none_is_accepted_and_reports_nothing_closed(self):
        self.assertFalse(midea_conn.close_connection(None))

    def test_a_broken_chain_warns_and_reports_false(self):
        class Missing:
            pass

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(Missing()))
        self.assertIn("_lan._disconnect", logs.output[0])
        # Das gerissene Glied MUSS benannt werden - eine Warnung ohne diese
        # Angabe laesst offen, wo man suchen muss.
        self.assertIn("'_lan'", logs.output[0])

    def test_a_chain_that_breaks_at_the_last_hop_names_that_hop(self):
        class HalfThere:
            class _lan:
                pass

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(HalfThere()))
        self.assertIn("'_disconnect'", logs.output[0])

    def test_a_non_callable_teardown_is_treated_as_broken(self):
        class NotCallable:
            class _lan:
                _disconnect = "nicht aufrufbar"

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(NotCallable()))
        # Der Text MUSS geprueft werden, nicht nur "irgendeine Warnung": ohne
        # den callable()-Waechter wuerde der Aufruf einen TypeError werfen, der
        # als teardown_failed gemeldet wird - gleiche Rueckgabe, gleiche
        # Warnstufe. Ein Test ohne diese Zusicherung bleibt beim Entfernen des
        # Waechters gruen und prueft damit nicht, was sein Name behauptet.
        self.assertIn("'_disconnect'", logs.output[0])
        self.assertNotIn("TypeError", logs.output[0])

    def test_a_coroutine_teardown_is_refused_instead_of_silently_ignored(self):
        # Wuerde msmart-ng _disconnect zur Koroutine machen, liefe der Aufruf
        # ins Leere: ein nicht-erwartetes Koroutinen-Objekt, nichts geschlossen,
        # und ohne diesen Waechter meldete close_connection trotzdem True -
        # genau die stille Wirkungslosigkeit, gegen die dieses Modul antritt.
        class AsyncTeardown:
            class _lan:
                @staticmethod
                async def _disconnect():
                    pass

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(AsyncTeardown()))
        self.assertIn("coroutine", logs.output[0].lower())

    def test_a_raising_descriptor_does_not_escape_from_a_finally(self):
        # getattr() faengt nur AttributeError. Eine Property, die etwas anderes
        # wirft, kaeme sonst durch - und weil close_connection aus finally-
        # Bloecken laeuft, verdraengte sie dort die urspruengliche Ausnahme.
        class RaisingDescriptor:
            @property
            def _lan(self):
                raise RuntimeError("Deskriptor wirft")

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(RaisingDescriptor()))
        self.assertIn("RuntimeError", logs.output[0])

    def test_the_original_exception_survives_a_broken_teardown(self):
        # Die eigentliche Zusage des Docstrings, an ihrem echten Einsatzort
        # gemessen: im finally darf das Aufraeumen den urspruenglichen Fehler
        # niemals ersetzen.
        class RaisingDescriptor:
            @property
            def _lan(self):
                raise RuntimeError("Aufraeumen kaputt")

        with self.assertLogs(midea_conn.__name__, level="WARNING"):
            with self.assertRaises(ValueError) as cm:
                try:
                    raise ValueError("der ECHTE Fehler")
                finally:
                    midea_conn.close_connection(RaisingDescriptor())
        self.assertEqual(str(cm.exception), "der ECHTE Fehler")

    def test_a_raising_teardown_warns_and_never_escalates(self):
        class Raises:
            class _lan:
                @staticmethod
                def _disconnect():
                    raise OSError("Socket schon weg")

        with self.assertLogs(midea_conn.__name__, level="WARNING") as logs:
            self.assertFalse(midea_conn.close_connection(Raises()))
        self.assertIn("OSError", logs.output[0])

    def test_a_keyboard_interrupt_is_not_swallowed(self):
        # Aufraeumen darf nie eskalieren - ein Abbruch durch den Nutzer aber
        # sehr wohl durchschlagen. Deshalb except Exception, nicht BaseException.
        class Interrupts:
            class _lan:
                @staticmethod
                def _disconnect():
                    raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            midea_conn.close_connection(Interrupts())


# --------------------------------------------------------------------------
# 2. Formtreue der Attrappen
# --------------------------------------------------------------------------

class FakeFidelityTests(unittest.TestCase):
    """Eine Attrappe darf AERMER sein als das echte Objekt, nie REICHER.

    Der behobene Fehler bestand genau aus dieser Umkehrung: fuenf Attrappen
    boten ein close(), das msmart-ngs AirConditioner nicht hat - und hielten
    damit einen Produktionspfad gruen, der in Wirklichkeit nie lief."""

    # Namen, die das echte AirConditioner-Objekt NICHT kennt (gemessen gegen
    # msmart-ng 2026.7.0; MsmartContractTests haelt diese Aussage aktuell).
    FORBIDDEN = ("close", "disconnect", "stop")

    def _device_fakes(self):
        """Alle Klassen der Suite, die ein Geraet nachstellen - erkannt daran,
        dass sie authenticate() oder refresh() anbieten."""
        import test_ensure
        import test_refresh_tokens

        found = []
        for module in (test_ensure, test_refresh_tokens):
            for name, obj in vars(module).items():
                if not inspect.isclass(obj):
                    continue
                if any(hasattr(obj, m) for m in ("authenticate", "refresh")):
                    found.append((module.__name__, name, obj))
        return found

    #: Alle Geraete-Attrappen der Suite, namentlich. Eine blosse Untergrenze
    #: liesse eine davon spurlos verschwinden - und ein Test, der ueber eine
    #: leere Menge laeuft, ist gruen, ohne etwas zu pruefen.
    EXPECTED_FAKES = {
        "test_ensure.FakeDevice",
        "test_ensure.CapabilityGatedDevice",
        "test_ensure._RecordingAC",
        "test_ensure._EventDevice",
        "test_refresh_tokens._RecordingAC",
    }

    def test_the_suite_actually_contains_the_known_device_fakes(self):
        found = {f"{module}.{name}" for module, name, _ in self._device_fakes()}
        self.assertEqual(found, self.EXPECTED_FAKES)

    def test_no_device_fake_offers_a_teardown_the_real_object_lacks(self):
        offenders = []
        for module_name, name, obj in self._device_fakes():
            for forbidden in self.FORBIDDEN:
                if hasattr(obj, forbidden):
                    offenders.append(f"{module_name}.{name}.{forbidden}")
        self.assertEqual(
            offenders, [],
            "Diese Attrappen bieten einen Aufraeum-Namen an, den msmart-ngs "
            "AirConditioner nicht hat. Produktionscode, der danach sucht, ist "
            "in Wirklichkeit tot: " + ", ".join(offenders))

    def test_the_import_stub_mirrors_the_real_teardown_path(self):
        import _stub_msmart  # noqa: F401  (registriert die Attrappe)
        from msmart.device.AC.device import AirConditioner as StubAC

        device = StubAC(ip="1.2.3.4", port=6444, device_id=1)
        for forbidden in self.FORBIDDEN:
            self.assertFalse(hasattr(device, forbidden), forbidden)
        self.assertTrue(midea_conn.close_connection(device))
        self.assertEqual(device._lan.disconnect_calls, 1)


# --------------------------------------------------------------------------
# 3. Abhaengigkeitsvertrag gegen die ECHTE Bibliothek
# --------------------------------------------------------------------------

# Laeuft im Subprozess: in dieser Suite registriert _stub_msmart eine Attrappe
# in sys.modules, und ein Vertragstest im selben Interpreter pruefte je nach
# Ladereihenfolge unbemerkt diese Attrappe statt der Bibliothek.
_CONTRACT_PROBE = r"""
import inspect, json, sys
try:
    from msmart.device.AC.device import AirConditioner
    from msmart.device.AC.command import PropertyId
    from importlib.metadata import version
except Exception as exc:
    print(json.dumps({"available": False, "reason": str(exc)}))
    sys.exit(0)

device = AirConditioner(ip="192.0.2.1", port=6444, device_id=1)
lan = getattr(device, "_lan", None)
fn = getattr(lan, "_disconnect", None)

# Zweiter Vertrag: die Mitnahme der iECO-Faehigkeit (prime_ieco_property).
# Auf einem EIGENEN Objekt, damit die Teardown-Sonde oben unberuehrt bleibt.
caps_cls = getattr(AirConditioner, "Capability", None)
ieco_flag = getattr(caps_cls, "IECO", None)
default_flags = getattr(caps_cls, "DEFAULT", None)
primed = AirConditioner(ip="192.0.2.1", port=6444, device_id=1)
prime_error = None
try:
    primed.override_capabilities({"additional_capabilities": ["IECO"]},
                                 merge=True)
except Exception as exc:
    prime_error = "%s: %s" % (type(exc).__name__, exc)

print(json.dumps({
    "available": True,
    "version": version("msmart-ng"),
    "public_teardown": [n for n in ("close", "disconnect", "stop")
                        if hasattr(AirConditioner, n)],
    "has_lan": lan is not None,
    "disconnect_callable": callable(fn),
    "disconnect_is_coroutine": inspect.iscoroutinefunction(fn) if fn else None,
    "disconnect_params": list(inspect.signature(fn).parameters) if fn else None,
    "ieco_flag_exists": ieco_flag is not None,
    # Die PRAEMISSE des Fehlers, den prime_ieco_property beseitigt: ein frisches
    # Objekt kennt IECO nicht. Faellt das eines Tages weg, ist der halbe
    # Waechterbau in ensure_ieco gegenstandslos - und das soll auffallen.
    "ieco_absent_from_default": bool(
        ieco_flag is not None and default_flags is not None
        and not (default_flags & ieco_flag)),
    "prime_error": prime_error,
    "prime_sets_supports_ieco": bool(getattr(primed, "supports_ieco", False)),
    "prime_adds_ieco_property": bool(
        PropertyId.IECO in getattr(primed, "_supported_properties", set())),
    # Issue #7: der out_silent-Guard in _validate_and_arm haengt an DIESEN
    # Instance-Attributen (device.supports_out_silent / device.out_silent) -
    # genau wie der iECO-Vertrag oben supports_ieco auf einem frischen Objekt
    # liest. hasattr/getattr statt Direktreferenz, damit ein kuenftiges
    # Umbenennen einen sauberen Test-Fehlschlag ergibt statt die Sonde
    # abstuerzen zu lassen. Die Population (get_capabilities()+refresh() traegt
    # OUT_SILENT in _supported_properties) ist am Geraet belegt und laesst sich
    # ohne Verbindung nicht pruefen - deshalb hier nur die NAMEN.
    "out_silent_attr_readable": hasattr(device, "out_silent"),
    "supports_out_silent_attr_readable": hasattr(device, "supports_out_silent"),
    "out_silent_property_id_exists":
        getattr(PropertyId, "OUT_SILENT", None) is not None,
    "out_silent_capability_exists":
        getattr(caps_cls, "OUT_SILENT", None) is not None,
}))
"""
# Dass hier die ECHTE Bibliothek geprueft wird und keine Attrappe, belegt der
# version("msmart-ng")-Aufruf oben: er liest die Metadaten der installierten
# Distribution und wirft PackageNotFoundError, wenn 'msmart' nur ein Modul ohne
# Paket ist - dann meldet die Sonde available=False. Eine zusaetzliche Marke am
# Attrappen-Modul waere hier wirkungslos, weil dieser Unterprozess die Attrappe
# gar nicht erst importiert.


class MsmartContractTests(unittest.TestCase):
    """Pinnt die PRIVATE Kette, auf der midea_conn beruht.

    Schlaegt dieser Test nach einem Versions-Sprung fehl, ist close_connection()
    wieder wirkungslos geworden - dann ist TEARDOWN_PATH nachzuziehen.

    Ist msmart-ng gar nicht installiert - der Normalfall auf einem
    Entwicklerrechner -, wird ehrlich uebersprungen. Die CI fuehrt ihn im Job
    'install-smoke' mit den echten Abhaengigkeiten aus; das ist die einzige
    Stelle, an der er wirklich etwas beweist, und dort ist Ueberspringen
    verboten (siehe STRICT_ENV).

    Ein UEBERSPRUNGENER Test faellt bei unittest mit Exit-Code 0 durch. Genau
    daran waere dieser Waechter fast wertlos geworden: er ist das Einzige, was
    zwischen einer kuenftigen msmart-Fassung und einem stillschweigend wieder
    wirkungslosen close_connection() steht. Deshalb zwei Regeln:

    1. Ein ABSTURZ der Sonde ist ein Fehler, kein Grund zum Ueberspringen.
       Genau dieser Fall - eine geaenderte Konstruktor-Signatur etwa - ist der,
       den der Waechter entdecken soll; als Skip waere die CI dabei gruen.
    2. Ist STRICT_ENV gesetzt, wird JEDES Ueberspringen zum Fehler. Der
       CI-Schritt setzt die Variable, weil dort ein fehlendes msmart-ng selbst
       schon ein Befund ist."""

    #: Gesetzt vom CI-Schritt: hier darf nicht uebersprungen werden.
    STRICT_ENV = "MIDEA_IECO_REQUIRE_MSMART"

    @classmethod
    def _refuse(cls, reason: str):
        """Ueberspringt - oder scheitert, wenn Ueberspringen verboten ist."""
        if os.environ.get(cls.STRICT_ENV, "").strip():
            raise AssertionError(
                f"{cls.STRICT_ENV} ist gesetzt, der Vertrag MUSS hier laufen: "
                f"{reason}")
        raise unittest.SkipTest(reason)

    @classmethod
    def setUpClass(cls):
        result = subprocess.run([sys.executable, "-c", _CONTRACT_PROBE],
                                capture_output=True, text=True, timeout=60,
                                cwd=str(REPO_DIR))
        if result.returncode != 0:
            # Bewusst KEIN Skip: siehe Regel 1 im Klassen-Docstring.
            raise AssertionError(
                "Vertragssonde abgestuerzt - das ist ein Befund, kein Grund zum "
                f"Ueberspringen: {result.stderr.strip()[:300]}")
        try:
            cls.probe = json.loads(result.stdout)
        except ValueError as exc:
            raise AssertionError(
                f"Vertragssonde lieferte kein gueltiges JSON ({exc}): "
                f"{result.stdout[:200]!r}") from exc
        if not cls.probe.get("available"):
            cls._refuse(
                "msmart-ng ist nicht installiert - der Vertrag gegen die echte "
                "Bibliothek laeuft im CI-Job 'install-smoke'. Grund der Sonde: "
                f"{cls.probe.get('reason', '(keiner)')}")

    def test_the_private_teardown_path_still_exists(self):
        self.assertTrue(self.probe["has_lan"],
                        "AirConditioner._lan ist verschwunden")
        self.assertTrue(self.probe["disconnect_callable"],
                        "_lan._disconnect fehlt oder ist nicht aufrufbar")

    def test_the_teardown_is_still_synchronous(self):
        # Traegt: midea_conn ruft ohne await auf. Wuerde msmart daraus eine
        # Koroutine machen, liefe das Aufraeumen ins Leere (und eine
        # RuntimeWarning waere der einzige Hinweis).
        self.assertFalse(self.probe["disconnect_is_coroutine"],
                         "_lan._disconnect ist jetzt eine Koroutine - "
                         "midea_conn.close_connection muss awaiten")

    def test_the_teardown_takes_no_arguments(self):
        self.assertEqual(self.probe["disconnect_params"], [],
                         "_lan._disconnect hat Parameter bekommen")

    def test_no_public_teardown_appeared_upstream(self):
        # Umkehrwaechter: sobald msmart-ng ein oeffentliches close() anbietet,
        # soll dieser Test fehlschlagen und den Rueckbau von midea_conn
        # einfordern - sonst bleibt der Griff in fremde Interna fuer immer
        # stehen, obwohl es einen sauberen Weg gaebe.
        self.assertEqual(
            self.probe["public_teardown"], [],
            "msmart-ng %s bietet jetzt %s - bitte midea_conn auf die "
            "oeffentliche API umstellen und dieses Modul entfernen."
            % (self.probe["version"], self.probe["public_teardown"]))

    def test_the_pinned_version_matches_what_is_installed(self):
        # Weicht die installierte Fassung von der ab, gegen die der Pfad
        # nachgemessen wurde, ist die Warnung in midea_conn irrefuehrend.
        self.assertEqual(self.probe["version"], midea_conn.PINNED_MSMART_VERSION)

    # -- Vertrag der iECO-Mitnahme (midea_ieco_ensure.prime_ieco_property) ----
    # Diese vier Zusicherungen koennen NUR hier stehen: in der normalen Suite
    # ist msmart gestubbt, dort waeren sie wirkungslos.

    def test_the_ieco_capability_flag_still_exists(self):
        self.assertTrue(self.probe["ieco_flag_exists"],
                        "AirConditioner.Capability.IECO ist verschwunden - "
                        "prime_ieco_property kann nichts mehr eintragen.")

    def test_a_fresh_device_still_does_not_claim_ieco(self):
        # Die Praemisse des behobenen Fehlers. Enthielte Capability.DEFAULT
        # eines Tages IECO, waere supports_ieco kein Beleg mehr fuer eine
        # Geraeteaussage - und der caps_answered-Waechter in ensure_ieco
        # muesste neu gedacht werden.
        self.assertTrue(
            self.probe["ieco_absent_from_default"],
            "Capability.DEFAULT enthaelt jetzt IECO - damit ist supports_ieco "
            "kein Beweis mehr dafuer, dass das Geraet geantwortet hat. Die "
            "Waechter in midea_ieco_ensure.ensure_ieco sind nachzuziehen.")

    def test_the_capability_override_is_accepted(self):
        self.assertIsNone(
            self.probe["prime_error"],
            "override_capabilities({'additional_capabilities': ['IECO']}, "
            "merge=True) wird nicht mehr angenommen: "
            f"{self.probe['prime_error']}")
        self.assertTrue(self.probe["prime_sets_supports_ieco"],
                        "override_capabilities lief durch, ohne supports_ieco "
                        "zu setzen.")

    def test_the_override_really_arms_the_ieco_property_poll(self):
        # Der tragende Teil: nicht das Flag zaehlt, sondern dass msmart daraus
        # _supported_properties nachzieht - nur dann pollt refresh() die
        # IECO-Property. Bliebe override_capabilities erhalten, riefe aber
        # _update_supported_properties() nicht mehr auf, waere supports_ieco
        # True und die Verifikation laese trotzdem wieder den Default False.
        # Genau diese stille Wirkungslosigkeit soll hier auffallen.
        self.assertTrue(
            self.probe["prime_adds_ieco_property"],
            "override_capabilities traegt PropertyId.IECO nicht mehr in "
            "_supported_properties ein - refresh() pollt iECO damit nicht "
            "mehr, und die Verifikation meldet wieder faelschlich 'weiterhin "
            "deaktiviert'.")

    # -- Vertrag der out_silent-Weiche (Issue #7) -----------------------------
    # Der Guard in midea_ieco_ensure._validate_and_arm liest device.out_silent
    # und device.supports_out_silent (Instance) und stuetzt sich darauf, dass
    # get_capabilities()+refresh() PropertyId.OUT_SILENT poll-bar machen. Wird
    # eines dieser Symbole umbenannt, muss das HIER auffallen, nicht erst an der
    # echten Anlage - dieselbe Rolle wie der iECO-Vertrag darueber.

    def test_the_out_silent_instance_attributes_still_exist(self):
        self.assertTrue(
            self.probe["out_silent_attr_readable"],
            "device.out_silent ist verschwunden - der out_silent-Guard in "
            "_validate_and_arm laese damit ins Leere.")
        self.assertTrue(
            self.probe["supports_out_silent_attr_readable"],
            "device.supports_out_silent ist verschwunden - das Capability-Gate "
            "des out_silent-Guards greift damit nicht mehr.")

    def test_the_out_silent_enum_members_still_exist(self):
        self.assertTrue(
            self.probe["out_silent_property_id_exists"],
            "PropertyId.OUT_SILENT ist verschwunden - out_silent wird dann bei "
            "get_capabilities()+refresh() nicht mehr gepollt.")
        self.assertTrue(
            self.probe["out_silent_capability_exists"],
            "AirConditioner.Capability.OUT_SILENT ist verschwunden - "
            "supports_out_silent kann die Faehigkeit nicht mehr melden.")


class ToolsBootstrapTests(unittest.TestCase):
    """Die Diagnose-Werkzeuge in tools/ muessen midea_conn wirklich finden.

    Warum als SUBPROZESS und nicht als schlichter Import: diese Testdatei legt
    das Repo-Wurzelverzeichnis selbst auf sys.path. Ein Import hier waere also
    auch dann gruen, wenn der Pfad-Bootstrap in den Werkzeugen fehlte - der Test
    wuerde die eigene Vorbereitung messen statt der Werkzeuge. Der Unterprozess
    startet mit fremdem Arbeitsverzeichnis und ohne diese Hilfe, also genau so,
    wie ein Nutzer das Werkzeug aufruft.

    Warum ueberhaupt: 'python3 -m py_compile' in tests/run_all.sh prueft nur die
    SYNTAX. Ein Import, der zur Laufzeit nicht aufloest, faellt dort nicht auf -
    und diese Werkzeuge haben sonst keine Tests."""

    TOOLS = ("probe_ieco_current_mode.py", "probe_ieco_modes.py",
             "probe_ieco_property_only.py")

    def test_every_tool_is_covered_here(self):
        # Kommt ein Werkzeug dazu, soll diese Liste auffallen und nicht
        # stillschweigend unvollstaendig bleiben.
        on_disk = {p.name for p in (REPO_DIR / "tools").glob("probe_*.py")}
        self.assertEqual(on_disk, set(self.TOOLS))

    def test_tools_resolve_their_imports_when_started_directly(self):
        neutral = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(neutral))
        for tool in self.TOOLS:
            with self.subTest(tool=tool):
                result = subprocess.run(
                    [sys.executable, str(REPO_DIR / "tools" / tool), "--help"],
                    capture_output=True, text=True, timeout=60, cwd=neutral)
                self.assertNotIn("ModuleNotFoundError", result.stderr)
                self.assertNotIn("ImportError", result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr[:400])


# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------

class CatalogTests(unittest.TestCase):
    """Analog zu den beiden Werkzeugen: der Katalog muss vollstaendig und in
    beiden Sprachen strukturgleich sein."""

    def _used_keys(self):
        src = (REPO_DIR / "midea_conn.py").read_text(encoding="utf-8")
        return set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', src))

    def test_every_used_key_exists(self):
        missing = self._used_keys() - set(midea_conn._MESSAGES)
        self.assertEqual(missing, set(), f"Verwendet, aber nicht im Katalog: {missing}")

    def test_no_orphan_keys(self):
        orphans = set(midea_conn._MESSAGES) - self._used_keys()
        self.assertEqual(orphans, set(), f"Im Katalog, aber unbenutzt: {orphans}")

    def test_placeholder_count_matches_between_languages(self):
        for key, (english, german) in midea_conn._MESSAGES.items():
            self.assertEqual(english.count("%s"), german.count("%s"), key)

    def test_both_languages_non_empty(self):
        for key, (english, german) in midea_conn._MESSAGES.items():
            self.assertTrue(english.strip(), key)
            self.assertTrue(german.strip(), key)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            midea_conn.t("does_not_exist")

    def test_german_is_reachable(self):
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "de"}):
            self.assertIn("WARNUNG", midea_conn.t("teardown_failed", "OSError", "x"))


if __name__ == "__main__":
    unittest.main()
