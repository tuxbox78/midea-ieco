#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_refresh_tokens.py (stdlib unittest, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_refresh_tokens  (aus dem Repo-Root)
oder direkt: python3 tests/test_refresh_tokens.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

import midea_refresh_tokens as mrt  # noqa: E402


class _ConfigPathMixin(unittest.TestCase):
    """Legt ein temporaeres Verzeichnis an und pinnt mrt.CONFIG_PATH darauf."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "devices.json"
        orig = mrt.CONFIG_PATH
        mrt.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(mrt, "CONFIG_PATH", orig))


class LoadConfigTests(_ConfigPathMixin):
    def test_missing_returns_empty(self):
        self.assertEqual(mrt.load_config(), {"devices": []})

    def test_valid_config(self):
        self.path.write_text('{"devices": [{"name": "X"}]}', encoding="utf-8")
        self.assertEqual(mrt.load_config()["devices"][0]["name"], "X")

    def test_malformed_json_exits_1(self):
        self.path.write_text("{ not valid json", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            mrt.load_config()
        self.assertEqual(cm.exception.code, 1)

    def test_toplevel_list_exits_1(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            mrt.load_config()
        self.assertEqual(cm.exception.code, 1)

    def test_devices_not_a_list_exits_1(self):
        self.path.write_text('{"devices": {}}', encoding="utf-8")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            mrt.load_config()

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root umgeht Dateirechte")
    def test_unreadable_exits_1(self):
        self.path.write_text('{"devices": []}', encoding="utf-8")
        self.path.chmod(0)
        self.addCleanup(lambda: self.path.chmod(0o600))
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            mrt.load_config()


class MsmartMissingProbeTests(unittest.TestCase):
    """#12: fehlt msmart, bricht main() klar ab BEVOR ein Cloud-Kontakt passiert."""

    def test_probe_exits_before_cloud_contact(self):
        probe = subprocess.run([sys.executable, "-c", "import msmart"],
                               capture_output=True)
        if probe.returncode == 0:
            self.skipTest("msmart ist installiert - Negativpfad nicht pruefbar")
        work = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_refresh_tokens.py"),
             "--all", "--username", "x@example.com", "--password", "secret"],
            capture_output=True, text=True, cwd=work)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("msmart-ng", result.stderr)
        self.assertNotIn("Hole Token", result.stdout + result.stderr)


class SaveConfigTests(_ConfigPathMixin):
    """#3: atomarer, fensterfreier 0600-Write; Original bleibt bei Fehler intakt."""

    def test_success_writes_0600_and_content(self):
        mrt.save_config({"devices": [{"name": "Wohnzimmer", "token": "abc"}]})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text)["devices"][0]["name"], "Wohnzimmer")

    def test_crash_leaves_original_intact_and_no_tmp(self):
        self.path.write_text('{"devices": [{"name": "OLD"}]}\n', encoding="utf-8")
        self.path.chmod(0o600)
        before = self.path.read_bytes()
        with mock.patch("midea_refresh_tokens.json.dump",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                mrt.save_config({"devices": [{"name": "NEW"}]})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.tmp.name).glob(".devices.json.*")), [])

    def test_preexisting_0644_becomes_0600_atomic(self):
        self.path.write_text('{"devices": []}\n', encoding="utf-8")
        self.path.chmod(0o644)
        ino_before = self.path.stat().st_ino
        mrt.save_config({"devices": [{"name": "X"}]})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.path.stat().st_ino, ino_before)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root umgeht Verzeichnisrechte")
    def test_readonly_dir_raises_oserror(self):
        ro = Path(self.tmp.name) / "ro"
        ro.mkdir()
        mrt.CONFIG_PATH = ro / "devices.json"
        ro.chmod(0o555)
        self.addCleanup(lambda: ro.chmod(0o755))
        with self.assertRaises(OSError):
            mrt.save_config({"devices": []})


