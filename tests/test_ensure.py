#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_ieco_ensure.py (stdlib unittest, Fake-msmart, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_ensure  (aus dem Repo-Root)
oder direkt: python3 tests/test_ensure.py
"""
import asyncio
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stub_msmart  # noqa: E402,F401  (registriert Fake-msmart VOR dem Import)
import midea_ieco_ensure as mie  # noqa: E402


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


class FakeDevice:
    """Konfigurierbares Fake-AC-Objekt fuer die Retry-Matrix (#13/#14)."""

    def __init__(self, *, online=True, power_state=False, ieco=False,
                 supports_ieco=True, apply_raises=None, caps_raises=None):
        self.online = online
        self.power_state = power_state
        self.ieco = ieco
        self.eco = False
        self.operational_mode = 1
        self.supports_ieco = supports_ieco
        self._apply_raises = apply_raises
        self._caps_raises = caps_raises
        self.apply_calls = 0
        self.caps_calls = 0
        self.refresh_calls = 0

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

    async def close(self):
        pass


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
        self.assertIn("Keine Geraete", out.getvalue())

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
        self.assertNotIn("Keine Geraete", out.getvalue())


class CapabilityGatedDevice:
    """Modelliert das reale msmart-ng-Verhalten: device.ieco liefert den WAHREN
    Wert erst, nachdem get_capabilities() (fuellt _supported_properties) UND
    danach refresh() (pollt dann IECO) liefen - vorher immer den Default False.
    Genau das liess die Verifikation frueher faelschlich fehlschlagen."""

    def __init__(self, *, power_state=True, true_ieco=True, supports_ieco=True):
        self.online = True
        self.power_state = power_state
        self.operational_mode = 1
        self.eco = False
        self.supports_ieco = supports_ieco
        self._true_ieco = true_ieco
        self._caps = False
        self._refreshed_after_caps = False
        self.apply_calls = 0

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

    async def close(self):
        pass


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

    def test_already_on_and_ieco_short_circuits(self):
        # Geraet an und bereits in iECO: der 'schon aktiv'-Kurzschluss greift nur,
        # weil auch der Initial-Read jetzt Capabilities+refresh macht (sonst laese
        # ieco False und es wuerde unnoetig apply() aufgerufen).
        d = CapabilityGatedDevice(power_state=True, true_ieco=True)
        self.assertTrue(self._run([d]))
        self.assertEqual(d.apply_calls, 0)


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
        self.assertIn("Beispiele:", out)
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
        self.assertIn("WARNUNG", out)

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
        self.assertIn("uebersprungen", out)

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
        self.assertIn("Hinweis", out)


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
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_ieco_ensure.py"), "list"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Beispiele:", result.stdout)


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
        self.assertIn("WARNUNG", out)
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
        self.assertIn("WARNUNG", out)
        self.assertEqual(len(self.processed), 0)


if __name__ == "__main__":
    unittest.main()
