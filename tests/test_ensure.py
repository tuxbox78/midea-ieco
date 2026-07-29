#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_ieco_ensure.py (stdlib unittest, Fake-msmart, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_ensure  (aus dem Repo-Root)
oder direkt: python3 tests/test_ensure.py
"""
import asyncio
import enum
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, suppress
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stub_msmart  # noqa: E402,F401  (registriert Fake-msmart VOR dem Import)
# Gemeinsame, formtreue LAN-Attrappe: das echte AirConditioner-Objekt hat kein
# close(), das Aufraeumen laeuft ueber die private Kette _lan._disconnect().
from _stub_msmart import RecordingLAN as _RecordingLAN  # noqa: E402
import midea_ieco_ensure as mie  # noqa: E402

# Ausgabesprache fuer das GESAMTE Modul auf Englisch pinnen (den Default).
# Ohne dieses Pinning haengen alle Textzusicherungen an der Locale des
# Ausfuehrenden: auf einem deutschen Entwicklerrechner waeren sie gruen, im
# (locale-losen) CI rot. Die deutsche Fassung prueft GermanOutputTests gezielt.
_LANG_PATCHER = None


def setUpModule():
    global _LANG_PATCHER
    _LANG_PATCHER = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"})
    _LANG_PATCHER.start()


def tearDownModule():
    if _LANG_PATCHER is not None:
        _LANG_PATCHER.stop()


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _expect_exit1(self):
        with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()):
            mie.load_config()
        self.assertEqual(cm.exception.code, 1)

    def test_missing_exits_1(self):
        self._expect_exit1()

    def test_valid_config(self):
        self.path.write_text('{"devices": []}', encoding="utf-8")
        self.assertEqual(mie.load_config(), {"devices": []})

    def test_malformed_json_exits_1(self):
        self.path.write_text("{ bad", encoding="utf-8")
        self._expect_exit1()

    def test_invalid_utf8_exits_1(self):
        # Nicht-UTF-8 (Latin-1-Umlaut, Byte 0xFC) -> UnicodeDecodeError, ein
        # ValueError, aber KEIN JSONDecodeError. Muss dennoch sauber Exit 1
        # liefern statt eines rohen Tracebacks.
        self.path.write_bytes(b'{"devices": [{"name": "K\xfcche"}]}')
        self._expect_exit1()

    def test_toplevel_list_exits_1(self):
        self.path.write_text("[]", encoding="utf-8")
        self._expect_exit1()

    def test_devices_not_a_list_exits_1(self):
        self.path.write_text('{"devices": 5}', encoding="utf-8")
        self._expect_exit1()


class _OpMode(enum.IntEnum):
    """Bildet msmart-ng's OperationalMode nach (gleiche Werte, gleiches
    IntEnum-Verhalten), damit die Fakes sich wie echte Geraete verhalten -
    inklusive der Modusbindung von iECO."""
    AUTO = 1
    COOL = 2
    DRY = 3
    HEAT = 4
    FAN_ONLY = 5


class FakeDevice:
    """Konfigurierbares Fake-AC-Objekt fuer die Retry-Matrix (#13/#14)."""

    def __init__(self, *, online=True, power_state=False, ieco=False,
                 supports_ieco=True, apply_raises=None, caps_raises=None):
        self.online = online
        self.power_state = power_state
        self.ieco = ieco
        self.eco = False
        # COOL als Standard: der Modus, in dem iECO tatsaechlich haelt (am Geraet
        # gemessen). Mit dem frueheren AUTO-Default wuerden diese Tests am
        # Modus-Guard haengenbleiben, statt den Pfad zu pruefen, um den es ihnen
        # geht (Retry/Capabilities).
        self.operational_mode = _OpMode.COOL
        self.supports_ieco = supports_ieco
        self._apply_raises = apply_raises
        self._caps_raises = caps_raises
        self.apply_calls = 0
        self.caps_calls = 0
        self.refresh_calls = 0
        self.close_calls = 0
        self._lan = _RecordingLAN(self._note_close)

    def _note_close(self) -> None:
        """Haken fuer Unterklassen, die das Schliessen anders protokollieren
        wollen. Wird ueber _lan._disconnect() erreicht, nicht ueber ein
        close() - das echte Geraeteobjekt hat keines."""
        self.close_calls += 1

    async def get_capabilities(self):
        self.caps_calls += 1
        if self._caps_raises is not None:
            raise self._caps_raises

    async def refresh(self):
        self.refresh_calls += 1

    async def apply(self):
        self.apply_calls += 1
        if self._apply_raises is not None:
            raise self._apply_raises


def _scripted_connect(items):
    """Async-Ersatz fuer connect_and_refresh: gibt der Reihe nach die
    uebergebenen Fake-Geraete zurueck; ist ein Eintrag eine Exception, wird sie
    geworfen (simuliert einen fehlgeschlagenen Reconnect). Bildet
    with_capabilities nach wie das echte connect_and_refresh: bei True erst
    get_capabilities(), dann refresh() auf dem Geraet."""
    seq = list(items)

    async def _connect(dev_conf, retries=mie.CONNECT_RETRIES, with_capabilities=False):
        item = seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        if with_capabilities:
            await item.get_capabilities()
            await item.refresh()
        return item

    return _connect


async def _anoop(*args, **kwargs):
    return None


class RetryHardeningTests(unittest.TestCase):
    """#13/#14: kein apply() auf totem Objekt; kein Traceback aus dem Reconnect."""

    def _run(self, items, only_if_on=False):
        connect = _scripted_connect(items)
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(redirect_stdout(io.StringIO()))
            return asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=only_if_on))

    def test_apply_retries_then_succeeds(self):
        d_init = FakeDevice(apply_raises=RuntimeError("f1"))
        d1 = FakeDevice(apply_raises=RuntimeError("f2"))
        d2 = FakeDevice()
        d_verify = FakeDevice(ieco=True, power_state=True)
        self.assertTrue(self._run([d_init, d1, d2, d_verify]))
        self.assertEqual([d_init.apply_calls, d1.apply_calls, d2.apply_calls],
                         [1, 1, 1])
        # Am HANDELNDEN Objekt pruefen, nicht am vorkonfigurierten
        # Verifikationsgeraet: jedes per Reconnect frisch aufgebaute Geraet muss
        # den gewuenschten Zustand auch wirklich gesetzt bekommen. Ohne diese
        # Zusicherung liess sich 'device.ieco = True' aus dem Reconnect-Zweig
        # ersatzlos entfernen - die Wiederholungen haetten dann erfolgreich
        # ausgesehen, ohne iECO je zu setzen (dasselbe Anti-Muster, das
        # StateChangeTests fuer den Normalpfad bereits beseitigt hat).
        for reconnected in (d1, d2):
            self.assertIs(reconnected.ieco, True)
            self.assertIs(reconnected.power_state, True)

    def test_reconnect_failure_no_apply_on_dead_object(self):
        d_init = FakeDevice(apply_raises=RuntimeError("apply fail"))
        self.assertFalse(self._run([d_init, RuntimeError("reconnect boom")]))
        # Kein zweiter apply() auf dem alten, bereits geschlossenen Objekt:
        self.assertEqual(d_init.apply_calls, 1)

    def test_reconnect_caps_timeout_returns_false_without_raising(self):
        d_init = FakeDevice(apply_raises=RuntimeError("apply fail"))
        d1 = FakeDevice(caps_raises=TimeoutError("caps timeout"))
        # Mit dem alten 'except RuntimeError' haette der TimeoutError den
        # ganzen Lauf mit einem Traceback beendet; jetzt sauberer False.
        self.assertFalse(self._run([d_init, d1]))

    def test_only_if_on_and_off_skips_caps_and_apply(self):
        d = FakeDevice(online=True, power_state=False, ieco=False)
        self.assertTrue(self._run([d], only_if_on=True))
        self.assertEqual((d.caps_calls, d.apply_calls), (0, 0))