class TokenExtractionTests(unittest.TestCase):
    """#11: alle (key, token)-Paare, beliebige Feldreihenfolge, dedupliziert."""

    def test_single_entry(self):
        text = '"tokenlist": [{"udpId": "1", "key": "aabb", "token": "ccdd"}]'
        self.assertEqual(mrt.extract_token_key_pairs(text), [("aabb", "ccdd")])

    def test_two_entries_one_list_in_order(self):
        text = ('"tokenlist": [{"key": "aa", "token": "bb"}, '
                '{"key": "cc", "token": "dd"}]')
        self.assertEqual(mrt.extract_token_key_pairs(text),
                         [("aa", "bb"), ("cc", "dd")])

    def test_two_separate_lists(self):
        text = ('x "tokenlist": [{"key":"11","token":"22"}] y '
                '"tokenlist": [{"key":"33","token":"44"}] z')
        self.assertEqual(mrt.extract_token_key_pairs(text),
                         [("11", "22"), ("33", "44")])

    def test_swapped_field_order(self):
        # token VOR key - die alte Regex haette das komplett verpasst.
        text = '"tokenlist": [{"token": "bb", "udpId": "x", "key": "aa"}]'
        self.assertEqual(mrt.extract_token_key_pairs(text), [("aa", "bb")])

    def test_uppercase_hex(self):
        text = '"tokenlist": [{"key": "ABCDEF", "token": "012ABC"}]'
        self.assertEqual(mrt.extract_token_key_pairs(text), [("ABCDEF", "012ABC")])

    def test_no_tokenlist_returns_empty(self):
        self.assertEqual(mrt.extract_token_key_pairs("nothing here"), [])

    def test_deduplicates_order_preserving(self):
        text = ('"tokenlist": [{"key":"aa","token":"bb"},'
                '{"key":"aa","token":"bb"},{"key":"cc","token":"dd"}]')
        self.assertEqual(mrt.extract_token_key_pairs(text),
                         [("aa", "bb"), ("cc", "dd")])

    def test_old_format_still_matched_superset(self):
        # Realistischer, einzeiliger bytes-repr wie im Doc-Kommentar.
        text = ('response: b\'{"result": {"tokenlist": [{"udpId": "9", '
                '"key": "deadbeef", "token": "cafe1234"}]}}\'')
        self.assertIn(("deadbeef", "cafe1234"), mrt.extract_token_key_pairs(text))


