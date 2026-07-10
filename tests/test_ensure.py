#!/usr/bin/env python3
"""Unit-Tests fuer midea_ieco_ensure.py (stdlib unittest, Fake-msmart, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_ensure  (aus dem Repo-Root)
oder direkt: python3 tests/test_ensure.py
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