class EmptyAllTests(unittest.TestCase):
    """C3: 'all' auf leerer devices.json meldet klar Exit 1 statt still 'OK'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def test_all_on_empty_devices_exits_1(self):
        # all([]) waere True -> ohne Guard faelschlich Exit 0. Guard: Exit 1.
        self.path.write_text('{"devices": []}', encoding="utf-8")
        with mock.patch.object(mie.sys, "argv", ["midea_ieco_ensure.py", "all"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("No devices configured", out.getvalue())

    def test_all_on_nonempty_devices_does_not_trip_guard(self):
        # Positivfall: bei >=1 Geraet greift der Guard NICHT; main() laeuft
        # durch (ensure_ieco gestubbt) und endet mit Exit 0.
        self.path.write_text('{"devices": [{"name": "X", "ip": "1", "id": "1"}]}',
                             encoding="utf-8")

        async def _ok(dev_conf, only_if_on):
            return True

        with mock.patch.object(mie.sys, "argv", ["midea_ieco_ensure.py", "all"]), \
                mock.patch.object(mie, "ensure_ieco", _ok), \
                mock.patch.object(mie.asyncio, "sleep", _anoop), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 0)
        self.assertNotIn("No devices configured", out.getvalue())


class CapabilityGatedDevice:
    """Modelliert das reale msmart-ng-Verhalten: device.ieco liefert den WAHREN
    Wert erst, nachdem get_capabilities() (fuellt _supported_properties) UND
    danach refresh() (pollt dann IECO) liefen - vorher immer den Default False.
    Genau das liess die Verifikation frueher faelschlich fehlschlagen."""

    def __init__(self, *, power_state=True, true_ieco=True, supports_ieco=True):
        self.online = True
        self.power_state = power_state
        self.operational_mode = _OpMode.COOL
        self.eco = False
        self.supports_ieco = supports_ieco
        self._true_ieco = true_ieco
        self._caps = False
        self._refreshed_after_caps = False
        self.close_calls = 0
        self._lan = _RecordingLAN(self._note_close)
        self.apply_calls = 0

    def _note_close(self) -> None:
        self.close_calls += 1

    @property
    def ieco(self):
        return self._true_ieco if (self._caps and self._refreshed_after_caps) else False

    @ieco.setter
    def ieco(self, value):
        self._true_ieco = value

    async def get_capabilities(self):
        self._caps = True
        self._refreshed_after_caps = False

    async def refresh(self):
        if self._caps:
            self._refreshed_after_caps = True

    async def apply(self):
        self.apply_calls += 1


class IecoVerificationCapabilityTests(unittest.TestCase):
    """Der wahre ieco-Zustand ist nur nach get_capabilities()+refresh() sichtbar.
    Verifikation UND Initial-Read muessen deshalb Capabilities abfragen - sonst
    laese device.ieco False und der Lauf wuerde faelschlich als Fehlschlag
    gewertet (der gemeldete Bug)."""

    def _run(self, items, only_if_on=False):
        connect = _scripted_connect(items)
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(redirect_stdout(io.StringIO()))
            return asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=only_if_on))

    def test_verification_reads_true_ieco_via_capabilities(self):
        # Geraet aus -> einschalten + iECO setzen. Das Verifikationsgeraet meldet
        # iECO nur, wenn die Verifikation Capabilities+refresh gemacht hat. Der
        # Erfolg beweist with_capabilities=True bei der Verifikation - OHNE den
        # Fix laese ieco False und assertTrue wuerde scheitern.
        d_action = CapabilityGatedDevice(power_state=False, true_ieco=False)
        d_verify = CapabilityGatedDevice(power_state=True, true_ieco=True)
        self.assertTrue(self._run([d_action, d_verify]))

    def test_a_verification_without_an_answer_is_not_called_disabled(self):
        """Antwortet das Geraet auf die Verifikation gar nicht, darf der Lauf
        keine Aussage ueber iECO treffen.

        msmart-ng reicht Netzfehler aus refresh()/get_capabilities() nicht nach
        oben: Device._send_command faengt sie und liefert eine leere
        Antwortliste. connect_and_refresh kehrt dann normal zurueck, und ohne
        die online-Pruefung staenden in device.ieco die DEFAULTS des frischen
        Objekts. Der Lauf meldete daraufhin 'iECO ist laut Geraet weiterhin
        deaktiviert' - eine Ursachenaussage ueber ein Geraet, das nichts gesagt
        hat.

        Der Test ist bewusst so gebaut, dass er ohne die Pruefung nicht etwa
        eine andere Meldung liefert, sondern ERFOLG: das Verifikationsgeraet
        traegt true_ieco=True. Nur die fehlende Antwort macht den Unterschied."""
        d_action = CapabilityGatedDevice(power_state=True, true_ieco=False)
        d_verify = CapabilityGatedDevice(power_state=True, true_ieco=True)
        d_verify.online = False

        connect = _scripted_connect([d_action, d_verify])
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            result = asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=False))

        self.assertFalse(result)
        text = out.getvalue()
        self.assertIn("no state back", text)
        # Die alte, falsche Ursachenaussage darf NICHT mehr erscheinen.
        self.assertNotIn("still disabled", text)

    def test_already_on_and_ieco_short_circuits(self):
        # Geraet an und bereits in iECO: der 'schon aktiv'-Kurzschluss greift nur,
        # weil auch der Initial-Read jetzt Capabilities+refresh macht (sonst laese
        # ieco False und es wuerde unnoetig apply() aufgerufen).
        d = CapabilityGatedDevice(power_state=True, true_ieco=True)
        self.assertTrue(self._run([d]))
        self.assertEqual(d.apply_calls, 0)


class ModeLabelTests(unittest.TestCase):
    """_mode_label: Name UND Zahl, robust gegen unerwartete Werte.

    Hintergrund: msmart-ng's OperationalMode ist ein IntEnum und rendert ab
    Python 3.11 im f-String als nackte Zahl - 'mode=5' ist in einem Bugreport
    kaum zu deuten."""

    def test_enum_shows_name_and_value(self):
        self.assertEqual(mie._mode_label(_OpMode.FAN_ONLY), "FAN_ONLY (5)")
        self.assertEqual(mie._mode_label(_OpMode.COOL), "COOL (2)")

    def test_plain_int_still_readable(self):
        self.assertEqual(mie._mode_label(2), "2")

    def test_unknown_object_does_not_crash(self):
        self.assertEqual(mie._mode_label(None), "None")

    def test_mode_value_extracts_int(self):
        self.assertEqual(mie._mode_value(_OpMode.COOL), 2)
        self.assertEqual(mie._mode_value(4), 4)

    def test_mode_value_none_when_undeterminable(self):
        self.assertIsNone(mie._mode_value(None))
        self.assertIsNone(mie._mode_value("cool"))


class ModeGuardTests(unittest.TestCase):
    """Modus-Guard (Issue #3): iECO ist an den Betriebsmodus gebunden.

    Am echten Geraet gemessen (2026-07-27): COOL und HEAT tragen iECO, AUTO,
    DRY und FAN_ONLY nicht. In einem nicht tragenden Modus darf KEIN apply()
    mehr abgesetzt werden - es waere garantiert vergeblich."""

    # Exakt die am echten Geraet gemessene Aufteilung.
    CAPABLE = (_OpMode.COOL, _OpMode.HEAT)
    NOT_CAPABLE = (_OpMode.AUTO, _OpMode.DRY, _OpMode.FAN_ONLY)

    def _run(self, mode, *, only_if_on=False, ieco=False, power=True):
        d_action = FakeDevice(power_state=power, ieco=ieco)
        d_action.operational_mode = mode
        d_verify = FakeDevice(power_state=True, ieco=True)
        d_verify.operational_mode = mode
        connect = _scripted_connect([d_action, d_verify])
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            result = asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=only_if_on))
        return result, out.getvalue(), d_action

    def test_capable_modes_proceed_to_apply(self):
        for mode in self.CAPABLE:
            with self.subTest(mode=mode):
                ok, _, dev = self._run(mode)
                self.assertTrue(ok)
                self.assertEqual(dev.apply_calls, 1)

    def test_incapable_modes_skip_apply_entirely(self):
        # Kern des Fixes: kein vergeblicher Schreibvorgang, kein Roundtrip.
        for mode in self.NOT_CAPABLE:
            with self.subTest(mode=mode):
                _, out, dev = self._run(mode)
                self.assertEqual(dev.apply_calls, 0)
                self.assertIn("does not support iECO", out)

    def test_incapable_mode_is_failure_on_explicit_call(self):
        # Ohne --only-if-on wollte der Nutzer aktiv iECO -> kein stilles "OK".
        for mode in self.NOT_CAPABLE:
            with self.subTest(mode=mode):
                ok, out, _ = self._run(mode, only_if_on=False)
                self.assertFalse(ok)
                self.assertIn("Nothing was switched", out)

    def test_incapable_mode_is_no_error_with_only_if_on(self):
        # Im Cron ist ein bewusst gewaehlter Modus kein Fehlerzustand, sonst
        # gaebe es 72 Fehlmeldungen pro Tag.
        for mode in self.NOT_CAPABLE:
            with self.subTest(mode=mode):
                ok, out, _ = self._run(mode, only_if_on=True)
                self.assertTrue(ok)
                self.assertIn("not an error", out)

    def test_active_ieco_short_circuits_before_guard(self):
        # Laeuft iECO bereits, darf eine zu enge Modus-Liste das NICHT als
        # Problem melden - der Kurzschluss greift vorher.
        ok, out, dev = self._run(_OpMode.FAN_ONLY, ieco=True)
        self.assertTrue(ok)
        self.assertEqual(dev.apply_calls, 0)
        self.assertIn("Already in the desired state", out)
        self.assertNotIn("does not support iECO", out)

    def test_unknown_mode_fails_open(self):
        # Laesst sich der Modus nicht bestimmen, wird NICHT blockiert - sonst
        # koennte eine kuenftige msmart-Version funktionierende Geraete lahmlegen.
        ok, out, dev = self._run("unbekannt")
        self.assertTrue(ok)
        self.assertEqual(dev.apply_calls, 1)
        self.assertNotIn("does not support iECO", out)

    def test_off_device_in_incapable_mode_is_not_powered_on(self):
        # Kein Einschalten als halbe Aktion: der Nutzer wollte iECO, nicht
        # blossen Betrieb ohne iECO.
        ok, out, dev = self._run(_OpMode.AUTO, power=False, only_if_on=False)
        self.assertFalse(ok)
        self.assertEqual(dev.apply_calls, 0)
        self.assertFalse(dev.power_state)

    def test_guard_message_names_the_mode_and_the_remedy(self):
        _, out, _ = self._run(_OpMode.FAN_ONLY)
        self.assertIn("FAN_ONLY (5)", out)
        self.assertIn("COOL or HEAT", out)


class OverviewTests(unittest.TestCase):
    """Discoverability: kein Argument und 'list' zeigen eine netzwerkfreie
    Uebersicht (Exit 0) - ohne token/key und ohne je connect_and_refresh
    aufzurufen. Regressionsschutz: echte Geraetenamen verhalten sich unveraendert."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _write(self, obj):
        self.path.write_text(json.dumps(obj), encoding="utf-8")

    def _run(self, argv):
        """Treibt main() mit gepatchtem argv. connect_and_refresh MUSS ungenutzt
        bleiben - jeder Aufruf laesst den Test scheitern (beweist: kein Netz).
        Liefert (exit_code, stdout)."""
        async def _boom(*a, **k):
            raise AssertionError("connect_and_refresh im Overview-Pfad aufgerufen")

        out = io.StringIO()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", _boom))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco"] + argv))
            es.enter_context(redirect_stdout(out))
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        return cm.exception.code, out.getvalue()

    def test_no_arg_prints_overview_exit0(self):
        self._write({"devices": []})
        code, out = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("Examples:", out)
        # Die Uebersicht muss die Schwesterbefehle nennen (Discoverability-Ziel).
        self.assertIn("midea-ieco-refresh-tokens", out)
        self.assertIn("midea-ieco-update", out)

    def test_list_prints_devices_exit0(self):
        self._write({"devices": [{"name": "Wohnzimmer", "ip": "192.168.0.5", "port": 6444}]})
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("Wohnzimmer", out)
        self.assertIn("192.168.0.5:6444", out)

    def test_overview_never_prints_secrets(self):
        # Kernsicherheit der Funktion: token/key duerfen NIE in der Ausgabe stehen.
        self._write({"devices": [{"name": "X", "ip": "1.2.3.4", "port": 6444,
                                  "id": 1, "token": "DEADBEEFTOKEN", "key": "CAFEKEY"}]})
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertNotIn("DEADBEEFTOKEN", out)
        self.assertNotIn("CAFEKEY", out)

    def test_missing_config_is_graceful(self):
        # Keine devices.json: die Uebersicht bleibt informativ und endet mit 0.
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("install.sh", out)

    def test_malformed_config_is_graceful(self):
        self.path.write_text("{ kaputt", encoding="utf-8")
        code, out = self._run(["list"])
        self.assertEqual(code, 0)

    def test_list_ignores_only_if_on(self):
        self._write({"devices": []})
        code, _ = self._run(["list", "--only-if-on"])
        self.assertEqual(code, 0)

    def test_reserved_device_name_is_flagged(self):
        # Ein Geraet, das wie ein reserviertes Wort heisst, ist per CLI nicht
        # erreichbar -> die Uebersicht warnt aktiv.
        self._write({"devices": [{"name": "list", "ip": "1.2.3.4", "port": 6444}]})
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("WARNING", out)

    def test_unknown_device_name_still_exits_1(self):
        # Regressionsschutz: ein echter (nicht reservierter) Name bleibt Exit 1.
        self._write({"devices": [{"name": "Wohnzimmer", "ip": "1.2.3.4", "port": 6444,
                                  "id": 1, "token": "t", "key": "k"}]})
        code, _ = self._run(["Nichtvorhanden"])
        self.assertEqual(code, 1)

    def test_malformed_device_entries_are_graceful(self):
        # Von Hand kaputt editierte devices.json mit Nicht-Objekt-Eintraegen
        # darf die Uebersicht NICHT mit einem Traceback abbrechen (Ziel:
        # funktioniert auch bei kaputter Datei). Der gueltige Eintrag bleibt da.
        self._write({"devices": ["oops", 123, None,
                                 {"name": "Wohnzimmer", "ip": "1.2.3.4", "port": 6444}]})
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("Wohnzimmer", out)
        self.assertIn("skipped", out)

    def test_nonstring_name_does_not_crash_or_leak(self):
        # Ein Objekt unter 'name' ist unhashbar -> die reservierte-Wort-Pruefung
        # wuerde ohne Guard mit TypeError abbrechen. Mit Guard: Exit 0, und der
        # verschachtelte Inhalt wird nicht ausgegeben.
        self._write({"devices": [{"name": {"token": "SECRETNAME"}, "ip": "1.2.3.4"}]})
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertNotIn("SECRETNAME", out)

    def test_invalid_utf8_config_is_graceful(self):
        # Nicht-UTF-8-devices.json (z.B. Geraetename in Latin-1 gespeichert,
        # Byte 0xFC) loest UnicodeDecodeError aus - ein ValueError, NICHT
        # JSONDecodeError. Die Uebersicht muss trotzdem mit Exit 0 samt Hinweis
        # erscheinen statt mit einem Traceback abzubrechen.
        self.path.write_bytes(b'{"devices": [{"name": "K\xfcche", "ip": "1.2.3.4"}]}')
        code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("Note", out)


