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

    def test_invalid_utf8_exits_1(self):
        # Nicht-UTF-8 (Latin-1-Umlaut, Byte 0xFC) -> UnicodeDecodeError: ein
        # ValueError, aber KEIN JSONDecodeError. Muss sauber Exit 1 liefern.
        self.path.write_bytes(b'{"devices": [{"name": "K\xfcche"}]}')
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
            [sys.executable, str(REPO_DIR / "midea_refresh_tokens.py"), "--all"],
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


class DiscoverInvocationTests(unittest.TestCase):
    """0.2.0: discover laeuft OHNE Zugangsdaten. Belegt: (a) exakte, credential-
    freie argv; (b) die leere {}-Isolations-Konfig (0600) im pro-Aufruf-Temp-CWD;
    (c) das Aufraeumen des Temp-Verzeichnisses in Erfolg UND Fehler; (d) genau
    EIN discover-Aufruf (kein argv-Fallback mehr); (e) Fehlerklassen -> RuntimeError
    inkl. der neuen Guards fuer nicht anlegbares Temp-Verzeichnis / Konfig."""

    TL = '{"tokenlist": [{"key": "aa", "token": "bb"}]}'

    @staticmethod
    def _ns(rc=0, out="", err=""):
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    def test_argv_is_exact_and_credential_free(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return self._ns(out=self.TL)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            matches, _ = mrt.fetch_candidate_credentials("192.168.0.5")

        self.assertEqual(matches, [("aa", "bb")])
        self.assertEqual(seen["cmd"], [
            sys.executable, "-m", "midealocal.cli", "discover",
            "--host", "192.168.0.5", "--debug"])
        # Keinerlei Zugangsdaten-Flags in der Prozess-argv.
        self.assertNotIn("--username", seen["cmd"])
        self.assertNotIn("--password", seen["cmd"])

    def test_isolation_guard_is_empty_config_0600_in_cwd(self):
        seen = {}

        def fake_run(cmd, **kw):
            # Determinismus-Guard: eine LEERE {}-Config (0600) liegt im
            # temporaeren CWD, damit die CLI keine nutzer-globale Konfig zieht.
            cwd = kw["cwd"]
            cfg = Path(cwd) / "midea-local.json"
            self.assertTrue(cfg.exists())
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")), {})
            seen["cwd"] = cwd
            return self._ns(out=self.TL)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            mrt.fetch_candidate_credentials("192.168.0.5")

        self.assertFalse(Path(seen["cwd"]).exists())  # danach entfernt

    def test_two_calls_use_distinct_isolated_dirs(self):
        # Isolationsbeleg gegen ein Wettrennen zweier gleichzeitiger Laeufe:
        # jeder Aufruf bekommt ein EIGENES Verzeichnis, beide werden entfernt.
        cwds = []

        def fake_run(cmd, **kw):
            cwds.append(kw["cwd"])
            return self._ns(out=self.TL)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            mrt.fetch_candidate_credentials("192.168.0.5")
            mrt.fetch_candidate_credentials("192.168.0.6")

        self.assertEqual(len(cwds), 2)
        self.assertNotEqual(cwds[0], cwds[1])
        for cwd in cwds:
            self.assertFalse(Path(cwd).exists())

    def test_tempdir_removed_on_error(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cwd"] = kw["cwd"]
            raise subprocess.TimeoutExpired(cmd, mrt.SUBPROCESS_TIMEOUT)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                mrt.fetch_candidate_credentials("192.168.0.5")
        self.assertFalse(Path(seen["cwd"]).exists())  # auch im Fehlerfall weg

    def test_no_tokenlist_raises_and_runs_once(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return self._ns(rc=0, out="no tokens here")

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                mrt.fetch_candidate_credentials("192.168.0.5")
        self.assertEqual(len(calls), 1)  # kein zweiter (argv-)Aufruf mehr

    def test_nonzero_exit_raises(self):
        with mock.patch("midea_refresh_tokens.subprocess.run",
                        side_effect=lambda cmd, **kw: self._ns(rc=2, err="boom")):
            with self.assertRaises(RuntimeError):
                mrt.fetch_candidate_credentials("192.168.0.5")

    def test_timeout_becomes_runtimeerror(self):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, mrt.SUBPROCESS_TIMEOUT)

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "nicht reagiert"):
                mrt.fetch_candidate_credentials("1.2.3.4")

    def test_midealocal_missing_becomes_runtimeerror(self):
        with mock.patch("midea_refresh_tokens.subprocess.run",
                        side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(RuntimeError, "nicht installiert"):
                mrt.fetch_candidate_credentials("1.2.3.4")

    def test_generic_oserror_becomes_runtimeerror(self):
        # Ein sonstiger Subprozess-Startfehler (OSError-Unterklasse, aber KEIN
        # FileNotFoundError) darf nicht als roher Traceback durchschlagen: er wird
        # als RuntimeError gewrappt (update_device faengt nur RuntimeError).
        # PermissionError ist eine OSError-Unterklasse - und belegt zugleich, dass
        # die reihenfolge-sensible FileNotFoundError-Klausel NICHT faelschlich greift.
        with mock.patch("midea_refresh_tokens.subprocess.run",
                        side_effect=PermissionError("exec denied")):
            with self.assertRaisesRegex(RuntimeError, "nicht gestartet werden"):
                mrt.fetch_candidate_credentials("1.2.3.4")

    def test_mkdtemp_failure_becomes_runtimeerror(self):
        # Temp-Verzeichnis nicht anlegbar (z.B. voller Datentraeger) -> klarer
        # RuntimeError statt rohem OSError-Traceback.
        with mock.patch("midea_refresh_tokens.tempfile.mkdtemp",
                        side_effect=OSError("no space")):
            with self.assertRaisesRegex(RuntimeError, "Arbeitsverzeichnis"):
                mrt.fetch_candidate_credentials("1.2.3.4")

    def test_config_write_failure_becomes_runtimeerror_and_cleans_up(self):
        # mkdtemp real (Verzeichnis entsteht wirklich), aber der {}-Write
        # scheitert -> sauberer RuntimeError UND das Temp-Verzeichnis wird
        # dennoch entfernt (finally). real_mkdtemp VOR dem Patch binden, sonst
        # riefe der Spy sich selbst rekursiv auf.
        created = {}
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(*a, **k):
            created["dir"] = real_mkdtemp(*a, **k)
            return created["dir"]

        with mock.patch("midea_refresh_tokens.tempfile.mkdtemp", side_effect=spy_mkdtemp), \
                mock.patch("midea_refresh_tokens._atomic_write_json",
                           side_effect=OSError("nope")):
            with self.assertRaisesRegex(RuntimeError, "Isolations-Konfig"):
                mrt.fetch_candidate_credentials("1.2.3.4")
        self.assertFalse(Path(created["dir"]).exists())


class CredentialFreeMainTests(_ConfigPathMixin):
    """0.2.0: main() fragt NIE nach Zugangsdaten (kein Prompt) und die frueheren
    Flags --username/--password existieren nicht mehr (argparse lehnt sie ab)."""

    def test_main_never_prompts_and_processes(self):
        self.path.write_text(
            json.dumps({"devices": [{"name": "W", "ip": "1.2.3.4", "id": 1}]}),
            encoding="utf-8")
        processed = []

        def _fake_update(dev):
            processed.append(dev)
            return True

        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch("builtins.input",
                           side_effect=AssertionError("main() darf nicht prompten")), \
                mock.patch.object(mrt, "update_device", _fake_update), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(len(processed), 1)
        self.assertNotIn("Zugangsdaten", out.getvalue())

    def test_removed_flags_are_rejected(self):
        # --username/--password gibt es nicht mehr: argparse lehnt sie laut mit
        # Exit 2 ab (statt sie still zu ignorieren). parse_args scheitert VOR der
        # msmart-Pruefung, daher unabhaengig davon, ob msmart installiert ist.
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_refresh_tokens.py"),
             "--all", "--username", "x@e.example"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", (result.stdout + result.stderr).lower())


class MalformedEntryTargetTests(_ConfigPathMixin):
    """main() ueberspringt Nicht-Objekt-Eintraege in devices.json mit Warnung,
    statt mit AttributeError (d.get auf einem Nicht-Objekt) abzubrechen -
    gueltige Geraete werden normal verarbeitet."""

    def _run(self, argv):
        processed = []

        def _fake_update(dev):
            processed.append(dev)
            return True

        out = io.StringIO()
        # msmart stubben, damit die Verfuegbarkeitspruefung in main() passiert;
        # update_device/save_config mocken, damit weder Hardware noch ein
        # Dateischreibzugriff noetig ist.
        with mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", _fake_update), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.sys, "argv", ["x"] + argv), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        return cm.exception.code, out.getvalue(), processed

    def test_all_skips_nondict_and_processes_valid(self):
        self.path.write_text(
            json.dumps({"devices": ["oops", 123, {"name": "W", "ip": "1.2.3.4", "id": 1}]}),
            encoding="utf-8")
        code, out, processed = self._run(["--all"])
        self.assertEqual(code, 0)
        self.assertIn("WARNUNG", out)
        self.assertEqual(len(processed), 1)

    def test_named_skips_nondict_sibling(self):
        # Ohne den Guard wuerde d.get("name") auf "oops" hier mit AttributeError
        # abbrechen (Nicht-Objekt hat kein .get).
        self.path.write_text(
            json.dumps({"devices": ["oops", {"name": "W", "ip": "1.2.3.4", "id": 1}]}),
            encoding="utf-8")
        code, out, processed = self._run(["--name", "W"])
        self.assertEqual(code, 0)
        self.assertEqual(len(processed), 1)


if __name__ == "__main__":
    unittest.main()