class FetchDiscoverTests(unittest.TestCase):
    """#5a / C4: Passwort ueber 0600-midea-local.json in einem PRO AUFRUF
    isolierten Temp-Verzeichnis statt argv; Fallback; Verzeichnis-Isolierung."""

    TL = '{"tokenlist": [{"key": "aa", "token": "bb"}]}'

    @staticmethod
    def _ns(rc=0, out="", err=""):
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    def test_config_route_keeps_password_out_of_argv(self):
        seen = {}

        def fake_run(cmd, **kw):
            # Waehrend des Aufrufs liegt eine 0600-midea-local.json mit den
            # Zugangsdaten im (temporaeren) CWD - NICHT auf der Kommandozeile.
            cwd = kw["cwd"]
            cfg = Path(cwd) / "midea-local.json"
            self.assertTrue(cfg.exists())
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")),
                             {"username": "u@e", "password": "secret"})
            seen["cmd"], seen["cwd"] = cmd, cwd
            return self._ns(out=self.TL)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run), \
                redirect_stderr(io.StringIO()):
            matches, _ = mrt.fetch_candidate_credentials("u@e", "secret", "192.168.0.5")

        self.assertEqual(matches, [("aa", "bb")])
        self.assertNotIn("--password", seen["cmd"])
        self.assertNotIn("secret", seen["cmd"])
        self.assertFalse(Path(seen["cwd"]).exists())  # Temp-Verzeichnis danach weg

    def test_two_calls_use_distinct_isolated_dirs(self):
        # Isolationsbeleg gegen ein Wettrennen zweier gleichzeitiger Laeufe:
        # jeder Aufruf bekommt ein EIGENES Verzeichnis, beide werden entfernt.
        cwds = []

        def fake_run(cmd, **kw):
            cwds.append(kw["cwd"])
            return self._ns(out=self.TL)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run), \
                redirect_stderr(io.StringIO()):
            mrt.fetch_candidate_credentials("u@e", "secret", "192.168.0.5")
            mrt.fetch_candidate_credentials("u@e", "secret", "192.168.0.6")

        self.assertEqual(len(cwds), 2)
        self.assertNotEqual(cwds[0], cwds[1])
        for cwd in cwds:
            self.assertFalse(Path(cwd).exists())

    def test_fallback_to_argv_when_config_route_empty(self):
        outputs = [self._ns(rc=0, out="no tokens here"), self._ns(rc=0, out=self.TL)]
        cmds = []
        cwds = []

        def fake_run(cmd, **kw):
            cmds.append(cmd)
            cwds.append(kw.get("cwd"))
            return outputs[len(cmds) - 1]

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run), \
                redirect_stdout(io.StringIO()):
            matches, _ = mrt.fetch_candidate_credentials("u@e", "secret", "192.168.0.5")

        self.assertEqual(matches, [("aa", "bb")])
        self.assertEqual(len(cmds), 2)
        self.assertNotIn("--password", cmds[0])       # Primaerweg ohne Passwort
        self.assertIn("--password", cmds[1])          # Fallback mit Passwort
        self.assertIn("secret", cmds[1])
        # Primaerweg lief in einem eigenen Temp-CWD (danach entfernt); der
        # argv-Fallback laeuft ohne gesetztes cwd.
        self.assertIsNotNone(cwds[0])
        self.assertIsNone(cwds[1])
        self.assertFalse(Path(cwds[0]).exists())

    def test_exec_error_does_not_fall_back(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            raise subprocess.TimeoutExpired(cmd, mrt.SUBPROCESS_TIMEOUT)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                mrt.fetch_candidate_credentials("u@e", "secret", "1.2.3.4")
        self.assertEqual(len(calls), 1)  # Ausfuehrungsfehler -> kein Fallback


class _CredentialsPathMixin(unittest.TestCase):
    """Legt ein temporaeres Verzeichnis an und pinnt mrt.CREDENTIALS_PATH darauf."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "credentials.json"
        orig = mrt.CREDENTIALS_PATH
        mrt.CREDENTIALS_PATH = self.path
        self.addCleanup(lambda: setattr(mrt, "CREDENTIALS_PATH", orig))


def _fake_stdin(is_tty):
    return SimpleNamespace(isatty=lambda: is_tty)


class CleanCredentialValueTests(unittest.TestCase):
    """#18: gemeinsamer Platzhalter-Filter fuer Datei- UND CLI-Werte."""

    def test_valid_value_passes_through(self):
        self.assertEqual(mrt._clean_credential_value("real@value.example"), "real@value.example")

    def test_every_known_placeholder_rejected(self):
        for placeholder in mrt.PLACEHOLDER_VALUES:
            self.assertIsNone(mrt._clean_credential_value(placeholder))

    def test_non_string_rejected(self):
        self.assertIsNone(mrt._clean_credential_value(None))
        self.assertIsNone(mrt._clean_credential_value(123))


class ResolveCredentialsTests(_CredentialsPathMixin):
    """#18: CLI-Placeholder werden wie Datei-Placeholder behandelt; EOF sauber gemeldet."""

    def test_valid_cli_args_used_directly(self):
        with redirect_stdout(io.StringIO()):
            username, password = mrt.resolve_credentials("u@e.example", "realpw")
        self.assertEqual((username, password), ("u@e.example", "realpw"))

    def test_cli_username_placeholder_falls_back_to_file(self):
        self.path.write_text(
            json.dumps({"username": "file@e.example", "password": "filepw"}),
            encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            username, password = mrt.resolve_credentials("dein@account.example", None)
        self.assertEqual((username, password), ("file@e.example", "filepw"))

    def test_cli_password_placeholder_falls_back_to_file(self):
        self.path.write_text(
            json.dumps({"username": "file@e.example", "password": "filepw"}),
            encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            username, password = mrt.resolve_credentials("cli@e.example", "HIER_PASSWORT_EINTRAGEN")
        self.assertEqual(username, "cli@e.example")
        self.assertEqual(password, "filepw")

    def test_no_tty_and_no_credentials_exits_1_without_prompting(self):
        with mock.patch.object(mrt.sys, "stdin", _fake_stdin(False)), \
                mock.patch("builtins.input", side_effect=AssertionError("darf nicht prompten")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.resolve_credentials(None, None)
        self.assertEqual(cm.exception.code, 1)

    def test_eof_on_username_prompt_exits_1_with_message(self):
        with mock.patch.object(mrt.sys, "stdin", _fake_stdin(True)), \
                mock.patch("builtins.input", side_effect=EOFError), \
                redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                mrt.resolve_credentials(None, None)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("EOF", err.getvalue())

    def test_eof_on_password_prompt_exits_1_with_message(self):
        with mock.patch.object(mrt.sys, "stdin", _fake_stdin(True)), \
                mock.patch("builtins.input", return_value="u@e.example"), \
                mock.patch("midea_refresh_tokens.getpass.getpass", side_effect=EOFError), \
                redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                mrt.resolve_credentials(None, None)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("EOF", err.getvalue())

    def test_keyboard_interrupt_not_swallowed(self):
        # Strg+C soll NICHT wie EOF behandelt werden - bewusst ungefangen.
        with mock.patch.object(mrt.sys, "stdin", _fake_stdin(True)), \
                mock.patch("builtins.input", side_effect=KeyboardInterrupt), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                mrt.resolve_credentials(None, None)


if __name__ == "__main__":
    unittest.main()