class OverviewWithoutMsmartTests(unittest.TestCase):
    """Der Lazy-Import macht die Uebersicht unabhaengig von msmart: `list` muss
    auch dann mit Exit 0 laufen, wenn msmart NICHT installiert ist - frueher
    scheiterte schon der Top-Level-Import. Nur pruefbar, wenn msmart im aktiven
    Interpreter fehlt; sonst uebersprungen (analog MsmartMissingProbeTests in
    test_refresh_tokens.py)."""

    def test_list_runs_without_msmart(self):
        probe = subprocess.run([sys.executable, "-c", "import msmart"],
                               capture_output=True)
        if probe.returncode == 0:
            self.skipTest("msmart installiert - der msmart-freie Pfad ist nicht pruefbar")
        # Der echte Skriptlauf importiert msmart NICHT (Lazy-Import erst beim
        # Geraetezugriff), die Uebersicht muss also sauber mit Exit 0 erscheinen.
        # Sprache explizit mitgeben: der Unterprozess erbt zwar os.environ (und
        # damit das Pinning aus setUpModule), aber darauf soll sich der Test
        # nicht stillschweigend verlassen.
        env = {**os.environ, "MIDEA_IECO_LANG": "en"}
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_ieco_ensure.py"), "list"],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Examples:", result.stdout)


