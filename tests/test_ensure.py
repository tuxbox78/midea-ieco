#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_ieco_ensure.py (stdlib unittest, Fake-msmart, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_ensure  (aus dem Repo-Root)
oder direkt: python3 tests/test_ensure.py
"""
import asyncio
import io
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

    async def get_capabilities(self):
        self.caps_calls += 1
        if self._caps_raises is not None:
            raise self._caps_raises

    async def apply(self):
        self.apply_calls += 1
        if self._apply_raises is not None:
            raise self._apply_raises

    async def close(self):
        pass


def _scripted_connect(items):
    """Async-Ersatz fuer connect_and_refresh: gibt der Reihe nach die
    uebergebenen FakeDevices zurueck; ist ein Eintrag eine Exception, wird sie
    geworfen (simuliert einen fehlgeschlagenen Reconnect)."""
    seq = list(items)

    async def _connect(dev_conf, retries=mie.CONNECT_RETRIES):
        item = seq.pop(0)
        if isinstance(item, BaseException):
            raise item
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


if __name__ == "__main__":
    unittest.main()