class OverviewWithoutMsmartAnywhereTests(unittest.TestCase):
    """Die Uebersicht laeuft ohne msmart - ueberall pruefbar.

    OverviewWithoutMsmartTests oben ueberspringt sich selbst, sobald msmart
    installiert ist; auf einem Entwicklerrechner ist der Lazy-Import damit
    unbewacht. Hier wird der Import im Unterprozess gezielt blockiert
    (sys.modules['msmart'] = None -> ImportError), sodass der Pfad unabhaengig
    von der Umgebung laeuft. Die Skripte laufen aus einer Kopie in einem leeren
    Verzeichnis: so sieht der Lauf garantiert keine echte devices.json."""

    def test_list_runs_without_msmart(self):
        work = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
        # ALLE Module aus dem Repo-Wurzelverzeichnis kopieren statt einer
        # gepflegten Namensliste: die Werkzeuge importieren einander (i18n,
        # Verbindungsabbau), und eine Liste, an die man bei jedem neuen Modul
        # denken muss, faellt genau dann um, wenn eines dazukommt.
        for path in sorted(REPO_DIR.glob("*.py")):
            shutil.copy(path, work)
        target = os.path.join(work, "midea_ieco_ensure.py")
        code = (
            "import runpy, sys\n"
            f"sys.path.insert(0, {work!r})\n"
            "sys.modules['msmart'] = None\n"
            "sys.argv = ['midea_ieco_ensure.py', 'list']\n"
            f"runpy.run_path({target!r}, run_name='__main__')\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, cwd=work,
                                env={**os.environ, "MIDEA_IECO_LANG": "en"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Examples:", result.stdout)
        # Ohne devices.json bleibt die Uebersicht informativ statt leer.
        self.assertIn("install.sh", result.stdout)


class MalformedEntryDeviceSelectionTests(unittest.TestCase):
    """Auch der Steuerungspfad (all / <name>) - nicht nur die Uebersicht - darf
    an einem Nicht-Objekt-Eintrag in devices.json NICHT mit TypeError abbrechen:
    er wird gemeldet und uebersprungen, gueltige Geraete werden verarbeitet."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _write(self, obj):
        self.path.write_text(json.dumps(obj), encoding="utf-8")

    def _run(self, argv):
        self.processed = []

        async def _ok(dev_conf, only_if_on):
            self.processed.append(dev_conf)
            return True

        async def _boom(*a, **k):
            raise AssertionError("connect_and_refresh sollte nicht aufgerufen werden")

        out = io.StringIO()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _ok))
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", _boom))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco"] + argv))
            es.enter_context(redirect_stdout(out))
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        return cm.exception.code, out.getvalue()

    def test_all_skips_nondict_and_processes_valid(self):
        self._write({"devices": ["oops", 123, None,
                                 {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]})
        code, out = self._run(["all"])
        self.assertEqual(code, 0)
        self.assertIn("WARNING", out)
        self.assertEqual(len(self.processed), 1)

    def test_named_target_found_despite_nondict_sibling(self):
        # Ohne den Guard wuerde d["name"] auf "oops" hier mit TypeError abbrechen.
        self._write({"devices": ["oops",
                                 {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]})
        code, _ = self._run(["W"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.processed), 1)

    def test_all_with_only_nondict_entries_exits_1(self):
        self._write({"devices": ["oops", 123]})
        code, out = self._run(["all"])
        self.assertEqual(code, 1)
        self.assertIn("WARNING", out)
        self.assertEqual(len(self.processed), 0)

    def test_all_skips_incomplete_dict(self):
        # Objekt ohne ip/id: gemeldet + uebersprungen, gueltiges verarbeitet.
        self._write({"devices": [{"name": "X"},
                                 {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]})
        code, out = self._run(["all"])
        self.assertEqual(code, 0)
        self.assertIn("incomplete", out)
        self.assertEqual(len(self.processed), 1)

    def test_named_incomplete_target_not_found(self):
        # Der benannte Ziel-Eintrag ist unvollstaendig -> uebersprungen ->
        # 'nicht gefunden' Exit 1, mit erklaerender Warnung (kein Crash).
        self._write({"devices": [{"name": "W", "ip": "1.2.3.4"}]})  # kein id
        code, out = self._run(["W"])
        self.assertEqual(code, 1)
        self.assertIn("incomplete", out)
        self.assertEqual(len(self.processed), 0)

    def test_noninteger_id_skipped(self):
        self._write({"devices": [{"name": "B", "ip": "1.2.3.4", "id": "abc", "token": "t", "key": "k"},
                                 {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]})
        code, out = self._run(["all"])
        self.assertEqual(code, 0)
        self.assertIn("numeric", out)
        self.assertEqual(len(self.processed), 1)

    def test_bad_port_skipped(self):
        # Nicht-numerischer port wuerde in connect_and_refresh ungefangen
        # abbrechen -> vorab ueberspringen; das gueltige Geraet laeuft normal.
        self._write({"devices": [{"name": "B", "ip": "1.2.3.4", "id": 1, "port": "abc",
                                  "token": "t", "key": "k"},
                                 {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]})
        code, out = self._run(["all"])
        self.assertEqual(code, 0)
        self.assertIn("port", out)
        self.assertEqual(len(self.processed), 1)

    def test_incomplete_only_no_crash_real_path(self):
        # Direkter Crash-Schutz: OHNE Mock von ensure_ieco/connect_and_refresh.
        # Ohne den Guard wuerde connect_and_refresh auf {"name":"X"} mit einem
        # ungefangenen KeyError('ip') abbrechen. Mit Guard wird X vorab
        # uebersprungen -> devices leer -> Exit 1, ohne je zu verbinden.
        self._write({"devices": [{"name": "X"}]})
        with mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("incomplete", out.getvalue())


class DeviceConfigProblemTests(unittest.TestCase):
    """_device_config_problem: gueltige Geraete -> None; fehlendes name/ip/id
    oder nicht-numerische id -> Klartext-Grund (das sind genau die Felder, die
    connect_and_refresh ausserhalb seines try liest)."""

    def test_complete_device_ok(self):
        self.assertIsNone(mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": 12345, "token": "t", "key": "k"}))

    def test_numeric_string_id_ok(self):
        self.assertIsNone(mie._device_config_problem({"name": "W", "ip": "1.2.3.4", "id": "123"}))

    def test_missing_ip_flagged(self):
        self.assertIn("ip", mie._device_config_problem({"name": "W", "id": 1}))

    def test_missing_id_flagged(self):
        self.assertIn("id", mie._device_config_problem({"name": "W", "ip": "1.2.3.4"}))

    def test_noninteger_id_flagged(self):
        self.assertIn("numeric", mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": "abc"}))

    def test_none_and_list_id_flagged(self):
        self.assertIn("numeric", mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": None}))
        self.assertIn("numeric", mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": [1]}))

    def test_valid_or_absent_port_ok(self):
        # port ist optional: fehlt es (Default 6444) oder ist es int-konvertibel,
        # ist es kein Problem.
        self.assertIsNone(mie._device_config_problem({"name": "W", "ip": "1.2.3.4", "id": 1}))
        self.assertIsNone(mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": 1, "port": 6444}))
        self.assertIsNone(mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": 1, "port": "6444"}))

    def test_noninteger_port_flagged(self):
        # port wird in connect_and_refresh ebenfalls ausserhalb des try mit int()
        # konvertiert -> nicht-numerischer/leerer/None/Listen-Wert = Absturz.
        for bad in ("abc", "", None, [1]):
            self.assertIn("port", mie._device_config_problem(
                {"name": "W", "ip": "1.2.3.4", "id": 1, "port": bad}))


class _RecordingAC:
    """Aufzeichnendes Ersatz-Geraet fuer connect_and_refresh (siehe dortigen
    Testkommentar). Ersetzt msmart-ngs AirConditioner im Stub-Modul."""

    instances: list["_RecordingAC"] = []

    def __init__(self, *, ip=None, port=None, device_id=None):
        self.init_args = (ip, port, device_id)
        self.auth_args = None
        self.calls: list[str] = []
        self.online = True
        self.power_state = True
        self.ieco = True
        self.eco = False
        self.operational_mode = _OpMode.COOL
        self.supports_ieco = True
        self.auth_raises = None
        # Das Schliessen laeuft ueber dieselbe private Kette wie beim echten
        # Objekt und wird in DIESELBE Reihenfolgeliste protokolliert - die
        # Tests unten pruefen die Abfolge authenticate/refresh/close.
        self._lan = _RecordingLAN(lambda: self.calls.append("close"))
        _RecordingAC.instances.append(self)

    async def authenticate(self, token, key):
        self.auth_args = (token, key)
        self.calls.append("authenticate")
        if self.auth_raises is not None:
            raise self.auth_raises

    async def get_capabilities(self):
        self.calls.append("get_capabilities")

    async def refresh(self):
        self.calls.append("refresh")


class ConnectAndRefreshBodyTests(unittest.TestCase):
    """Direkter Test von connect_and_refresh - bisher in JEDEM Test ersetzt.

    Der Rumpf war dadurch voellig ungeprueft: ein vertauschtes Argumentpaar in
    authenticate(token, key) haette jede Geraeteverbindung fuer jeden Nutzer
    zerstoert, ohne dass ein Test rot wurde."""

    # Port bewusst NICHT der Default 6444: mit dem Default liesse sich ein
    # hartkodiertes 6444 in connect_and_refresh nicht von der korrekten
    # Auswertung des Eintrags unterscheiden - der Schwestertest im anderen
    # Modul faengt dieselbe Mutation genau deshalb.
    DEV = {"name": "W", "ip": "1.2.3.4", "port": 6445, "id": 42,
           "token": "MEIN_TOKEN", "key": "MEIN_KEY"}

    def setUp(self):
        _RecordingAC.instances = []
        module = sys.modules["msmart.device.AC.device"]
        original = module.AirConditioner
        module.AirConditioner = _RecordingAC
        self.addCleanup(lambda: setattr(module, "AirConditioner", original))

    def _connect(self, dev=None, **kwargs):
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(redirect_stdout(io.StringIO()))
            return asyncio.run(mie.connect_and_refresh(dict(dev or self.DEV), **kwargs))

    def test_authenticate_receives_token_first_then_key(self):
        self._connect()
        self.assertEqual(_RecordingAC.instances[0].auth_args,
                         ("MEIN_TOKEN", "MEIN_KEY"))

    def test_device_is_constructed_with_ip_port_and_id(self):
        self._connect()
        self.assertEqual(_RecordingAC.instances[0].init_args, ("1.2.3.4", 6445, 42))

    def test_missing_port_defaults_to_6444(self):
        dev = {k: v for k, v in self.DEV.items() if k != "port"}
        self._connect(dev)
        self.assertEqual(_RecordingAC.instances[0].init_args[1], 6444)

    def test_without_capabilities_only_refresh_runs(self):
        self._connect()
        self.assertEqual(_RecordingAC.instances[0].calls, ["authenticate", "refresh"])

    def test_with_capabilities_queries_them_before_refresh(self):
        # Reihenfolge ist wesentlich: refresh() pollt die IECO-Property nur,
        # wenn get_capabilities() sie zuvor in _supported_properties eingetragen
        # hat. Andersherum laese device.ieco immer den Default False.
        self._connect(with_capabilities=True)
        self.assertEqual(_RecordingAC.instances[0].calls,
                         ["authenticate", "get_capabilities", "refresh"])

    def test_every_retry_uses_a_brand_new_device_object(self):
        # Ein fehlgeschlagener Versuch hinterlaesst einen defekten Socket-
        # Zustand; deshalb muss je Versuch ein FRISCHES Objekt entstehen.
        with mock.patch.object(_RecordingAC, "authenticate",
                               side_effect=RuntimeError("nope"), autospec=True):
            with self.assertRaises(RuntimeError):
                self._connect(retries=3)
        self.assertEqual(len(_RecordingAC.instances), 3)

    def test_gives_up_with_runtimeerror_after_the_configured_retries(self):
        with mock.patch.object(_RecordingAC, "authenticate",
                               side_effect=RuntimeError("nope"), autospec=True):
            with self.assertRaises(RuntimeError):
                self._connect(retries=2)
        self.assertEqual(len(_RecordingAC.instances), 2)

    def test_default_retry_count_is_used_when_not_given(self):
        with mock.patch.object(_RecordingAC, "authenticate",
                               side_effect=RuntimeError("nope"), autospec=True):
            with self.assertRaises(RuntimeError):
                self._connect()
        self.assertEqual(len(_RecordingAC.instances), mie.CONNECT_RETRIES)


class ConnectPacingOrderTests(unittest.TestCase):
    """Die Pause zwischen zwei VERBINDUNGSVERSUCHEN, reihenfolgegenau.

    Von den vier sleep-Aufrufen des Moduls waren drei reihenfolgegenau
    festgeschrieben (Settle nach apply, Pause im apply-Retry, Pause zwischen den
    Geraeten) - ausgerechnet der, der die Verbindungsversuche entzerrt, liess
    sich ersatzlos entfernen. Genau dafuer gibt es das Pacing: das Geraet
    vertraegt nur EINE lokale Verbindung und blockiert nach dichten Zugriffen
    voruebergehend. Ohne die Pause erzeugt der Wiederholungsversuch also die
    Stoerung, die er ueberwinden soll.

    Geprueft wird die REIHENFOLGE, nicht die Anzahl: eine Pause hinter dem
    letzten Versuch entzerrt nichts, ergibt bei einem Zaehltest aber dieselbe
    Zahl."""

    DEV = {"name": "W", "ip": "1.2.3.4", "port": 6445, "id": 42,
           "token": "t", "key": "k"}

    def setUp(self):
        _RecordingAC.instances = []
        module = sys.modules["msmart.device.AC.device"]
        original = module.AirConditioner
        module.AirConditioner = _RecordingAC
        self.addCleanup(lambda: setattr(module, "AirConditioner", original))

    def _events(self, retries, succeed_at=None):
        """Ereignisliste eines connect_and_refresh-Laufs.

        Versuche und Pausen landen in DERSELBEN Liste - nur so ist ihre
        Reihenfolge zueinander ueberhaupt vergleichbar."""
        events = []
        state = {"n": 0}

        async def _authenticate(self_ac, token, key):
            state["n"] += 1
            events.append("versuch")
            if succeed_at is None or state["n"] < succeed_at:
                raise RuntimeError("nope")

        async def _sleep(seconds):
            events.append(f"pause {seconds}")

        with ExitStack() as es:
            es.enter_context(mock.patch.object(_RecordingAC, "authenticate",
                                               _authenticate))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _sleep))
            es.enter_context(redirect_stdout(io.StringIO()))
            with suppress(RuntimeError):
                asyncio.run(mie.connect_and_refresh(dict(self.DEV),
                                                    retries=retries))
        return events

    def test_a_pause_sits_between_two_attempts(self):
        pause = f"pause {mie.RETRY_DELAY}"
        self.assertEqual(self._events(3),
                         ["versuch", pause, "versuch", pause, "versuch"])

    def test_no_pause_follows_the_last_attempt(self):
        # Eine Pause nach dem letzten Versuch verzoegert nur die Fehlermeldung.
        events = self._events(2)
        self.assertEqual(events[-1], "versuch")
        self.assertEqual(events, ["versuch", f"pause {mie.RETRY_DELAY}", "versuch"])

    def test_a_successful_first_attempt_never_pauses(self):
        self.assertEqual(self._events(3, succeed_at=1), ["versuch"])

    def test_the_pause_precedes_the_attempt_that_succeeds(self):
        self.assertEqual(self._events(3, succeed_at=2),
                         ["versuch", f"pause {mie.RETRY_DELAY}", "versuch"])


class RenderedMessageOrderTests(unittest.TestCase):
    """Mehr-Platzhalter-Meldungen an ihren ECHTEN Aufrufstellen.

    Die AST-Pruefung im Schwestermodul zaehlt nur die ANZAHL der Argumente; ein
    vertauschtes Paar ergibt eine syntaktisch einwandfreie, inhaltlich falsche
    Meldung und blieb bislang unbemerkt. Am schwersten wiegt das bei den
    Statuszeilen: dort meldet ein Tausch den Wert von 'eco' als 'iECO' - eine
    Falschauskunft in genau der Ausgabe, um die es diesem Projekt geht."""

    def _run(self, items, only_if_on=False):
        connect = _scripted_connect(items)
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            asyncio.run(mie.ensure_ieco({"name": "Wohnzimmer", "ip": "1", "id": "1"},
                                        only_if_on=only_if_on))
        return out.getvalue()

    def _assert_order(self, text, *parts):
        """Vergleicht die ERSTVORKOMMEN der Teilstuecke im gesamten Text.

        Was dieser Helfer NICHT leistet: er sieht nicht die einzelne Zeile.
        Steht ein Teilstueck schon in einer frueheren, korrekten Zeile, ist die
        Behauptung erfuellt, obwohl die gemeinte Zeile vertauscht sein kann.
        Dagegen helfen nur die Vollzeilen-Zusicherungen weiter unten.

        Auf Erstvorkommen und nicht fortlaufend zu suchen ist Absicht: eine
        fortlaufende Suche findet ein vertauschtes Paar im naechsten
        Wiederholungsversuch erneut und faellt genau dadurch nicht mehr auf.
        Gemessen an conn_attempt_failed, das dabei still ueberlebte."""
        last = -1
        for part in parts:
            self.assertIn(part, text)
            index = text.index(part)
            self.assertGreater(index, last,
                               f"{part!r} steht an der falschen Stelle: {text!r}")
            last = index

    # Vier unterscheidbare Werte in EINER Zeile: nur so faellt jeder Tausch
    # benachbarter Argumente auf. Insbesondere ieco=True/eco=False - waeren
    # beide gleich, bliebe genau der gefaehrlichste Tausch unsichtbar.
    STATUS_TAIL = "power=True, mode=COOL (2), ieco=True, eco=False"

    def test_status_before_lists_power_mode_ieco_eco_in_that_order(self):
        out = self._run([FakeDevice(power_state=True, ieco=True)])
        self.assertIn(f"[Wohnzimmer] Status before action: {self.STATUS_TAIL}", out)

    def test_status_after_lists_power_mode_ieco_eco_in_that_order(self):
        out = self._run([FakeDevice(power_state=False, ieco=False),
                         FakeDevice(power_state=True, ieco=True)])
        self.assertIn(f"[Wohnzimmer] Status after action: {self.STATUS_TAIL}", out)

    def test_capability_failure_names_device_then_type_then_detail(self):
        out = self._run([FakeDevice(power_state=True,
                                    caps_raises=TimeoutError("KAPUTT"))])
        self._assert_order(out, "[Wohnzimmer]", "TimeoutError", "KAPUTT")

    def test_apply_attempt_line_counts_attempt_of_total(self):
        out = self._run([FakeDevice(power_state=True,
                                    apply_raises=RuntimeError("APPLYBOOM")),
                         RuntimeError("stop")])
        self._assert_order(out, "[Wohnzimmer]", f"1/{mie.ACTION_RETRIES}",
                           "RuntimeError", "APPLYBOOM")

    def test_reconnect_failure_names_device_then_type_then_detail(self):
        out = self._run([FakeDevice(power_state=True,
                                    apply_raises=RuntimeError("x")),
                         RuntimeError("RECONNECTBOOM")])
        self._assert_order(out, "[Wohnzimmer]", "RuntimeError", "RECONNECTBOOM")

    def test_mode_guard_names_the_actual_mode_before_the_capable_ones(self):
        device = FakeDevice(power_state=True, ieco=False)
        device.operational_mode = _OpMode.FAN_ONLY
        out = self._run([device])
        self._assert_order(out, "[Wohnzimmer]", "FAN_ONLY (5)", "COOL or HEAT")

    # --- vollstaendig gerenderte Zeilen -----------------------------------
    # Die Zusicherungen oben pruefen die REIHENFOLGE einzelner Bruchstuecke im
    # Gesamttext. Das reicht nicht: eine korrekte fruehere Zeile kann die
    # Reihenfolge-Behauptung erfuellen, waehrend die gepruefte Zeile vertauscht
    # ist. Wirksam ist nur der Vergleich mit der KOMPLETTEN gerenderten Zeile -
    # so wie es die Statuszeilen oben schon tun. Die Erwartung entsteht aus dem
    # Katalog (t(...)), damit eine Umformulierung sie mitnimmt statt sie still
    # zu veralten; festgehalten wird allein die Argument-REIHENFOLGE.

    def test_device_error_line_renders_name_then_reason(self):
        out = self._run([RuntimeError("VERBINDUNGSGRUND")])
        self.assertIn(mie.t("dev_error", "Wohnzimmer", "VERBINDUNGSGRUND"), out)

    def test_mode_guard_lines_render_in_full(self):
        device = FakeDevice(power_state=True, ieco=False)
        device.operational_mode = _OpMode.FAN_ONLY
        out = self._run([device])
        self.assertIn(mie.t("dev_mode_unsupported", "Wohnzimmer", "FAN_ONLY (5)",
                            mie.t("mode_names_capable")), out)
        self.assertIn(mie.t("dev_mode_nothing_switched", "Wohnzimmer",
                            mie.t("mode_names_capable")), out)

    def test_reconnect_and_apply_failure_lines_render_in_full(self):
        out = self._run([FakeDevice(power_state=True, ieco=False,
                                    apply_raises=RuntimeError("APPLYBOOM")),
                         RuntimeError("RECONNECTBOOM")])
        self.assertIn(mie.t("dev_reconnect_failed", "Wohnzimmer", "RuntimeError",
                            "RECONNECTBOOM"), out)
        # last_exc ist hier der Reconnect-Fehler: er war der letzte.
        self.assertIn(mie.t("dev_apply_failed", "Wohnzimmer", "RECONNECTBOOM"), out)

    def test_verify_failure_line_renders_name_then_reason(self):
        out = self._run([FakeDevice(power_state=True, ieco=False),
                         RuntimeError("VERIFYBOOM")])
        self.assertIn(mie.t("dev_verify_failed", "Wohnzimmer", "VERIFYBOOM"), out)

    def test_capability_failure_line_renders_in_full(self):
        out = self._run([FakeDevice(power_state=True,
                                    caps_raises=TimeoutError("KAPUTT"))])
        self.assertIn(mie.t("dev_caps_failed", "Wohnzimmer", "TimeoutError",
                            "KAPUTT"), out)

    # --- Schwaerzung: dieses Werkzeug ist der haeufigere der beiden Wege ------
    # Der Token-Abruf laeuft woechentlich, DIESES Werkzeug alle 20 Minuten - und
    # es ruft authenticate(token, key) unmittelbar vor den Meldungen hier. Ein
    # Token im Text einer Bibliotheksausnahme landete damit 72-mal am Tag im
    # ieco.log, das mit der normalen umask (meist 0644) entsteht, waehrend
    # dieselben Werte in devices.json mit 0600 geschuetzt sind. Die Schwaerzung
    # sitzt in midea_i18n.make_translator und deckt deshalb JEDE Katalogzeile
    # ab; geprueft wird sie hier am tatsaechlich gedruckten Text.
    def test_a_token_in_a_library_error_never_reaches_the_log(self):
        token = "a1b2c3d4" * 16   # 128 Hex, Laenge eines echten Tokens
        out = self._run([RuntimeError(f"auth failed for token {token}")])
        self.assertNotIn(token, out)
        self.assertIn("[hex:128]", out)

    def test_connection_attempt_line_counts_attempt_of_total(self):
        # connect_and_refresh laeuft hier ECHT (nur das AC-Objekt ist ein Fake).
        _RecordingAC.instances = []
        module = sys.modules["msmart.device.AC.device"]
        original = module.AirConditioner
        module.AirConditioner = _RecordingAC
        self.addCleanup(lambda: setattr(module, "AirConditioner", original))
        out = io.StringIO()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(_RecordingAC, "authenticate",
                                               side_effect=RuntimeError("CONNBOOM"),
                                               autospec=True))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(redirect_stdout(out))
            with self.assertRaises(RuntimeError) as cm:
                asyncio.run(mie.connect_and_refresh(
                    {"name": "Wohnzimmer", "ip": "1.2.3.4", "port": 6445, "id": 42,
                     "token": "t", "key": "k"}, retries=2))
        text = out.getvalue()
        self._assert_order(text, "[Wohnzimmer]", "1/2", "RuntimeError", "CONNBOOM")
        # Vollstaendig gerendert: die Zeile wiederholt sich je Versuch, und
        # eine Reihenfolge-Pruefung ueber den Gesamttext kann ein vertauschtes
        # Paar in der Wiederholung uebersehen.
        self.assertIn(mie.t("conn_attempt_failed", "Wohnzimmer", 1, 2,
                            "RuntimeError", "CONNBOOM"), text)
        self.assertIn(mie.t("conn_attempt_failed", "Wohnzimmer", 2, 2,
                            "RuntimeError", "CONNBOOM"), text)
        # Die Aufgabemeldung nennt erst das Geraet, dann die Versuchszahl.
        self._assert_order(str(cm.exception), "Wohnzimmer", "2")
        self.assertIn(mie.t("conn_gave_up", "Wohnzimmer", 2), str(cm.exception))


class RenderedOverviewAndConfigLineTests(unittest.TestCase):
    """Vollstaendig gerenderte Zeilen ausserhalb von ensure_ieco.

    Uebersicht und Konfig-Meldungen entstehen an anderen Stellen und blieben
    deshalb von RenderedMessageOrderTests unberuehrt. Am schwersten wiegen die
    Beispielzeilen: sie sind die Anleitung, in die ein Neuling sieht, und
    vertauscht ergeben sie 'all midea-ieco' - ein Kommando, das es nicht gibt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _main(self, argv):
        out = io.StringIO()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie.sys, "argv",
                                               ["midea-ieco"] + argv))
            es.enter_context(redirect_stdout(out))
            with self.assertRaises(SystemExit):
                asyncio.run(mie.main())
        return out.getvalue()

    def test_example_lines_pair_the_command_with_its_target(self):
        self.path.write_text(json.dumps({"devices": []}), encoding="utf-8")
        out = self._main([])
        self.assertIn(mie.t("ov_example_all", mie.CMD_MAIN, mie.TARGET_ALL), out)
        self.assertIn(mie.t("ov_example_only_if_on", mie.CMD_MAIN, mie.TARGET_ALL),
                      out)
        self.assertIn(mie.t("ov_example_list", mie.CMD_MAIN, mie.TARGET_LIST), out)

    def test_reserved_name_line_names_the_device_then_the_file(self):
        # Ein Geraet, das 'all' heisst, ist per CLI nicht ansteuerbar.
        self.path.write_text(json.dumps({"devices": [
            {"name": "all", "ip": "1.2.3.4", "port": 6444, "id": 1}]}),
            encoding="utf-8")
        out = self._main(["list"])
        self.assertIn(mie.t("ov_reserved_name", "all", mie.CONFIG_PATH.name), out)

    def test_skipped_entries_line_counts_first_then_names_the_file(self):
        self.path.write_text(json.dumps({"devices": [
            "kaputt", 42, {"name": "W", "ip": "1.2.3.4", "id": 1}]}),
            encoding="utf-8")

        async def _ensure(dev_conf, only_if_on):
            return True

        out = io.StringIO()
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _ensure))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv",
                                               ["midea-ieco", "all"]))
            es.enter_context(redirect_stdout(out))
            with self.assertRaises(SystemExit):
                asyncio.run(mie.main())
        self.assertIn(mie.t("main_skipped_entries", 2, mie.CONFIG_PATH.name),
                      out.getvalue())

    # Die Fehlerdetails kommen aus einer GESETZTEN Ausnahme statt aus einer
    # echten kaputten Datei: nur so sind Typname und Text im Test bekannt und
    # die Erwartung bleibt ueber Python-Versionen hinweg stabil.
    def test_unreadable_config_line_names_path_type_and_detail(self):
        self.path.write_text("{}", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(mie.json, "load", side_effect=ValueError("KAPUTT")), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit):
                mie.load_config()
        self.assertIn(mie.t("cfg_unreadable", mie.CONFIG_PATH, "ValueError",
                            "KAPUTT"), out.getvalue())

    def test_soft_unreadable_config_line_names_path_type_and_detail(self):
        # Der weiche Pfad der Uebersicht: er bricht NICHT ab, meldet aber
        # denselben Sachverhalt - und mit denselben drei Werten.
        self.path.write_text("{}", encoding="utf-8")
        with mock.patch.object(mie.json, "load", side_effect=ValueError("KAPUTT")):
            devices, problem = mie._read_devices_soft()
        self.assertEqual(devices, [])
        self.assertEqual(problem, mie.t("soft_cfg_unreadable", mie.CONFIG_PATH,
                                        "ValueError", "KAPUTT"))


class IncompleteEntryMessageOrderTests(unittest.TestCase):
    """main()-Meldung fuer unvollstaendige Eintraege: Name, Datei, Grund."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def test_incomplete_entry_names_device_then_file_then_reason(self):
        self.path.write_text(json.dumps({"devices": [{"name": "Kueche"}]}),
                             encoding="utf-8")
        with mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                asyncio.run(mie.main())
        text = out.getvalue()
        for part, previous in (("devices.json", "'Kueche'"), ("'ip'", "devices.json")):
            self.assertIn(part, text)
            self.assertLess(text.index(previous), text.index(part), text)


class DeviceGuardTests(unittest.TestCase):
    """online- und supports_ieco-Guard: beide Parameter von FakeDevice existierten,
    wurden aber von KEINEM Test je auf False gesetzt - die Guards waren tot."""

    def _run(self, device):
        connect = _scripted_connect([device])
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            result = asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=False))
        return result, out.getvalue(), device

    def test_offline_device_is_a_failure_and_nothing_is_applied(self):
        ok, out, dev = self._run(FakeDevice(online=False, power_state=True))
        self.assertFalse(ok)
        self.assertEqual(dev.apply_calls, 0)
        self.assertIn("not report itself as online", out)

    def test_device_without_ieco_capability_is_a_failure(self):
        ok, out, dev = self._run(
            FakeDevice(online=True, power_state=True, supports_ieco=False))
        self.assertFalse(ok)
        self.assertEqual(dev.apply_calls, 0)
        self.assertIn("no iECO capability", out)


class EnsureIecoFailureReturnsTests(unittest.TestCase):
    """Die drei Fehlerpfade von ensure_ieco muessen False liefern.

    ExitCodeTests ersetzt ensure_ieco vollstaendig durch eine Attrappe und
    sichert damit nur die Abbildung des ERGEBNISSES auf den Exit-Code, nie
    dessen Zustandekommen. 'return False' -> 'return True' ueberlebte hier
    deshalb an drei Stellen: der 20-Minuten-Cron meldete Exit 0, obwohl das
    Geraet nie erreicht wurde. Die Nachbarpfade (offline, keine iECO-Faehigkeit,
    'weiterhin deaktiviert', apply gescheitert) sind laengst abgedeckt - genau
    der Zwillingsfall, um den es hier geht."""

    def _run(self, items, only_if_on=False):
        connect = _scripted_connect(items)
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            result = asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=only_if_on))
        return result, out.getvalue()

    def test_connection_failure_is_a_failure(self):
        result, out = self._run([RuntimeError("nicht erreichbar")])
        self.assertFalse(result)
        self.assertIn("nicht erreichbar", out)

    def test_capability_failure_is_a_failure(self):
        # Der ERSTE get_capabilities()-Aufruf (nicht der im Reconnect-Zweig):
        # ohne Capabilities ist der wahre iECO-Zustand unbekannt, ein 'OK' waere
        # geraten.
        device = FakeDevice(power_state=True, caps_raises=TimeoutError("caps"))
        result, out = self._run([device])
        self.assertFalse(result)
        self.assertEqual(device.apply_calls, 0)
        self.assertIn("get_capabilities", out)

    def test_verification_reconnect_failure_is_a_failure(self):
        # apply() hat geklappt, aber die abschliessende Verifikation kommt nicht
        # mehr zustande: unbestaetigt ist nicht bestaetigt.
        acting = FakeDevice(power_state=True, ieco=False)
        result, out = self._run([acting, RuntimeError("verify weg")])
        self.assertFalse(result)
        self.assertEqual(acting.apply_calls, 1)
        self.assertIn("verify weg", out)

    def test_only_if_on_with_an_off_device_stays_a_success(self):
        # Gegenprobe zur Richtung: der einzige Pfad, der hier bewusst True
        # liefert, muss True bleiben - sonst meldete der Cron fuer jede bewusst
        # ausgeschaltete Anlage einen Fehler.
        result, _ = self._run([FakeDevice(power_state=False)], only_if_on=True)
        self.assertTrue(result)


class ExitCodeFromTheRealEnsureTests(unittest.TestCase):
    """Exit-Code 2 EINMAL mit der echten ensure_ieco statt einer Attrappe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def test_a_real_device_failure_yields_exit_2(self):
        self.path.write_text(json.dumps({"devices": [
            {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]}),
            encoding="utf-8")

        async def _boom(*a, **k):
            raise RuntimeError("Geraet nicht erreichbar")

        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", _boom))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("Geraet nicht erreichbar", out.getvalue())


class ExitCodeTests(unittest.TestCase):
    """Exit-Codes sind die Schnittstelle zu Cron und Monitoring.

    Das Schwestermodul hat diese Absicherung bereits; hier fehlte sie noch, und
    'sys.exit(2)' liess sich bei einem Geraetefehler zu 'sys.exit(0)' aendern,
    ohne dass ein Test rot wurde - ein stiller Fehlschlag im 20-Minuten-Cron,
    den keine Ueberwachung je bemerkt haette."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _run(self, argv, devices, ensure_result=True):
        self.path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

        async def _ensure(dev_conf, only_if_on):
            return ensure_result

        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _ensure))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco"] + argv))
            es.enter_context(redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        return cm.exception.code

    DEV = [{"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]

    def test_success_is_zero(self):
        self.assertEqual(self._run(["all"], self.DEV), 0)

    def test_device_failure_is_two(self):
        self.assertEqual(self._run(["all"], self.DEV, ensure_result=False), 2)

    def test_named_device_failure_is_two(self):
        self.assertEqual(self._run(["W"], self.DEV, ensure_result=False), 2)

    def test_one_failure_among_several_is_still_two(self):
        # all() ueber gemischte Ergebnisse: ein einziges kaputtes Geraet muss
        # den Gesamtlauf als Fehler ausweisen.
        devices = self.DEV + [{"name": "X", "ip": "1.2.3.5", "id": 2,
                               "token": "t", "key": "k"}]
        results = iter([True, False])

        async def _ensure(dev_conf, only_if_on):
            return next(results)

        self.path.write_text(json.dumps({"devices": devices}), encoding="utf-8")
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _ensure))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]))
            es.enter_context(redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_device_is_one(self):
        self.assertEqual(self._run(["Nichtvorhanden"], self.DEV), 1)

    def test_empty_all_is_one(self):
        self.assertEqual(self._run(["all"], []), 1)

    def test_overview_is_zero(self):
        self.assertEqual(self._run(["list"], self.DEV), 0)


class PacingBoundsTests(unittest.TestCase):
    """Die Wartezeiten in midea_ieco_ensure.py sind wegen der Einzelverbindungs-
    Grenze der Firmware tragend, waren aber - anders als beim Schwestermodul -
    an keine Untergrenze gebunden und liessen sich auf 0 setzen."""

    def test_retry_delay_is_long_enough_to_decompress(self):
        self.assertGreaterEqual(mie.RETRY_DELAY, 2.0)

    def test_settle_and_device_delays_are_long_enough_to_matter(self):
        # Untergrenzen wie bei RETRY_DELAY: unterhalb eines Sekundenbruchteils
        # entzerrt nichts mehr, und der Wert waere nur noch Dekoration. Die
        # PLATZIERUNG der Pausen sichert PacingOrderTests.
        self.assertGreaterEqual(mie.SETTLE_DELAY, 1.0)
        self.assertGreaterEqual(mie.DEVICE_DELAY, 0.5)

    def test_retry_budget_is_not_a_single_shot(self):
        self.assertGreaterEqual(mie.CONNECT_RETRIES, 2)
        self.assertGreaterEqual(mie.ACTION_RETRIES, 2)


class _EventDevice(FakeDevice):
    """FakeDevice, das apply() und das Schliessen (ueber _lan._disconnect) in
    eine gemeinsame Ereignisliste protokolliert - damit laesst sich die
    REIHENFOLGE pruefen statt nur die Anzahl."""

    def __init__(self, events, **kwargs):
        super().__init__(**kwargs)
        self._events = events

    async def apply(self):
        self._events.append("apply")
        await super().apply()

    def _note_close(self) -> None:
        self._events.append("close")
        super()._note_close()


class PacingOrderTests(unittest.TestCase):
    """Pausen und Schliessen muessen an der richtigen STELLE liegen.

    Ein Zaehltest kann 'Pause vor dem naechsten Zugriff' nicht von 'Pause
    danach' unterscheiden - und genau darauf kommt es an: eine Pause hinter dem
    letzten Zugriff entzerrt gar nichts. Fuer die Kandidatenpause des
    Schwestermoduls wurde das bereits geloest (CandidatePacingOrderTests); hier
    fehlte der Zwilling, weshalb sich saemtliche sleep- und close-Aufrufe
    ersatzlos entfernen liessen. Bei einem Geraet, das genau EINE lokale
    Verbindung vertraegt, erzeugt das die Stoerung, die das Werkzeug hinterher
    meldet."""

    def _events(self, events, items):
        """Faehrt ensure_ieco und liefert die gemeinsame Ereignisliste zurueck.

        ``events`` wird vom Aufrufer angelegt und auch an die _EventDevice-
        Instanzen gegeben - nur so landen Geraete- und Ablaufereignisse in
        DERSELBEN Liste und ihre Reihenfolge wird vergleichbar."""
        seq = list(items)

        async def _connect(dev_conf, retries=mie.CONNECT_RETRIES,
                           with_capabilities=False):
            events.append("connect")
            item = seq.pop(0)
            if isinstance(item, BaseException):
                raise item
            if with_capabilities:
                await item.get_capabilities()
                await item.refresh()
            return item

        async def _sleep(seconds):
            events.append(f"sleep {seconds}")

        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", _connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _sleep))
            es.enter_context(redirect_stdout(io.StringIO()))
            asyncio.run(mie.ensure_ieco({"name": "X", "ip": "1", "id": "1"},
                                        only_if_on=False))
        return events

    def test_settle_pause_sits_between_apply_and_the_verification(self):
        # Ohne die Pause laese die Verifikation den Zustand, bevor das Geraet
        # ihn uebernommen hat - der Lauf gaelte faelschlich als gescheitert.
        #
        # Die Abfolge apply -> close -> sleep -> connect ist genau festgelegt:
        # geschlossen wird VOR der Pause, damit diese in eine Zeit faellt, in
        # der das Geraet gar keine Verbindung haelt. Laege das Schliessen hinter
        # der Pause, wartete das Werkzeug zwei Sekunden auf einer offenen
        # Sitzung und verbaende sich unmittelbar danach neu - bei einer Anlage,
        # die nur EINE lokale Verbindung vertraegt, ist das die ungeschickteste
        # aller Reihenfolgen.
        events = []
        self.assertEqual(
            self._events(events,
                         [_EventDevice(events, power_state=True, ieco=False),
                          _EventDevice(events, power_state=True, ieco=True)]),
            ["connect", "apply", "close", f"sleep {mie.SETTLE_DELAY}",
             "connect", "close"])

    def test_retry_pause_sits_before_the_reconnect(self):
        # Gleiche Regel im Wiederholpfad: erst schliessen, dann warten, dann
        # neu verbinden.
        events = []
        self.assertEqual(
            self._events(events, [
                _EventDevice(events, power_state=True, ieco=False,
                             apply_raises=RuntimeError("erster Versuch")),
                _EventDevice(events, power_state=True, ieco=False),
                _EventDevice(events, power_state=True, ieco=True)]),
            ["connect", "apply", "close", f"sleep {mie.RETRY_DELAY}", "connect",
             "apply", "close", f"sleep {mie.SETTLE_DELAY}", "connect", "close"])

    def test_the_device_is_closed_even_when_the_run_fails(self):
        # Der finally-Block ist die einzige Zusage, dass eine Verbindung auch im
        # Fehlerfall freigegeben wird.
        events = []
        device = _EventDevice(events, online=False, power_state=True)
        self.assertEqual(self._events(events, [device]), ["connect", "close"])


class DevicePacingOrderTests(unittest.TestCase):
    """Die Pause ZWISCHEN den Geraeten, ebenfalls reihenfolgegenau.

    Sie liegt hier bewusst NACH jedem Geraet (auch nach dem letzten) - anders
    als im Schwestermodul, das VOR jedem weiteren Zugriff pausiert. Dieser Test
    haelt die bestehende Platzierung fest; ohne ihn liess sich der Aufruf
    ersatzlos entfernen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def _events(self, count):
        self.path.write_text(json.dumps({"devices": [
            {"name": f"D{i}", "ip": f"1.2.3.{i}", "id": i, "token": "t", "key": "k"}
            for i in range(count)]}), encoding="utf-8")
        events = []

        async def _ensure(dev_conf, only_if_on):
            events.append(f"geraet {dev_conf['name']}")
            return True

        async def _sleep(seconds):
            events.append(f"pause {seconds}")

        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _ensure))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _sleep))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]))
            es.enter_context(redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit):
                asyncio.run(mie.main())
        return events

    def test_a_pause_follows_every_device(self):
        pause = f"pause {mie.DEVICE_DELAY}"
        self.assertEqual(self._events(3),
                         ["geraet D0", pause, "geraet D1", pause,
                          "geraet D2", pause])

    def test_a_single_device_is_also_followed_by_a_pause(self):
        self.assertEqual(self._events(1),
                         ["geraet D0", f"pause {mie.DEVICE_DELAY}"])


class OnlyIfOnWiringTests(unittest.TestCase):
    """Das Flag --only-if-on muss von main() bis ensure_ieco DURCHGEREICHT werden.

    Die innere Logik des Guards war gut abgedeckt, seine Verdrahtung dagegen gar
    nicht: man konnte in main() 'only_if_on=args.only_if_on' auf 'False'
    festnageln, ohne dass ein Test rot wurde. Das ist der sicherheitskritischste
    Pfad des Projekts - eine Regression dort schaltet per Cron alle 20 Minuten
    jede bewusst ausgeschaltete Anlage EIN."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))
        self.path.write_text(json.dumps({"devices": [
            {"name": "W", "ip": "1.2.3.4", "id": 1, "token": "t", "key": "k"}]}),
            encoding="utf-8")

    def _seen_flag(self, argv):
        seen = []

        async def _capture(dev_conf, only_if_on):
            seen.append(only_if_on)
            return True

        async def _boom(*a, **k):
            raise AssertionError("connect_and_refresh darf hier nicht laufen")

        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "ensure_ieco", _capture))
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", _boom))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            es.enter_context(mock.patch.object(mie.sys, "argv", ["midea-ieco"] + argv))
            es.enter_context(redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit):
                asyncio.run(mie.main())
        self.assertEqual(len(seen), 1, "ensure_ieco wurde nicht genau einmal gerufen")
        return seen[0]

    def test_flag_reaches_ensure_ieco_for_a_named_device(self):
        self.assertIs(self._seen_flag(["W", "--only-if-on"]), True)

    def test_flag_reaches_ensure_ieco_for_all(self):
        self.assertIs(self._seen_flag(["all", "--only-if-on"]), True)

    def test_absent_flag_arrives_as_false(self):
        # Gegenprobe: ohne das Flag darf NICHT versehentlich True ankommen -
        # sonst wuerde eine laufende Anlage nie eingeschaltet.
        self.assertIs(self._seen_flag(["W"]), False)
        self.assertIs(self._seen_flag(["all"]), False)


class StateChangeTests(unittest.TestCase):
    """Die beiden einzigen echten Zustandsaenderungen des Werkzeugs muessen am
    HANDELNDEN Geraet belegt werden.

    Zuvor kam der Erfolg ausschliesslich vom - unabhaengig vorkonfigurierten -
    Verifikationsgeraet: eine Fassung, die weder einschaltet noch iECO setzt noch
    ueberhaupt verifiziert, meldete weiterhin 'OK: iECO is active (confirmed by
    the device)' und bestand die gesamte Suite."""

    def _run(self, d_action, d_verify, only_if_on=False):
        connect = _scripted_connect([d_action, d_verify])
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            result = asyncio.run(mie.ensure_ieco(
                {"name": "X", "ip": "1", "id": "1"}, only_if_on=only_if_on))
        return result, out.getvalue()

    def test_switched_off_device_is_powered_on_and_set_to_ieco(self):
        d_action = FakeDevice(power_state=False, ieco=False)
        ok, _ = self._run(d_action, FakeDevice(power_state=True, ieco=True))
        self.assertTrue(ok)
        # Am handelnden Objekt geprueft, nicht am Verifikationsgeraet:
        self.assertIs(d_action.power_state, True)
        self.assertIs(d_action.ieco, True)
        self.assertEqual(d_action.apply_calls, 1)

    def test_running_device_gets_ieco_without_touching_power(self):
        d_action = FakeDevice(power_state=True, ieco=False)
        ok, _ = self._run(d_action, FakeDevice(power_state=True, ieco=True))
        self.assertTrue(ok)
        self.assertIs(d_action.ieco, True)
        self.assertIs(d_action.power_state, True)

    def test_device_denying_ieco_at_verification_is_a_failure(self):
        # Meldet das Geraet nach dem Schreiben weiterhin ieco=False, ist der Lauf
        # fehlgeschlagen - auch wenn alles davor geklappt hat. Ohne diesen Test
        # liess sich die abschliessende Pruefung ersatzlos entfernen.
        ok, out = self._run(FakeDevice(power_state=True, ieco=False),
                            FakeDevice(power_state=True, ieco=False))
        self.assertFalse(ok)
        self.assertIn("still disabled", out)

    def test_a_switched_off_device_with_a_stale_ieco_flag_is_still_powered_on(self):
        # Der Kurzschluss lautet 'is_on AND ieco'. Faellt die is_on-Bedingung
        # weg, gilt ein AUSGESCHALTETES Geraet, das den iECO-Schalter noch
        # gesetzt meldet, als 'bereits im gewuenschten Zustand' - es wird nie
        # eingeschaltet, und der Lauf meldet trotzdem Erfolg.
        d_action = FakeDevice(power_state=False, ieco=True)
        ok, out = self._run(d_action, FakeDevice(power_state=True, ieco=True))
        self.assertTrue(ok)
        self.assertIs(d_action.power_state, True)
        self.assertEqual(d_action.apply_calls, 1)
        self.assertNotIn("Already in the desired state", out)

    def test_only_if_on_never_powers_on_a_switched_off_device(self):
        # Die Kernzusage des Flags, am Objekt belegt statt am Rueckgabewert.
        d_action = FakeDevice(power_state=False, ieco=False)
        ok, _ = self._run(d_action, FakeDevice(), only_if_on=True)
        self.assertTrue(ok)
        self.assertIs(d_action.power_state, False)
        self.assertEqual(d_action.apply_calls, 0)


class GermanOutputTests(unittest.TestCase):
    """Gegenprobe zum modulweiten Englisch-Pinning: dieselben Pfade auf Deutsch.

    Zweck ist nicht, jede Formulierung zu wiederholen, sondern zu belegen, dass
    die Sprache tatsaechlich durchschlaegt - und zwar in beiden Richtungen. Ohne
    diesen Test koennte der deutsche Katalogzweig unbemerkt kaputtgehen, weil
    alle uebrigen Tests nur die englische Fassung sehen."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "de"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mie.CONFIG_PATH
        mie.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mie, "CONFIG_PATH", orig))

    def test_overview_is_german(self):
        self.path.write_text('{"devices": []}', encoding="utf-8")
        with mock.patch.object(mie.sys, "argv", ["midea-ieco", "list"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Beispiele:", out.getvalue())
        self.assertNotIn("Examples:", out.getvalue())

    def test_mode_guard_message_is_german(self):
        d_action = FakeDevice(power_state=True, ieco=False)
        d_action.operational_mode = _OpMode.FAN_ONLY
        connect = _scripted_connect([d_action, FakeDevice(power_state=True, ieco=True)])
        with ExitStack() as es:
            es.enter_context(mock.patch.object(mie, "connect_and_refresh", connect))
            es.enter_context(mock.patch.object(mie.asyncio, "sleep", _anoop))
            out = es.enter_context(redirect_stdout(io.StringIO()))
            asyncio.run(mie.ensure_ieco({"name": "X", "ip": "1", "id": "1"},
                                        only_if_on=False))
        text = out.getvalue()
        self.assertIn("unterstuetzt kein iECO", text)
        # Die Modusnamen selbst bleiben unuebersetzt - nur das Bindewort wechselt.
        self.assertIn("COOL oder HEAT", text)
        self.assertIn("FAN_ONLY (5)", text)

    def test_config_problem_reason_is_german(self):
        self.assertIn("numerisch", mie._device_config_problem(
            {"name": "W", "ip": "1.2.3.4", "id": "abc"}))

    def test_empty_all_message_is_german(self):
        self.path.write_text('{"devices": []}', encoding="utf-8")
        with mock.patch.object(mie.sys, "argv", ["midea-ieco", "all"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(mie.main())
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Keine Geraete", out.getvalue())


class CatalogTests(unittest.TestCase):
    """Der Katalog muss vollstaendig und in beiden Sprachen strukturgleich sein -
    sonst faellt eine Luecke erst beim Nutzer auf (analog test_refresh_tokens.py)."""

    def _used_keys(self):
        src = (REPO_DIR / "midea_ieco_ensure.py").read_text(encoding="utf-8")
        return set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', src))

    def test_every_used_key_exists(self):
        missing = self._used_keys() - set(mie._MESSAGES)
        self.assertEqual(missing, set(), f"Verwendet, aber nicht im Katalog: {missing}")

    def test_no_orphan_keys(self):
        orphans = set(mie._MESSAGES) - self._used_keys()
        self.assertEqual(orphans, set(), f"Im Katalog, aber unbenutzt: {orphans}")

    def test_placeholder_count_matches_between_languages(self):
        # Ungleiche %s-Zahl waere ein TypeError zur Laufzeit - ausgerechnet im
        # Fehlerpfad, in dem man ihn am wenigsten gebrauchen kann.
        for key, (english, german) in mie._MESSAGES.items():
            self.assertEqual(english.count("%s"), german.count("%s"), key)

    def test_both_languages_non_empty(self):
        for key, (english, german) in mie._MESSAGES.items():
            self.assertTrue(english.strip(), key)
            self.assertTrue(german.strip(), key)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            mie.t("does_not_exist")


if __name__ == "__main__":
    unittest.main()
