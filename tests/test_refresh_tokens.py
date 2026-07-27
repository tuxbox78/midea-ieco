#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_refresh_tokens.py (stdlib unittest, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_refresh_tokens  (aus dem Repo-Root)
oder direkt: python3 tests/test_refresh_tokens.py
"""
import ast
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stub_msmart  # noqa: E402,F401  (Fake-msmart VOR midea_ieco_ensure registrieren)
import midea_ieco_ensure as mie  # noqa: E402  (nur fuer die Katalog-Aritaetspruefung)
import midea_refresh_tokens as mrt  # noqa: E402


# Ausgabesprache fuer das GESAMTE Modul auf Englisch pinnen (den Default).
# Ohne dieses Pinning haengen alle Textzusicherungen an der Locale des
# Ausfuehrenden: auf einem deutschen Entwicklerrechner waeren sie gruen, im
# (locale-losen) CI rot. Die deutschen Gegenproben ueberschreiben das gezielt
# per eigenem patch.dict in ihrem setUp.
_LANG_PATCHER = None


def setUpModule():
    global _LANG_PATCHER
    _LANG_PATCHER = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"})
    _LANG_PATCHER.start()


def tearDownModule():
    if _LANG_PATCHER is not None:
        _LANG_PATCHER.stop()


def _longest_literal(template: str) -> str:
    """Laengstes festes Textstueck einer Katalog-Vorlage (ohne %s-Platzhalter).

    Damit laesst sich eine Meldung wiedererkennen, ohne ihren Wortlaut im Test
    zu duplizieren: aendert jemand die Formulierung im Katalog, wandert die
    Zusicherung automatisch mit, statt still auf einen nicht mehr existierenden
    Text zu pruefen (genau so war die frueher hier fest verdrahtete deutsche
    Marke wirkungslos geworden)."""
    return max(template.split("%s"), key=len).strip(" []().:")


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
        # Sprache explizit pinnen und BEIDE Sprachfassungen des Markers pruefen.
        # Zuvor stand hier nur der deutsche Wortlaut ("Hole Token"), waehrend der
        # Unterprozess nach der i18n-Umstellung englisch lief - die Zusicherung
        # konnte damit nicht mehr fehlschlagen und haette einen Cloud-Kontakt vor
        # der msmart-Pruefung nicht mehr bemerkt.
        env = {**os.environ, "MIDEA_IECO_LANG": "en"}
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_refresh_tokens.py"), "--all"],
            capture_output=True, text=True, cwd=work, env=env)
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("msmart-ng", result.stderr)
        for template in mrt._MESSAGES["dev_fetching"]:
            marker = _longest_literal(template)
            # Selbstschutz: eine leere oder zu kurze Marke waere eine Zusicherung,
            # die nicht fehlschlagen KANN - lieber hier auffallen als still passieren.
            self.assertGreater(len(marker), 10, template)
            self.assertNotIn(marker, combined)


def _run_script_without_msmart(testcase, script, argv):
    """Faehrt eines der Werkzeuge in einem Unterprozess OHNE msmart.

    Warum nicht einfach die Umgebung nutzen: MsmartMissingProbeTests (hier) und
    OverviewWithoutMsmartTests (test_ensure.py) ueberspringen sich selbst,
    sobald msmart importierbar ist - auf jedem Entwicklerrechner mit
    installierter Bibliothek sind diese Pfade damit unbewacht, und im CI haengt
    ihre Ausfuehrung an einer Zufaelligkeit der Umgebung. 'sys.modules[...] =
    None' laesst 'import msmart' zuverlaessig mit ImportError scheitern, also
    laeuft der Pfad ueberall.

    Die Skripte werden in ein leeres Verzeichnis kopiert: CONFIG_PATH haengt am
    Modulverzeichnis, der Lauf sieht dort also garantiert keine echte
    devices.json."""
    work = tempfile.mkdtemp()
    testcase.addCleanup(lambda: shutil.rmtree(work, ignore_errors=True))
    for name in ("midea_ieco_ensure.py", "midea_refresh_tokens.py",
                 "midea_i18n.py"):
        shutil.copy(REPO_DIR / name, work)
    target = os.path.join(work, script)
    code = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {work!r})\n"
        "sys.modules['msmart'] = None\n"
        f"sys.argv = {argv!r}\n"
        f"runpy.run_path({target!r}, run_name='__main__')\n"
    )
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=work,
                          env={**os.environ, "MIDEA_IECO_LANG": "en"})


class RefreshWithoutMsmartTests(unittest.TestCase):
    """Ohne msmart bricht der Token-Abruf ab, BEVOR Cloud-Kontakt entsteht -
    ueberall pruefbar, nicht nur wo msmart zufaellig fehlt."""

    def test_refresh_exits_1_before_any_cloud_contact(self):
        result = _run_script_without_msmart(
            self, "midea_refresh_tokens.py", ["midea_refresh_tokens.py", "--all"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("msmart-ng", result.stderr)
        combined = result.stdout + result.stderr
        for template in mrt._MESSAGES["dev_fetching"]:
            marker = _longest_literal(template)
            self.assertGreater(len(marker), 10, template)
            self.assertNotIn(marker, combined)


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

    def _assert_message(self, cm, key):
        """Prueft, dass die RuntimeError-Meldung aus dem erwarteten Katalog-
        Eintrag stammt - ueber den Katalog statt ueber einen im Test kopierten
        Wortlaut. Diese Meldungen werden dem Nutzer ausgegeben (ueber
        'dev_fetch_failed'), waren aber lange deutsch fest verdrahtet; eine hier
        eingetippte Zeichenkette wuerde bei der naechsten Umformulierung still
        veralten, statt den Test rot zu machen."""
        marker = _longest_literal(mrt._MESSAGES[key][0])
        self.assertGreater(len(marker), 10, key)
        self.assertIn(marker, str(cm.exception))

    def _assert_rendered(self, cm, key, *args):
        """Wie _assert_message, aber gegen die VOLLSTAENDIG gerenderte Meldung.

        Der Marker oben ist das laengste Literal des Katalogtextes und liegt
        damit zwangslaeufig NEBEN den Platzhaltern - ein vertauschtes
        Wertepaar laesst ihn unberuehrt. Diese drei Meldungen fuehren je zwei
        Werte (Ausnahmetyp und -text); vertauscht lesen sie sich als
        'no space: OSError'. Die Werte kommen aus der im Test gesetzten
        Ausnahme, sind hier also bekannt."""
        self.assertIn(mrt.t(key, *args), str(cm.exception))

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
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("192.0.2.77")
        self._assert_message(cm, "err_discover_timeout")
        # Die AUFRUFSTELLE muss ihre beiden Werte in der richtigen Reihenfolge
        # uebergeben. Eine reine Aritaetspruefung sieht eine Vertauschung nicht:
        # sie ergaebe die syntaktisch einwandfreie, inhaltlich unsinnige Meldung
        # "did not respond within 192.0.2.77s (is the device at 60 reachable?)".
        # Deshalb wird hier die tatsaechlich erzeugte Meldung geprueft.
        message = str(cm.exception)
        self.assertIn(f"{mrt.SUBPROCESS_TIMEOUT}s", message)
        self.assertNotIn("192.0.2.77s", message)
        self.assertIn("192.0.2.77", message)

    def test_midealocal_missing_becomes_runtimeerror(self):
        with mock.patch("midea_refresh_tokens.subprocess.run",
                        side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_midealocal_missing")

    def test_generic_oserror_becomes_runtimeerror(self):
        # Ein sonstiger Subprozess-Startfehler (OSError-Unterklasse, aber KEIN
        # FileNotFoundError) darf nicht als roher Traceback durchschlagen: er wird
        # als RuntimeError gewrappt (update_device faengt nur RuntimeError).
        # PermissionError ist eine OSError-Unterklasse - und belegt zugleich, dass
        # die reihenfolge-sensible FileNotFoundError-Klausel NICHT faelschlich greift.
        with mock.patch("midea_refresh_tokens.subprocess.run",
                        side_effect=PermissionError("exec denied")):
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_discover_start")
        self._assert_rendered(cm, "err_discover_start", "PermissionError",
                              "exec denied")

    def test_mkdtemp_failure_becomes_runtimeerror(self):
        # Temp-Verzeichnis nicht anlegbar (z.B. voller Datentraeger) -> klarer
        # RuntimeError statt rohem OSError-Traceback.
        with mock.patch("midea_refresh_tokens.tempfile.mkdtemp",
                        side_effect=OSError("no space")):
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_tempdir")
        self._assert_rendered(cm, "err_tempdir", "OSError", "no space")

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
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_isolation_config")
        self._assert_rendered(cm, "err_isolation_config", "OSError", "nope")
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
        # Dateischreibzugriff noetig ist. Sprache pinnen, damit die Zusicherung
        # nicht an der Locale des Ausfuehrenden haengt.
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
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
        self.assertIn("WARNING", out)
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


class _LangMixin(unittest.TestCase):
    """Pinnt die Ausgabesprache fuer den Test. Ohne dieses Pinning haenge das
    Ergebnis an der Locale des Ausfuehrenden - der Test waere auf einem
    deutschen Entwicklerrechner gruen und im (englischen) CI rot."""

    LANG = "en"

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": self.LANG})
        patcher.start()
        self.addCleanup(patcher.stop)


class ResolveLangTests(unittest.TestCase):
    """#2: Ein englischsprachiger Nutzer bekam deutsche Fehlermeldungen. Die
    Sprachwahl spiegelt jetzt resolve_lang() aus install.sh:
    MIDEA_IECO_LANG > LC_ALL > LC_MESSAGES > LANG > 'en'."""

    def _resolve(self, **env):
        # clear=True: sonst wuerde eine echte Locale des Ausfuehrenden
        # durchschlagen und den Test vom Host abhaengig machen.
        with mock.patch.dict(os.environ, env, clear=True):
            return mrt.resolve_lang()

    def test_default_without_anything_is_english(self):
        self.assertEqual(self._resolve(), "en")

    def test_german_locale_yields_german(self):
        self.assertEqual(self._resolve(LANG="de_DE.UTF-8"), "de")

    def test_english_locale_yields_english(self):
        self.assertEqual(self._resolve(LANG="en_GB.UTF-8"), "en")

    def test_env_var_wins_over_locale(self):
        self.assertEqual(self._resolve(MIDEA_IECO_LANG="de", LANG="en_US.UTF-8"), "de")
        self.assertEqual(self._resolve(MIDEA_IECO_LANG="en", LANG="de_DE.UTF-8"), "en")

    def test_lc_all_wins_over_lang(self):
        self.assertEqual(self._resolve(LC_ALL="de_DE.UTF-8", LANG="en_US.UTF-8"), "de")

    def test_lc_messages_considered(self):
        self.assertEqual(self._resolve(LC_MESSAGES="de_DE.UTF-8"), "de")

    def test_spellings_and_case(self):
        for value in ("de", "DE", "German", "deutsch", "de-AT", "de_CH.UTF-8"):
            self.assertEqual(self._resolve(MIDEA_IECO_LANG=value), "de", value)

    def test_unknown_language_falls_back_to_english(self):
        # Kein Halbdeutsch fuer z.B. franzoesische Locales - klarer Rueckfall.
        for value in ("fr_FR.UTF-8", "C", "POSIX", "", "   "):
            self.assertEqual(self._resolve(LANG=value), "en", repr(value))

    def test_documented_divergences_from_install_sh(self):
        """Die vier im Modul dokumentierten Abweichungen von install.sh.

        Sie sind bewusst grosszuegiger (immer zugunsten von Deutsch, nie
        umgekehrt) - waren aber selbst ungetestet, obwohl sie ausdruecklich als
        Korrektur einer frueheren Falschbehauptung dokumentiert wurden."""
        # 'de.' als Praefix (install.sh kennt nur de_ und de-)
        self.assertEqual(self._resolve(LANG="de.UTF-8"), "de")
        # Werte werden getrimmt
        self.assertEqual(self._resolve(MIDEA_IECO_LANG=" de "), "de")
        # Eine leere/nur-Leerzeichen-Variable gilt als nicht gesetzt und faellt
        # auf die naechste Stufe durch
        self.assertEqual(self._resolve(MIDEA_IECO_LANG="   ",
                                       LANG="de_DE.UTF-8"), "de")
        self.assertEqual(self._resolve(LC_ALL="   ", LANG="de_DE.UTF-8"), "de")

    def test_divergences_never_go_the_other_way(self):
        # Die Richtungszusage: nie Englisch, wo install.sh Deutsch saehe.
        for env in ({"LANG": "de_DE.UTF-8"}, {"LC_ALL": "de_AT"},
                    {"MIDEA_IECO_LANG": "de"}, {"LC_MESSAGES": "de-CH"}):
            with self.subTest(env=env):
                self.assertEqual(self._resolve(**env), "de")

    def test_locales_that_are_not_german_stay_english(self):
        # 'da_DK' faengt nur den Fall ab, dass der Vergleich ganz entfaellt. Die
        # eigentliche Gefahr ist eine Aufweitung der Praefixliste auf ein blosses
        # 'de' am Wortanfang - dagegen braucht es einen Wert, der MIT 'de'
        # beginnt und trotzdem kein Deutsch ist. Genau das begruendet der
        # Modulkommentar von midea_i18n.py, ohne dass es geprueft war.
        for value in ("da_DK.UTF-8", "default", "dev_DEV", "des_ES"):
            with self.subTest(value=value):
                self.assertEqual(self._resolve(LANG=value), "en")


class CatalogTests(unittest.TestCase):
    """#2: Der Katalog muss vollstaendig und in beiden Sprachen strukturgleich
    sein - sonst faellt eine Luecke erst beim Nutzer auf."""

    def _source(self):
        return (REPO_DIR / "midea_refresh_tokens.py").read_text(encoding="utf-8")

    def _used_keys(self):
        src = self._source()
        keys = set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', src))
        keys |= set(re.findall(r'VERIFY_\w+, "([a-z0-9_]+)"', src))
        return keys

    def test_every_used_key_exists(self):
        missing = self._used_keys() - set(mrt._MESSAGES)
        self.assertEqual(missing, set(), f"Im Code verwendet, aber nicht im Katalog: {missing}")

    def test_no_orphan_keys(self):
        orphans = set(mrt._MESSAGES) - self._used_keys()
        self.assertEqual(orphans, set(), f"Im Katalog, aber unbenutzt: {orphans}")

    def test_placeholder_count_matches_between_languages(self):
        # Ungleiche %s-Zahl waere ein TypeError zur Laufzeit - genau in dem
        # Fehlerpfad, in dem man ihn am wenigsten gebrauchen kann.
        for key, (english, german) in mrt._MESSAGES.items():
            self.assertEqual(english.count("%s"), german.count("%s"), key)

    def test_both_languages_non_empty(self):
        for key, (english, german) in mrt._MESSAGES.items():
            self.assertTrue(english.strip(), key)
            self.assertTrue(german.strip(), key)

    def test_every_entry_is_actually_translated(self):
        """Beide Fassungen muessen sich unterscheiden - sonst ist ein Eintrag nur
        scheinbar uebersetzt.

        Der frueher hier stehende Test hiess '..._and_distinct_where_expected',
        pruefte aber ausschliesslich 'nicht leer': ein deutscher Eintrag durfte
        unbemerkt englischen Text enthalten. Ausgenommen sind nur Eintraege, die
        in beiden Sprachen bewusst gleich lauten."""
        # Begruendete Ausnahmen, nicht 'was gerade nicht passt'. Leer = keine.
        deliberately_identical: set[str] = set()
        for key, (english, german) in mrt._MESSAGES.items():
            if key in deliberately_identical:
                continue
            self.assertNotEqual(english, german,
                                f"Katalogeintrag '{key}' ist in beiden Sprachen gleich")

    def test_unknown_key_raises_instead_of_printing_nothing(self):
        with self.assertRaises(KeyError):
            mrt.t("does_not_exist")

    def test_every_call_site_passes_the_right_number_of_arguments(self):
        # Die Platzhalter-Paritaet oben vergleicht nur EN gegen DE. Stimmt die
        # Zahl dagegen nicht mit der AUFRUFSTELLE ueberein, gibt es einen
        # TypeError - und zwar ausgerechnet in einem selten durchlaufenen
        # Fehlerpfad, in dem er am meisten schadet. Deshalb hier statisch gegen
        # den Quelltext geprueft, fuer BEIDE Module.
        for module, path in ((mrt, REPO_DIR / "midea_refresh_tokens.py"),
                             (mie, REPO_DIR / "midea_ieco_ensure.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            checked = 0
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "t"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    key = node.args[0].value
                    # *args-Aufrufe koennen statisch nicht gezaehlt werden.
                    if any(isinstance(a, ast.Starred) for a in node.args):
                        continue
                    passed = len(node.args) - 1
                    expected = module._MESSAGES[key][0].count("%s")
                    self.assertEqual(
                        passed, expected,
                        f"{path.name}:{node.lineno} t({key!r}) uebergibt {passed} "
                        f"Argument(e), die Vorlage erwartet {expected}")
                    checked += 1
            # Selbstschutz: findet die Analyse nichts, prueft sie auch nichts.
            # Die Untergrenze haengt an der Kataloggroesse statt an einer festen
            # Zahl - so faellt es auf, wenn kuenftig ein grosser Teil der
            # Aufrufstellen nicht mehr erkannt wird (z.B. nach einer Umbenennung).
            self.assertGreaterEqual(
                checked, len(module._MESSAGES) // 2,
                f"nur {checked} t()-Aufrufe in {path.name} erkannt - die Analyse "
                f"greift offenbar nicht mehr")

    def test_multi_placeholder_templates_put_their_values_in_place(self):
        """Die VORLAGEN setzen ihre Werte an der erwarteten Stelle ein.

        Achtung, was dieser Test NICHT leistet: er ruft t() mit von Hand
        uebergebenen Argumenten auf und kann daher nur fehlschlagen, wenn sich
        die Katalogvorlage aendert - ueber die Aufrufstellen im Code sagt er
        nichts. Sein frueherer Name behauptete genau das ('..._call_sites_...')
        und liess die Luecke wie geschlossen aussehen. Die Aufrufstellen selbst
        pruefen RenderedMessageOrderTests (hier) und die gleichnamige Klasse in
        test_ensure.py, indem sie die tatsaechlich ERZEUGTE Meldung ansehen."""
        rendered = mrt.t("err_discover_timeout", mrt.SUBPROCESS_TIMEOUT, "1.2.3.4")
        # Die Sekundenzahl gehoert an die Zeitangabe, die IP an den Geraeteteil.
        self.assertIn(f"{mrt.SUBPROCESS_TIMEOUT}s", rendered)
        self.assertNotIn("1.2.3.4s", rendered)
        self.assertIn("1.2.3.4", rendered)

        mismatch = mrt.t("dev_id_mismatch", "Wohnzimmer", "111", "222")
        self.assertIn("id=111", mismatch)
        self.assertIn("id=222", mismatch)
        self.assertLess(mismatch.index("id=111"), mismatch.index("id=222"),
                        "bestehende id muss VOR der Cloud-id stehen")

    def test_interpolation_works_in_both_languages(self):
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}):
            self.assertIn("Wohnzimmer", mrt.t("dev_fetching", "Wohnzimmer", "1.2.3.4"))
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "de"}):
            self.assertIn("Wohnzimmer", mrt.t("dev_fetching", "Wohnzimmer", "1.2.3.4"))

    def test_languages_actually_differ(self):
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}):
            english = mrt.t("diag_rejected")
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "de"}):
            german = mrt.t("diag_rejected")
        self.assertNotEqual(english, german)


class ClassifyVerifyFailureTests(_LangMixin):
    """#2: Ein gescheiterter Kandidat muss seinen GRUND nennen. Die Textmarken
    stammen aus einer realen Messung gegen msmart-ng 2026.7.0 (je ein Fake-
    Geraet pro Fall); msmart-ng meldet alle vier als denselben Typ
    (AuthenticationError), unterscheidbar nur am Meldungstext."""

    class _AuthenticationError(Exception):
        """Stellvertreter fuer msmart.lan.AuthenticationError (kein msmart noetig)."""

    def _classify(self, message):
        return mrt.classify_verify_failure(self._AuthenticationError(message))

    def test_error_packet_is_rejected(self):
        # Der Fall aus Issue #2: Geraet sendet 8370...ERROR (PacketType 0xF).
        code, text = self._classify("Error packet received.")
        self.assertEqual(code, mrt.VERIFY_REJECTED)
        self.assertIn("rejected", text)

    def test_no_response_is_silent(self):
        code, text = self._classify("No response from host.")
        self.assertEqual(code, mrt.VERIFY_SILENT)
        self.assertIn("does not answer", text)

    def test_transport_closing_is_reset(self):
        code, _ = self._classify("Transport is closing or closed.")
        self.assertEqual(code, mrt.VERIFY_RESET)

    def test_connect_failed_is_unreachable(self):
        code, _ = self._classify("Connect failed.")
        self.assertEqual(code, mrt.VERIFY_UNREACHABLE)

    def test_rejected_and_silent_are_distinguishable(self):
        # Kernpunkt aus #2: 'Token falsch' und 'Geraet stumm' sind voellig
        # verschiedene Ursachen und duerfen nie denselben Code liefern.
        self.assertNotEqual(self._classify("Error packet received.")[0],
                            self._classify("No response from host.")[0])

    def test_own_timeout_is_reported_separately(self):
        # asyncio.wait_for wirft einen nackten TimeoutError - der darf NICHT
        # als stummes Geraet durchgehen, sonst zeigt die Diagnose auf die
        # falsche Ursache.
        code, text = mrt.classify_verify_failure(TimeoutError())
        self.assertEqual(code, mrt.VERIFY_CAP)
        self.assertIn(str(mrt.VERIFY_TIMEOUT), text)

    def test_named_cause_wins_over_the_timeout_branch(self):
        # Eine benannte Ursache muss auch dann korrekt eingeordnet werden, wenn
        # sie als ECHTER TimeoutError ankommt: msmart-ng erzeugt 'Connect
        # timeout.' als TimeoutError und haengt ihn nur auf manchen Pfaden in
        # einen AuthenticationError um. Wuerde der isinstance-Test zuerst greifen,
        # landete dieselbe Ursache je nach Aufrufweg mal als 'unreachable' und mal
        # pauschal als 'unser Zeitlimit'.
        # (Die frueher hier stehende Zusicherung prueft eine Eigenschaft der
        # test-eigenen Ersatzklasse und konnte nie fehlschlagen.)
        self.assertEqual(mrt.classify_verify_failure(
            TimeoutError("Connect timeout."))[0], mrt.VERIFY_UNREACHABLE)
        self.assertEqual(mrt.classify_verify_failure(
            TimeoutError("No response from host."))[0], mrt.VERIFY_SILENT)
        # Ohne Meldungstext bleibt es korrekt unser eigener Deckel.
        self.assertEqual(mrt.classify_verify_failure(TimeoutError())[0], mrt.VERIFY_CAP)

    def test_wrong_key_is_named_instead_of_falling_through(self):
        # msmart-ngs praeziseste Aussage: der Handshake kam zurueck, liess sich
        # aber nicht entschluesseln -> Geraet erreichbar, Token angenommen, KEY
        # falsch. Fiel zuvor stumm auf VERIFY_OTHER und ganz ohne Hinweis durch.
        code, text = self._classify("Calculated and received SHA256 digest do not match.")
        self.assertEqual(code, mrt.VERIFY_BAD_KEY)
        self.assertIn("key", text.lower())
        self.assertIsNotNone(mrt.summarize_failure_hint([code, code]))

    def test_real_tcp_reset_is_classified_as_reset(self):
        # Ein echtes RST kommt als durchgereichter OSError-Text an, NICHT als
        # 'Transport is closing' - der Errno unterscheidet sich je Plattform
        # (54 macOS / 104 Linux), der Wortlaut nicht.
        for errno in (54, 104):
            with self.subTest(errno=errno):
                code, _ = self._classify(f"[Errno {errno}] Connection reset by peer")
                self.assertEqual(code, mrt.VERIFY_RESET)

    def test_connect_timeout_is_unreachable_not_other(self):
        # Der haeufigste reale Fehlerfall (Geraet aus, veraltete IP, Firewall
        # verwirft): fiel zuvor auf VERIFY_OTHER und bekam damit GAR KEINEN
        # Gesamthinweis - obwohl der passende Hinweistext existierte.
        code, text = self._classify("Connect timeout.")
        self.assertEqual(code, mrt.VERIFY_UNREACHABLE)
        self.assertIsNotNone(mrt.summarize_failure_hint([code, code]))
        # Muss sich vom aktiv abgewiesenen Fall im TEXT unterscheiden - die
        # Ursachen sind verschieden, auch wenn der Code derselbe ist.
        self.assertNotEqual(text, self._classify("Connect failed.")[1])

    def test_unknown_message_keeps_original_text(self):
        # Formuliert msmart-ng kuenftig anders um, geht Einordnung verloren -
        # aber niemals die Information selbst.
        code, text = self._classify("Some brand new failure mode")
        self.assertEqual(code, mrt.VERIFY_OTHER)
        self.assertIn("Some brand new failure mode", text)

    def test_empty_message_still_yields_text(self):
        code, text = self._classify("")
        self.assertEqual(code, mrt.VERIFY_OTHER)
        self.assertTrue(text.strip())

    def test_a_whitespace_only_message_falls_back_to_the_no_detail_text(self):
        # Ohne das .strip() in classify_verify_failure gilt "   " als Inhalt:
        # der Nutzer bekaeme "AuthenticationError:" mit einer Handvoll
        # Leerzeichen dahinter statt der Angabe, dass es keine naehere Auskunft
        # gibt. Der bestehende Leerstring-Test sieht das nicht.
        code, text = self._classify("   \n  ")
        self.assertEqual(code, mrt.VERIFY_OTHER)
        self.assertIn(mrt.t("diag_no_detail"), text)

    def test_marker_match_is_case_insensitive(self):
        self.assertEqual(self._classify("ERROR PACKET RECEIVED.")[0], mrt.VERIFY_REJECTED)


# Woerter, die eine BESTIMMTE Ursache behaupten. msmart-ng wirft 'Connect
# failed.' fuer JEDEN OSError aus create_connection (lan.py): abgewiesene
# Verbindung, DNS-Fehler, nicht erreichbares Netz, nicht erreichbarer Host -
# real nachgestellt mit DNS-Fehler und ENETUNREACH. Der Text darf daher keine
# einzelne davon benennen.
_CAUSE_CLAIM_WORDS = {
    "en": ("refused", "answered", "responded", "listening", "switched off",
           "unplugged", "firewall", "wrong ip"),
    "de": ("abgewiesen", "geantwortet", "lauscht", "ausgeschaltet",
           "firewall", "falsche ip"),
}

# Woerter, die den Text OFFEN halten muessen: er soll die zu pruefenden
# Moeglichkeiten nebeneinander nennen, statt sich fuer eine zu entscheiden.
# Ohne diese Haelfte waere die Pruefung eine blosse Sperrliste - ein beliebiger
# neuer Falschtext ohne die verbotenen Woerter kaeme glatt durch.
_OPEN_QUESTION_WORDS = {
    "en": ("address", "port"),
    "de": ("adresse", "port"),
}


def _cause_claim_problems(text: str, lang: str) -> list[str]:
    """Alle Gruende, aus denen ``text`` als Ursachenbehauptung durchgeht."""
    problems = []
    low = text.lower()
    for word in _CAUSE_CLAIM_WORDS[lang]:
        if word in low:
            problems.append(f"behauptet '{word}'")
    for word in _OPEN_QUESTION_WORDS[lang]:
        if word not in low:
            problems.append(f"nennt '{word}' nicht")
    return problems


class UnreachableNeutralityTests(unittest.TestCase):
    """Der 'keine Verbindung'-Text darf keine einzelne Ursache behaupten.

    Die frueherer Fassung dieses Tests war eine reine SPERRLISTE und damit
    wirkungslos: ein voellig neuer Falschtext ('the unit is switched off -
    nothing is there at that address') bestand ihn anstandslos, und das Leeren
    der Wortliste ebenfalls - nichts sicherte die Liste selbst ab. Hier wird
    daher in beide Richtungen geprueft: der Katalogtext muss sauber sein, UND
    die Pruefung muss bekannte Falschtexte tatsaechlich zurueckweisen."""

    # Jeder dieser Texte MUSS beanstandet werden. Der dritte je Sprache
    # enthaelt bewusst kein einziges verbotenes Wort - er faellt nur ueber die
    # fehlende Offenheit auf und haelt damit diese Haelfte der Pruefung wach.
    BAD_TEXTS = {
        "en": ("connection was refused - something answered on that port",
               "the unit is switched off - nothing is there at that address",
               "nothing is there at that address"),
        "de": ("die Verbindung wurde abgewiesen, es hat also etwas geantwortet",
               "die Anlage ist ausgeschaltet - unter der Adresse ist nichts",
               "unter der Adresse ist nichts"),
    }

    def test_the_catalog_text_is_neutral_in_both_languages(self):
        for lang in ("en", "de"):
            with self.subTest(lang=lang), \
                    mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": lang}):
                text = mrt.t("diag_unreachable")
                self.assertEqual(_cause_claim_problems(text, lang), [], text)

    def test_the_check_rejects_known_wrong_texts(self):
        for lang, texts in self.BAD_TEXTS.items():
            for text in texts:
                with self.subTest(lang=lang, text=text):
                    self.assertNotEqual(
                        _cause_claim_problems(text, lang), [],
                        f"[{lang}] Falschtext wurde nicht beanstandet: {text}")

    def test_the_word_lists_themselves_are_intact(self):
        # Selbstschutz: leert jemand die Listen, prueft oben nichts mehr - und
        # genau das ueberlebte bisher unbemerkt.
        for lang in ("en", "de"):
            with self.subTest(lang=lang):
                self.assertGreaterEqual(len(_CAUSE_CLAIM_WORDS[lang]), 4)
                self.assertGreaterEqual(len(_OPEN_QUESTION_WORDS[lang]), 2)
        # Die beiden Woerter, um die es historisch ging, muessen drin bleiben.
        self.assertIn("refused", _CAUSE_CLAIM_WORDS["en"])
        self.assertIn("answered", _CAUSE_CLAIM_WORDS["en"])
        self.assertIn("abgewiesen", _CAUSE_CLAIM_WORDS["de"])
        self.assertIn("geantwortet", _CAUSE_CLAIM_WORDS["de"])

    def test_the_timeout_text_offers_alternatives_instead_of_one_cause(self):
        """Der Zeitueberschreitungs-Text DARF Ursachen nennen - aber mehrere.

        'Connect timeout.' heisst 'unter der Adresse hat niemand geantwortet';
        das laesst mehrere Erklaerungen zu, und der Text nennt sie bewusst
        nebeneinander. Genau deshalb faellt er nicht unter die Neutralitaets-
        pruefung oben - wohl aber unter die Auflage, sich nicht auf eine
        einzelne festzulegen."""
        cases = {"en": (" or ", "IP", "firewall"),
                 "de": (" oder ", "IP", "Firewall")}
        for lang, parts in cases.items():
            with self.subTest(lang=lang), \
                    mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": lang}):
                text = mrt.t("diag_unreachable_timeout")
                for part in parts:
                    self.assertIn(part, text)


class ClassifyVerifyFailureGermanTests(ClassifyVerifyFailureTests):
    """Dieselbe Matrix auf Deutsch: die CODES muessen sprachunabhaengig sein.
    Nur die beiden textpruefenden Faelle werden sprachspezifisch ueberschrieben."""

    LANG = "de"

    def test_error_packet_is_rejected(self):
        code, text = self._classify("Error packet received.")
        self.assertEqual(code, mrt.VERIFY_REJECTED)
        self.assertIn("abgelehnt", text)

    def test_no_response_is_silent(self):
        code, text = self._classify("No response from host.")
        self.assertEqual(code, mrt.VERIFY_SILENT)
        self.assertIn("antwortet", text)


class SummarizeFailureHintTests(_LangMixin):
    """#2: Aus den Codes ALLER Kandidaten einen handlungsleitenden Hinweis ableiten."""

    def test_all_rejected_points_at_tokens_not_network(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_REJECTED] * 3)
        self.assertIsNotNone(hint)
        self.assertIn("Network", hint)

    def test_all_silent_mentions_single_connection(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_SILENT] * 2)
        self.assertIsNotNone(hint)
        self.assertIn("ONE local connection", hint)

    def test_mixed_rejected_then_silent_flags_the_pattern(self):
        # Genau das Muster aus Issue #2: erster Kandidat aktiv abgelehnt,
        # danach schweigt das Geraet -> spaetere Kandidaten sind nicht aussagekraeftig.
        hint = mrt.summarize_failure_hint([mrt.VERIFY_REJECTED, mrt.VERIFY_SILENT,
                                           mrt.VERIFY_SILENT])
        self.assertIsNotNone(hint)
        self.assertIn("NOT meaningful", hint)

    def test_all_unreachable_points_at_port(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_UNREACHABLE])
        self.assertIsNotNone(hint)
        self.assertIn("6444", hint)

    def test_all_reset_mentions_single_connection(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_RESET] * 2)
        self.assertIsNotNone(hint)
        self.assertIn("ONE local connection", hint)

    def test_empty_and_unclassifiable_yield_no_invented_hint(self):
        self.assertIsNone(mrt.summarize_failure_hint([]))
        self.assertIsNone(mrt.summarize_failure_hint([mrt.VERIFY_OTHER]))

    def test_all_cap_yields_no_invented_hint(self):
        # Bewusst KEIN Sammelhinweis: unser Zeitlimit liegt oberhalb von
        # msmart-ngs eigenem Worst Case, der Zustand tritt praktisch nicht ein.
        # Fuer etwas, das nicht passiert, wird kein Ratschlag erfunden.
        self.assertIsNone(mrt.summarize_failure_hint([mrt.VERIFY_CAP] * 3))


class MixedHintOrderTests(_LangMixin):
    """Der gemischte Hinweis darf NUR greifen, wenn die Ablehnung TATSAECHLICH
    ZUERST kam.

    Zuvor arbeitete die Funktion auf set(codes) und war damit reihenfolgenblind:
    bei [nicht erreichbar, abgelehnt] behauptete sie 'danach antwortete das
    Geraet nicht mehr', obwohl die Ablehnung das LETZTE Ereignis war - also ein
    Hinweis, dessen jede Teilaussage falsch ist. Davor gab es an dieser Stelle
    gar keinen Hinweis; die Reihenfolgenblindheit machte aus Schweigen eine
    Fehlauskunft."""

    R = "rejected"
    S = "silent"
    U = "unreachable"
    RES = "reset"
    C = "cap"

    def _is_mixed(self, codes):
        hint = mrt.summarize_failure_hint(codes)
        return hint == mrt.t("hint_mixed")

    def test_rejection_first_gives_the_mixed_hint(self):
        for tail in (self.S, self.U, self.RES, self.C):
            with self.subTest(tail=tail):
                self.assertTrue(self._is_mixed([self.R, tail]))

    def test_rejection_last_does_not(self):
        # Der Regressionsfall. Kein Hinweis ist hier korrekt: das Geraet hat am
        # Ende sehr wohl geantwortet.
        for head in (self.S, self.U, self.RES, self.C):
            with self.subTest(head=head):
                self.assertFalse(self._is_mixed([head, self.R]))

    def test_reverse_order_yields_no_hint_at_all(self):
        self.assertIsNone(mrt.summarize_failure_hint([self.U, self.R]))
        self.assertIsNone(mrt.summarize_failure_hint([self.S, self.R]))

    def test_first_rejection_before_first_blocking_decides(self):
        # Nicht "irgendwo eine Ablehnung", sondern die ERSTE relativ zum ersten
        # Verstummen.
        self.assertTrue(self._is_mixed([self.R, self.R, self.S]))
        self.assertTrue(self._is_mixed([self.R, self.S, self.U]))
        self.assertFalse(self._is_mixed([self.S, self.R, self.S]))

    def test_without_any_rejection_there_is_no_mixed_hint(self):
        self.assertFalse(self._is_mixed([self.S, self.U]))

    def test_unclassified_companion_yields_nothing(self):
        # 'other' gehoert nicht zu den Verstummens-Codes -> nichts behaupten.
        self.assertIsNone(mrt.summarize_failure_hint([self.R, mrt.VERIFY_OTHER]))


class HintTruthTableTests(_LangMixin):
    """Punktweise Festlegung der Hinweis-Semantik, abgeleitet aus einer
    Wahrheitstabelle ueber ALLE Code-Folgen bis Laenge 4 (2801 Stueck).

    Jeder Fall hier ist ein Punkt, an dem sich zwei plausible Fassungen der
    Funktion unterscheiden - anders gesagt: jeder faengt eine Mutation, die
    zuvor ueberlebt hat. Geprueft wird der KATALOG-SCHLUESSEL des Hinweises und
    nicht nur 'irgendein Hinweis', damit eine Verwechslung zweier Hinweise
    ebenfalls auffaellt."""

    R = mrt.VERIFY_REJECTED
    K = mrt.VERIFY_BAD_KEY
    S = mrt.VERIFY_SILENT
    RES = mrt.VERIFY_RESET
    U = mrt.VERIFY_UNREACHABLE
    C = mrt.VERIFY_CAP
    O = mrt.VERIFY_OTHER  # noqa: E741  (Kuerzel wie die uebrigen Codes)

    # Sprachabhaengige Marke fuer 'die Antwort liess sich nicht entschluesseln'.
    DECRYPT_MARKER = "decrypt"

    _HINT_KEYS = ("hint_all_rejected", "hint_all_bad_key", "hint_all_answered",
                  "hint_all_silent", "hint_all_unreachable", "hint_all_reset",
                  "hint_mixed")

    def _label(self, codes):
        """Katalog-Schluessel des erzeugten Hinweises, oder None."""
        hint = mrt.summarize_failure_hint(list(codes))
        if hint is None:
            return None
        for key in self._HINT_KEYS:
            if hint == mrt.t(key):
                return key
        self.fail(f"unbekannter Hinweis fuer {codes}: {hint!r}")

    def test_pure_classes_keep_their_own_hint(self):
        for codes, key in (((self.R, self.R), "hint_all_rejected"),
                           ((self.K, self.K), "hint_all_bad_key"),
                           ((self.S, self.S), "hint_all_silent"),
                           ((self.U, self.U), "hint_all_unreachable"),
                           ((self.RES, self.RES), "hint_all_reset")):
            with self.subTest(codes=codes):
                self.assertEqual(self._label(codes), key)

    def test_answered_mixture_points_at_stale_credentials(self):
        """[abgelehnt, Key falsch]: das Geraet hat JEDEN Versuch beantwortet.

        Der klarste Befund, den dieses Werkzeug ueberhaupt liefern kann - und
        bis hierher der einzige, der ganz ohne Hinweis blieb, weil er keiner der
        reinen Mengen gleicht."""
        for codes in ((self.R, self.K), (self.K, self.R),
                      (self.R, self.R, self.K), (self.K, self.K, self.R)):
            with self.subTest(codes=codes):
                self.assertEqual(self._label(codes), "hint_all_answered")

    def test_a_wrong_key_counts_as_an_answer_for_the_mixed_hint(self):
        """Der gemischte Hinweis gilt fuer BEIDE Antwortarten.

        Faellt VERIFY_BAD_KEY aus _ANSWERED_CODES heraus, ist [Key falsch,
        stumm] wieder hinweislos - und der Kern der letzten Erweiterung damit
        stillschweigend zurueckgenommen."""
        self.assertEqual(self._label((self.K, self.S)), "hint_mixed")
        self.assertEqual(self._label((self.K, self.U)), "hint_mixed")

    def test_the_first_answer_must_precede_the_first_blockade(self):
        # Die ERSTE Antwort zaehlt, nicht irgendeine: bei [stumm, abgelehnt,
        # stumm] war die Blockade zuerst da, das Geraet hat also nicht
        # 'aufgehoert' zu antworten - es hat mittendrin geantwortet.
        self.assertEqual(self._label((self.S, self.R, self.S)), None)
        self.assertEqual(self._label((self.K, self.S, self.R, self.S)),
                         "hint_mixed")

    def test_nothing_answered_may_follow_the_last_answer(self):
        # Der Text behauptet ein ENDE ('danach nicht mehr'). Bei
        # [abgelehnt, stumm, abgelehnt] hat Kandidat 3 sehr wohl geantwortet -
        # eine Pruefung, die nur den Anfang betrachtet, sieht das nicht.
        self.assertEqual(self._label((self.R, self.S, self.R)), None)
        self.assertEqual(self._label((self.R, self.S, self.K)), None)
        # Gegenprobe: endet die Folge blockierend, bleibt der Hinweis richtig -
        # auch wenn zwischendrin erneut geantwortet wurde.
        self.assertEqual(self._label((self.R, self.S, self.R, self.S)),
                         "hint_mixed")

    def test_an_unclassified_code_in_the_tail_keeps_us_quiet(self):
        # Ueber VERIFY_OTHER ist per Definition nichts bekannt - er kann ein
        # Verstummen weder belegen noch widerlegen.
        self.assertEqual(self._label((self.R, self.O, self.S)), None)
        self.assertEqual(self._label((self.R, self.S, self.O)), None)

    def test_empty_and_unclassifiable_stay_silent(self):
        self.assertEqual(self._label(()), None)
        self.assertEqual(self._label((self.O, self.O)), None)
        self.assertEqual(self._label((self.C, self.C)), None)

    def test_mixed_hint_names_both_kinds_of_answer(self):
        """Der Text muss zur Menge passen.

        _ANSWERED_CODES umfasst Ablehnung UND nicht entschluesselbare Antwort;
        eine Formulierung, die nur von einer Ablehnung spricht, behauptet bei
        [Key falsch, stumm] etwas, das nie geschehen ist."""
        for key in ("hint_mixed", "hint_all_answered"):
            with self.subTest(key=key):
                self.assertIn(self.DECRYPT_MARKER, mrt.t(key).lower())


class HintTruthTableGermanTests(HintTruthTableTests):
    """Dieselbe Tabelle auf Deutsch: die Semantik ist sprachunabhaengig."""

    LANG = "de"
    DECRYPT_MARKER = "entschluessel"


class SummarizeFailureHintGermanTests(SummarizeFailureHintTests):
    """Gegenprobe auf Deutsch - dieselbe Logik, andere Sprache."""

    LANG = "de"

    def test_all_rejected_points_at_tokens_not_network(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_REJECTED] * 3)
        self.assertIn("Netzwerk", hint)

    def test_all_silent_mentions_single_connection(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_SILENT] * 2)
        self.assertIn("EINE lokale Verbindung", hint)

    def test_mixed_rejected_then_silent_flags_the_pattern(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_REJECTED, mrt.VERIFY_SILENT])
        self.assertIn("NICHT aussagekraeftig", hint)

    def test_all_reset_mentions_single_connection(self):
        hint = mrt.summarize_failure_hint([mrt.VERIFY_RESET] * 2)
        self.assertIn("EINE lokale Verbindung", hint)


def _fake_verify(results):
    """Async-Ersatz fuer verify_credentials: liefert die Tupel der Reihe nach."""
    seq = list(results)

    async def _verify(ip, port, device_id, key, token):
        return seq.pop(0)

    return _verify


class CandidateLoopTests(_LangMixin):
    """#2: Kandidaten werden ENTZERRT geprueft (das Geraet haelt nur eine
    Verbindung), und jeder Fehlschlag nennt seinen Grund."""

    DEV = {"name": "W", "ip": "1.2.3.4", "id": 42, "port": 6444}

    def _run(self, candidates, results):
        slept = []
        out = io.StringIO()
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: (candidates, None)), \
                mock.patch.object(mrt, "verify_credentials", _fake_verify(results)), \
                mock.patch.object(mrt.time, "sleep", lambda s: slept.append(s)), \
                redirect_stdout(out):
            ok = mrt.update_device(dict(self.DEV))
        return ok, out.getvalue(), slept

    def test_delay_between_candidates_but_not_before_first(self):
        ok, _, slept = self._run(
            [("k1", "t1"), ("k2", "t2"), ("k3", "t3")],
            [(False, mrt.VERIFY_REJECTED, "abgelehnt"),
             (False, mrt.VERIFY_SILENT, "stumm"),
             (False, mrt.VERIFY_SILENT, "stumm")])
        self.assertFalse(ok)
        # Drei Kandidaten -> genau ZWEI Pausen (keine vor dem ersten Versuch).
        self.assertEqual(slept, [mrt.CANDIDATE_DELAY] * 2)

    def test_candidate_delay_is_actually_long_enough_to_matter(self):
        # Der Test oben leitet seine Erwartung aus CANDIDATE_DELAY selbst ab und
        # bliebe daher auch bei 0.0 gruen - er sichert Anzahl und Platzierung der
        # Pausen, nicht ihre Wirkung. Der Wert wird deshalb hier gegen eine
        # UNABHAENGIGE Untergrenze geprueft: eine Pause unterhalb weniger Sekunden
        # entzerrt nichts, und genau die fehlende Entzerrung war der Befund aus
        # Issue #2. Gleiche Bauweise wie VerifyTimeoutHeadroomTests.
        self.assertGreaterEqual(mrt.CANDIDATE_DELAY, 3.0)
        self.assertGreaterEqual(mrt.DEVICE_DELAY, 1.0)

    def test_no_delay_for_single_candidate(self):
        ok, _, slept = self._run([("k1", "t1")], [(True, "", "")])
        self.assertTrue(ok)
        self.assertEqual(slept, [])

    def test_success_does_not_sleep_after_hit(self):
        # Erster Kandidat passt -> kein weiterer Versuch, keine weitere Pause.
        ok, _, slept = self._run([("k1", "t1"), ("k2", "t2")],
                                 [(True, "", "")])
        self.assertTrue(ok)
        self.assertEqual(slept, [])

    def test_candidate_line_is_cause_neutral(self):
        # Die Zeile darf nicht "abgelehnt" behaupten: nur EINE der moeglichen
        # Ursachen ist eine Ablehnung durch das Geraet. Bei einer nie zustande
        # gekommenen Verbindung hat niemand etwas abgelehnt.
        _, out, _ = self._run([("k1", "t1")],
                              [(False, mrt.VERIFY_UNREACHABLE, "keine Verbindung")])
        self.assertNotIn("rejected:", out)
        self.assertIn("failed:", out)

    def test_try_next_suffix_only_between_candidates(self):
        _, out, _ = self._run(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_SILENT, "A"), (False, mrt.VERIFY_SILENT, "B")])
        suffix = mrt.t("dev_try_next")
        # Genau einmal: nach dem ersten, nicht nach dem letzten Kandidaten.
        self.assertEqual(out.count(suffix), 1)

    def test_reason_is_printed_per_candidate(self):
        _, out, _ = self._run(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_REJECTED, "Geraet hat den Token aktiv abgelehnt"),
             (False, mrt.VERIFY_SILENT, "Geraet antwortet nicht")])
        self.assertIn("Geraet hat den Token aktiv abgelehnt", out)
        self.assertIn("Geraet antwortet nicht", out)

    def test_summary_hint_is_printed_on_total_failure(self):
        _, out, _ = self._run(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_REJECTED, "x"), (False, mrt.VERIFY_REJECTED, "y")])
        self.assertIn("Hint:", out)
        self.assertIn("Network", out)

    def test_successful_candidate_is_stored(self):
        dev = dict(self.DEV)
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([("k1", "t1"), ("k2", "t2")], None)), \
                mock.patch.object(mrt, "verify_credentials",
                                  _fake_verify([(False, mrt.VERIFY_REJECTED, "x"),
                                                (True, "", "")])), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()):
            ok = mrt.update_device(dev)
        self.assertTrue(ok)
        self.assertEqual((dev["key"], dev["token"]), ("k2", "t2"))


class _RecordingAC:
    """Aufzeichnendes Ersatz-Geraet fuer verify_credentials.

    Es ersetzt msmart-ngs AirConditioner im (bereits registrierten) Stub-Modul.
    Damit laesst sich der Rumpf von verify_credentials pruefen, ohne Netzwerk und
    ohne echte Bibliothek - bisher wurde die Funktion in JEDEM Test komplett
    ersetzt, sodass ihr Inhalt ungeprueft blieb."""

    instances: list["_RecordingAC"] = []

    # Als KLASSEN-Vorgaben, nicht als Instanzattribute: verify_credentials legt
    # sein AC-Objekt selbst an, ein Test kommt also nicht an die Instanz heran,
    # bevor sie benutzt wird. Ueber die Klasse laesst sich das Verhalten per
    # mock.patch.object trotzdem umstellen.
    auth_delay = 0.0
    refresh_delay = 0.0

    def __init__(self, *, ip=None, port=None, device_id=None):
        self.init_args = (ip, port, device_id)
        self.auth_args = None
        self.refresh_calls = 0
        self.closed = False
        self.auth_raises = None
        _RecordingAC.instances.append(self)

    async def authenticate(self, token, key):
        self.auth_args = (token, key)
        if self.auth_delay:
            await asyncio.sleep(self.auth_delay)
        if self.auth_raises is not None:
            raise self.auth_raises

    async def refresh(self):
        self.refresh_calls += 1
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)

    async def close(self):
        self.closed = True


class VerifyCredentialsBodyTests(_LangMixin):
    """Direkter Test von verify_credentials - der Funktion, auf der die gesamte
    Zusage des Werkzeugs beruht ('jeder Kandidat wird gegen das Geraet
    verifiziert'). Sie wurde bisher ausnahmslos wegge-mockt: eine Fassung, die
    bedingungslos True zurueckgibt oder Token und Key vertauscht, waere von
    keinem Test bemerkt worden."""

    def setUp(self):
        super().setUp()
        _RecordingAC.instances = []
        module = sys.modules["msmart.device.AC.device"]
        original = module.AirConditioner
        module.AirConditioner = _RecordingAC
        self.addCleanup(lambda: setattr(module, "AirConditioner", original))

    def _call(self, **kwargs):
        params = {"ip": "1.2.3.4", "port": 6444, "device_id": 42,
                  "key": "MEIN_KEY", "token": "MEIN_TOKEN"}
        params.update(kwargs)
        return asyncio.run(mrt.verify_credentials(**params))

    def test_authenticate_receives_token_first_then_key(self):
        # msmart-ngs Signatur ist authenticate(token, key). Vertauscht man beide,
        # scheitert JEDE Verbindung fuer JEDEN Nutzer - lautlos, weil unsere
        # Aufrufstelle (key, token) heisst und die Verwechslung naheliegt.
        self._call()
        device = _RecordingAC.instances[0]
        self.assertEqual(device.auth_args, ("MEIN_TOKEN", "MEIN_KEY"))

    def test_device_is_constructed_with_the_given_address(self):
        self._call(ip="10.0.0.7", port=6445, device_id=99)
        self.assertEqual(_RecordingAC.instances[0].init_args, ("10.0.0.7", 6445, 99))

    def test_success_reports_ok_and_refreshes(self):
        ok, code, detail = self._call()
        self.assertTrue(ok)
        self.assertEqual((code, detail), ("", ""))
        self.assertEqual(_RecordingAC.instances[0].refresh_calls, 1)

    def test_device_is_closed_on_success_and_on_failure(self):
        self._call()
        self.assertTrue(_RecordingAC.instances[0].closed)
        _RecordingAC.instances = []
        with mock.patch.object(_RecordingAC, "authenticate",
                               side_effect=RuntimeError("boom"), autospec=True):
            self._call()
        self.assertTrue(_RecordingAC.instances[0].closed)

    def test_authentication_failure_is_reported_not_swallowed(self):
        class _Auth(Exception):
            pass
        with mock.patch.object(_RecordingAC, "authenticate",
                               side_effect=_Auth("Error packet received."),
                               autospec=True):
            ok, code, _ = self._call()
        self.assertFalse(ok)
        self.assertEqual(code, mrt.VERIFY_REJECTED)

    def test_own_time_limit_actually_caps_a_hanging_device(self):
        # Ohne den wait_for-Deckel wuerde ein haengendes Geraet den ganzen Lauf
        # blockieren. Deckel fuer den Test kurz setzen, damit er schnell bleibt.
        with mock.patch.object(mrt, "VERIFY_TIMEOUT", 0.05):
            hang = self._make_hanging()
            ok, code, _ = hang
        self.assertFalse(ok)
        self.assertEqual(code, mrt.VERIFY_CAP)

    def _make_hanging(self):
        async def _slow(self_, token, key):
            await asyncio.sleep(5)
        with mock.patch.object(_RecordingAC, "authenticate", _slow):
            return self._call()

    def test_a_hanging_refresh_is_capped_as_well(self):
        # Der Deckel um refresh() war ungetestet - nur der um authenticate.
        # Ohne ihn blockierte ein Geraet, das den Handshake annimmt und dann
        # verstummt, den gesamten Lauf (im Wochen-Cron unbemerkt).
        _RecordingAC.instances = []
        with mock.patch.object(mrt, "VERIFY_TIMEOUT", 0.05), \
                mock.patch.object(_RecordingAC, "refresh_delay", 0.5):
            ok, code, _ = self._call()
        self.assertFalse(ok)
        self.assertEqual(code, mrt.VERIFY_CAP)
        # Der Handshake war erfolgreich - nur das Nachlesen haengt.
        self.assertIsNotNone(_RecordingAC.instances[0].auth_args)


class DiscoverRobustnessTests(_LangMixin):
    """Details, die darueber entscheiden, ob ein echter Fehlschlag lesbar ist -
    und ob ein haengender discover-Aufruf den Wochen-Cron blockiert."""

    @staticmethod
    def _ns(rc=0, out="", err=""):
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    def test_the_subprocess_call_carries_a_timeout(self):
        # test_timeout_becomes_runtimeerror mockt subprocess.run und wirft
        # unbedingt - ein FEHLENDES timeout=-Argument sieht dieser Test also
        # nicht. Hier wird das Argument selbst geprueft.
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw)
            return self._ns(out='{"tokenlist": [{"key": "aa", "token": "bb"}]}')

        with mock.patch("midea_refresh_tokens.subprocess.run", side_effect=fake_run):
            mrt.fetch_candidate_credentials("1.2.3.4")
        self.assertEqual(seen.get("timeout"), mrt.SUBPROCESS_TIMEOUT)

    def test_the_subprocess_timeout_has_headroom_for_the_cloud(self):
        # Anker wie bei VERIFY_TIMEOUT, gemessen an midea-local 6.6.1: jeder
        # Cloud-Request wird bis zu DREIMAL mit je 10s Zeitlimit versucht
        # (cloud.py: 'for _ in range(3)' um 'ClientTimeout(10)'), und der
        # Token-Abruf braucht mindestens zwei Requests (Login und Token).
        self.assertGreaterEqual(mrt.SUBPROCESS_TIMEOUT, 2 * 3 * 10)

    def test_a_signal_killed_discover_counts_as_a_failure(self):
        # Ein per Signal beendeter Prozess meldet einen NEGATIVEN Rueckgabewert.
        # Mit '> 0' statt '!= 0' gaelte das als Erfolg - und der Lauf scheiterte
        # anschliessend an der fehlenden tokenlist, mit irrefuehrender Meldung.
        with self.assertRaises(RuntimeError) as cm:
            mrt._parse_discover_output(self._ns(rc=-9, err="killed"))
        self.assertIn("-9", str(cm.exception))

    def test_the_failure_tail_shows_the_end_of_the_output(self):
        # Die Debug-Ausgabe ist lang; der FEHLER steht am Ende, das Setup-
        # Rauschen am Anfang. Mit '[:800]' saehe der Nutzer genau das Rauschen.
        noisy = "ANFANGSMARKE " + ("setup-rauschen " * 100) + "ECHTER-FEHLER"
        with self.assertRaises(RuntimeError) as cm:
            mrt._parse_discover_output(self._ns(rc=1, out=noisy))
        message = str(cm.exception)
        self.assertIn("ECHTER-FEHLER", message)
        self.assertNotIn("ANFANGSMARKE", message)

    def test_the_missing_tokenlist_tail_also_shows_the_end(self):
        noisy = "ANFANGSMARKE " + ("setup-rauschen " * 100) + "LETZTE-ZEILE"
        with self.assertRaises(RuntimeError) as cm:
            mrt._parse_discover_output(self._ns(rc=0, out=noisy))
        message = str(cm.exception)
        self.assertIn("LETZTE-ZEILE", message)
        self.assertNotIn("ANFANGSMARKE", message)

    def test_output_on_stderr_is_evaluated_too(self):
        # Die --debug-Ausgabe der CLI (und damit die tokenlist) landet auf
        # stderr. Wertet man nur stdout aus, findet der Abruf nie einen
        # Kandidaten - bei voellig unauffaelliger Fehlermeldung.
        matches, _ = mrt._parse_discover_output(
            self._ns(err='{"tokenlist": [{"key": "aa", "token": "bb"}]}'))
        self.assertEqual(matches, [("aa", "bb")])

    def test_the_first_reported_appliance_id_is_used(self):
        # Mehrere Treffer: der erste gehoert zum abgefragten Host. Mit '[-1]'
        # landete die ID eines anderen Geraets im Eintrag.
        _, appliance_id = mrt._parse_discover_output(self._ns(
            out='applianceCodes: 111 ... applianceCodes: 222 ... '
                '"tokenlist": [{"key": "aa", "token": "bb"}]'))
        self.assertEqual(appliance_id, "111")


class AtomicWriteDetailTests(_ConfigPathMixin):
    """Die beiden Zusagen von _atomic_write_json, die noch ungeprueft waren."""

    def test_the_data_is_forced_to_disk_before_the_rename(self):
        # Ohne fsync kann ein Stromausfall zwischen Schreiben und Umbenennen
        # eine leere devices.json hinterlassen - der Totalverlust aller Tokens.
        with mock.patch("midea_refresh_tokens.os.fsync") as fsync:
            mrt.save_config({"devices": []})
        fsync.assert_called()

    def test_the_temp_file_is_created_in_the_target_directory(self):
        # Nur im SELBEN Verzeichnis ist os.replace atomar (gleiches Dateisystem).
        # Ein Wechsel nach /tmp naehme der Funktion genau ihre Zusage.
        seen = {}
        real_mkstemp = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return real_mkstemp(*args, **kwargs)

        with mock.patch("midea_refresh_tokens.tempfile.mkstemp", side_effect=spy):
            mrt.save_config({"devices": []})
        self.assertEqual(seen.get("dir"), str(self.path.parent))


class UpdateDeviceCallSiteTests(_LangMixin):
    """Die AUFRUFSTELLE von verify_credentials - die einzige Stelle im Projekt,
    an der das (key, token)-Paar positional ausgepackt wird.

    Der RUMPF von verify_credentials ist seit der letzten Runde getestet
    (authenticate(token, key) wird gefangen), die Aufrufstelle war es nicht:
    vertauschte man dort key und token, blieb die gesamte Suite gruen - waehrend
    jeder Kandidat fuer jeden Nutzer dauerhaft scheiterte und sich die Tokens nie
    wieder erneuerten. Deshalb laeuft verify_credentials hier ECHT; ersetzt ist
    nur das AC-Objekt."""

    def setUp(self):
        super().setUp()
        _RecordingAC.instances = []
        module = sys.modules["msmart.device.AC.device"]
        original = module.AirConditioner
        module.AirConditioner = _RecordingAC
        self.addCleanup(lambda: setattr(module, "AirConditioner", original))

    # Port bewusst NICHT der Default 6444: sonst uebersteht ein hartkodierter
    # Wert an der Aufrufstelle den Test unbemerkt.
    DEV = {"name": "W", "ip": "10.0.0.9", "id": 42, "port": 6445}

    def _update(self, dev):
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([("K_SENTINEL", "T_SENTINEL")], None)), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()):
            return mrt.update_device(dev)

    def test_key_and_token_reach_authenticate_in_the_documented_order(self):
        dev = dict(self.DEV)
        self.assertTrue(self._update(dev))
        self.assertEqual(_RecordingAC.instances[0].auth_args,
                         ("T_SENTINEL", "K_SENTINEL"))

    def test_the_verified_pair_is_stored_on_the_right_side(self):
        # Gegenprobe zur Aufrufstelle: ein Tausch, der sich in BEIDEN Richtungen
        # aufhoebe, waere unsichtbar - also auch die Speicherseite pruefen.
        dev = dict(self.DEV)
        self._update(dev)
        self.assertEqual((dev["key"], dev["token"]), ("K_SENTINEL", "T_SENTINEL"))

    def test_address_port_and_id_of_the_entry_are_used(self):
        self._update(dict(self.DEV))
        self.assertEqual(_RecordingAC.instances[0].init_args,
                         ("10.0.0.9", 6445, 42))


class RenderedMessageOrderTests(_LangMixin):
    """Mehr-Platzhalter-Meldungen an ihren ECHTEN Aufrufstellen.

    Eine Aritaetspruefung sieht nur die ANZAHL der Argumente. Vertauscht man
    zwei, entsteht eine syntaktisch einwandfreie, inhaltlich unsinnige Meldung -
    und genau das ueberlebte bislang an neun von zehn Aufrufstellen. Wirksam ist
    nur das Muster, das die ERZEUGTE Meldung ansieht; die frueher hier stehende
    Pruefung rief t() mit von Hand uebergebenen Werten auf und konnte deshalb
    ueber die Aufrufstellen gar nichts aussagen."""

    DEV = {"name": "Wohnzimmer", "ip": "192.168.0.5", "id": 42, "port": 6444}

    def _run_update(self, candidates, results, dev=None, appliance_id=None):
        out = io.StringIO()
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: (candidates, appliance_id)), \
                mock.patch.object(mrt, "verify_credentials", _fake_verify(results)), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(out):
            mrt.update_device(dict(dev) if dev else dict(self.DEV))
        return out.getvalue()

    def _assert_order(self, text, first, second):
        """Vergleicht die ERSTVORKOMMEN beider Teilstuecke im gesamten Text.

        Was dieser Helfer NICHT leistet: er sieht nicht die einzelne Zeile.
        Steht ein Teilstueck schon in einer frueheren, korrekten Zeile, ist die
        Behauptung erfuellt, obwohl die gemeinte Zeile vertauscht sein kann.
        Dagegen helfen nur die Vollzeilen-Zusicherungen weiter unten.

        Auf Erstvorkommen und nicht fortlaufend zu suchen ist Absicht: eine
        fortlaufende Suche findet ein vertauschtes Paar in der naechsten
        gleichartigen Zeile erneut und faellt dadurch nicht mehr auf."""
        self.assertIn(first, text)
        self.assertIn(second, text)
        self.assertLess(text.index(first), text.index(second),
                        f"{first!r} muss vor {second!r} stehen: {text!r}")

    def test_fetching_line_names_the_device_then_the_address(self):
        out = self._run_update([("k", "t")], [(True, "", "")])
        self._assert_order(out, "[Wohnzimmer]", "192.168.0.5")

    def test_candidate_counter_reads_index_of_total(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2"), ("k3", "t3")],
            [(False, mrt.VERIFY_SILENT, "A"), (False, mrt.VERIFY_SILENT, "B"),
             (False, mrt.VERIFY_SILENT, "C")])
        # Vertauscht ergaebe der erste Kandidat "3/1" statt "1/3".
        self.assertIn("1/3", out)
        self.assertIn("3/3", out)
        self.assertNotIn("3/1", out)

    def test_failed_candidate_puts_the_reason_before_the_try_next_suffix(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_SILENT, "GRUND_EINS"),
             (False, mrt.VERIFY_SILENT, "GRUND_ZWEI")])
        self.assertIn(f"GRUND_EINS{mrt.t('dev_try_next')}", out)
        # Nach dem LETZTEN Kandidaten gibt es kein "versuche naechsten".
        self._assert_order(out, "GRUND_EINS", "GRUND_ZWEI")
        self.assertEqual(out.count(mrt.t("dev_try_next")), 1)

    def test_successful_candidate_reports_index_of_total(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2"), ("k3", "t3")],
            [(False, mrt.VERIFY_SILENT, "x"), (True, "", "")])
        self.assertIn("2/3", out)

    def test_id_mismatch_shows_the_stored_value_before_the_cloud_value(self):
        # Ein Tausch kehrte die Aussage um und schickte den Nutzer die falsche
        # ID korrigieren.
        out = self._run_update([("k", "t")], [(True, "", "")],
                               dev=dict(self.DEV, id=111), appliance_id="222")
        self._assert_order(out, "id=111", "id=222")

    def test_total_failure_line_names_the_device_then_the_count(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_SILENT, "x"), (False, mrt.VERIFY_SILENT, "y")])
        self._assert_order(out, "[Wohnzimmer]", " 2 ")

    def test_hint_line_names_the_device_then_the_hint(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_REJECTED, "x"), (False, mrt.VERIFY_REJECTED, "y")])
        hint_start = mrt.t("hint_all_rejected")[:30]
        self._assert_order(out, "[Wohnzimmer] Hint:", hint_start)

    def test_fetch_failure_line_names_the_device_then_the_error(self):
        out = io.StringIO()
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               side_effect=RuntimeError("FEHLERTEXT")), \
                redirect_stdout(out):
            mrt.update_device(dict(self.DEV))
        self._assert_order(out.getvalue(), "[Wohnzimmer]", "FEHLERTEXT")

    # --- vollstaendig gerenderte Zeilen -----------------------------------
    # Die Zusicherungen oben pruefen Bruchstuecke und deren Reihenfolge im
    # Gesamttext; eine korrekte fruehere Zeile kann diese Behauptung erfuellen,
    # waehrend die gemeinte Zeile vertauscht ist. Wirksam ist der Vergleich mit
    # der KOMPLETTEN gerenderten Zeile, aus dem Katalog erzeugt.

    def test_candidates_found_line_renders_name_then_count(self):
        out = self._run_update([("k1", "t1"), ("k2", "t2")], [(True, "", "")])
        self.assertIn(mrt.t("dev_candidates_found", "Wohnzimmer", 2), out)

    def test_total_failure_line_renders_name_then_count(self):
        out = self._run_update(
            [("k1", "t1"), ("k2", "t2")],
            [(False, mrt.VERIFY_SILENT, "x"), (False, mrt.VERIFY_SILENT, "y")])
        self.assertIn(mrt.t("dev_all_failed", "Wohnzimmer", 2), out)

    def test_fetch_failure_line_renders_name_then_error(self):
        out = io.StringIO()
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               side_effect=RuntimeError("FEHLERTEXT")), \
                redirect_stdout(out):
            mrt.update_device(dict(self.DEV))
        self.assertIn(mrt.t("dev_fetch_failed", "Wohnzimmer", "FEHLERTEXT"),
                      out.getvalue())

    def test_discover_exit_message_shows_the_code_then_the_output(self):
        # Vertauscht: "exited with code <Ausgabe>. Last output: 3".
        result = SimpleNamespace(returncode=3, stdout="", stderr="BOOMTAIL")
        with self.assertRaises(RuntimeError) as cm:
            mrt._parse_discover_output(result)
        self._assert_order(str(cm.exception), "3", "BOOMTAIL")


class ConfigMessageOrderTests(_ConfigPathMixin):
    """Dieselbe Pruefung fuer die Meldungen aus load_config und main()."""

    def test_unreadable_config_names_path_then_exception_then_detail(self):
        self.path.write_bytes(b'{"devices": [{"name": "K\xfcche"}]}')
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            mrt.load_config()
        text = err.getvalue()
        self.assertIn(str(self.path), text)
        self.assertIn("UnicodeDecodeError", text)
        self.assertLess(text.index(str(self.path)), text.index("UnicodeDecodeError"))

    def test_skipped_entries_line_counts_first_and_names_the_file_second(self):
        self.path.write_text(json.dumps({"devices": [
            "oops", 123, {"name": "W", "ip": "1.2.3.4", "id": 1}]}), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", lambda dev: True), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit):
                mrt.main()
        text = out.getvalue()
        # Vertauscht: "devices.json unexpected entry/entries in 2 skipped".
        self.assertIn("2 unexpected", text)
        self.assertLess(text.index("2 unexpected"), text.index("devices.json"))

    # Die Fehlerdetails kommen aus einer GESETZTEN Ausnahme statt aus einer
    # echten kaputten Datei: nur so sind Typname und Text bekannt und die
    # Erwartung bleibt ueber Python-Versionen hinweg stabil.
    def test_unreadable_config_line_renders_path_type_and_detail(self):
        self.path.write_text("{}", encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(mrt.json, "load", side_effect=ValueError("KAPUTT")), \
                redirect_stderr(err):
            with self.assertRaises(SystemExit):
                mrt.load_config()
        self.assertIn(mrt.t("cfg_unreadable", mrt.CONFIG_PATH, "ValueError",
                            "KAPUTT"), err.getvalue())

    def test_write_failure_line_renders_type_then_detail(self):
        # Der Schreibfehler ist die letzte Station eines erfolgreichen Laufs:
        # vertauscht meldet er '[Errno ...]: OSError' statt 'OSError: ...'.
        self.path.write_text(json.dumps({"devices": [
            {"name": "W", "ip": "1.2.3.4", "id": 1}]}), encoding="utf-8")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", lambda dev: True), \
                mock.patch.object(mrt, "save_config",
                                  side_effect=OSError("PLATTE VOLL")), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit):
                mrt.main()
        self.assertIn(mrt.t("main_write_failed", "OSError", "PLATTE VOLL"),
                      err.getvalue())


class NoWriteWithoutVerificationTests(_LangMixin):
    """Die Kernzusage des Moduls: bestehende Werte werden NUR nach erfolgreicher
    Verifikation ueberschrieben.

    Der Modul-Docstring verspricht ausdruecklich, dass bei einem Ausfall der
    Cloud-API die zuletzt gueltigen Tokens erhalten bleiben. Diese Zusage war
    ungetestet: verschob man die beiden Zuweisungen aus dem 'if ok:'-Zweig
    heraus, blieb die gesamte Suite gruen - und das Werkzeug zerstoerte die
    letzten funktionierenden Zugangsdaten, waehrend es 'bleiben unveraendert'
    ausgab."""

    GOOD = {"name": "W", "ip": "1.2.3.4", "id": 42, "port": 6444,
            "token": "GUT_TOKEN", "key": "GUT_KEY"}

    def _run(self, results, candidates=None):
        candidates = candidates or [("neu_k1", "neu_t1"), ("neu_k2", "neu_t2")]
        dev = dict(self.GOOD)
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: (candidates, None)), \
                mock.patch.object(mrt, "verify_credentials", _fake_verify(results)), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()) as out:
            ok = mrt.update_device(dev)
        return ok, dev, out.getvalue()

    def test_total_failure_leaves_the_entry_byte_identical(self):
        ok, dev, _ = self._run([(False, mrt.VERIFY_REJECTED, "x"),
                                (False, mrt.VERIFY_SILENT, "y")])
        self.assertFalse(ok)
        self.assertEqual(dev, self.GOOD)

    def test_failed_candidates_leave_no_trace(self):
        # Auch einzeln geprueft, damit die Ursache im Fehlerfall sofort sichtbar
        # ist: kein Kandidatenwert darf in den Eintrag gelangt sein.
        _, dev, _ = self._run([(False, mrt.VERIFY_REJECTED, "x"),
                               (False, mrt.VERIFY_REJECTED, "y")])
        self.assertEqual(dev["token"], "GUT_TOKEN")
        self.assertEqual(dev["key"], "GUT_KEY")

    def test_message_and_reality_agree(self):
        # Die Meldung sagt "bleiben unveraendert" - das muss auch stimmen.
        _, dev, out = self._run([(False, mrt.VERIFY_SILENT, "x"),
                                 (False, mrt.VERIFY_SILENT, "y")])
        self.assertIn("remain unchanged", out)
        self.assertEqual(dev, self.GOOD)

    def test_a_non_standard_port_survives_the_update(self):
        # setdefault, nicht Zuweisung: wer bewusst einen abweichenden Port
        # eingetragen hat, darf ihn nicht bei jedem Token-Abruf verlieren.
        dev = dict(self.GOOD, port=6999)
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([("k", "t")], None)), \
                mock.patch.object(mrt, "verify_credentials",
                                  _fake_verify([(True, "", "")])), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()):
            self.assertTrue(mrt.update_device(dev))
        self.assertEqual(dev["port"], 6999)

    def test_a_missing_port_is_filled_with_the_default(self):
        dev = {k: v for k, v in self.GOOD.items() if k != "port"}
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([("k", "t")], None)), \
                mock.patch.object(mrt, "verify_credentials",
                                  _fake_verify([(True, "", "")])), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()):
            self.assertTrue(mrt.update_device(dev))
        self.assertEqual(dev["port"], 6444)

    def test_only_the_verified_pair_is_written(self):
        # Positivkontrolle: der zweite Kandidat gewinnt -> genau dessen Werte,
        # nicht die des zuvor gescheiterten.
        ok, dev, _ = self._run([(False, mrt.VERIFY_REJECTED, "x"), (True, "", "")])
        self.assertTrue(ok)
        self.assertEqual((dev["key"], dev["token"]), ("neu_k2", "neu_t2"))


class SaveConfigIsCalledTests(_ConfigPathMixin):
    """main() muss devices.json tatsaechlich schreiben.

    Alle main()-Tests ersetzten save_config durch eine Attrappe und prueften nie,
    ob sie ueberhaupt lief - das Werkzeug konnte 'devices.json aktualisiert'
    melden und nichts schreiben."""

    def _run_main(self, argv, update_result=True, devices=None):
        devices = devices if devices is not None else [
            {"name": "W", "ip": "1.2.3.4", "id": 1}]
        self.path.write_text(json.dumps({"devices": devices}), encoding="utf-8")
        saver = mock.MagicMock()
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", lambda dev: update_result), \
                mock.patch.object(mrt, "save_config", saver), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                mock.patch.object(mrt.sys, "argv", ["x"] + argv), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        return cm.exception.code, saver

    def test_save_config_actually_runs(self):
        code, saver = self._run_main(["--all"])
        self.assertEqual(code, 0)
        saver.assert_called_once()

    def test_config_is_still_written_when_a_device_failed(self):
        # Ein Geraetefehler darf die bereits erfolgreichen Aktualisierungen
        # anderer Geraete nicht verwerfen.
        code, saver = self._run_main(["--all"], update_result=False)
        self.assertEqual(code, 2)
        saver.assert_called_once()

    def test_failed_new_device_is_not_persisted(self):
        # Ein neues Geraet, dessen Abruf scheiterte, darf NICHT gespeichert
        # werden - sonst steht ein kaputter Platzhalter in devices.json.
        code, saver = self._run_main(["--name", "Neu", "--host", "9.9.9.9"],
                                     update_result=False, devices=[])
        self.assertEqual(code, 2)
        saver.assert_not_called()


class UpdateDeviceFailureReturnsTests(_LangMixin):
    """Die drei Fehlerpfade von update_device muessen False liefern.

    ExitCodeTests und SaveConfigIsCalledTests ersetzen update_device
    vollstaendig durch eine Attrappe: sie sichern die Abbildung des ERGEBNISSES
    auf den Exit-Code, nie dessen Zustandekommen. 'return False' -> 'return
    True' ueberlebte hier deshalb an drei Stellen - der Cron-Lauf haette Erfolg
    gemeldet, obwohl nichts erreicht wurde, und keine Ueberwachung der Welt
    haette es bemerkt.

    Die benachbarten Fehlerpfade (alle Kandidaten gescheitert) sind laengst
    abgedeckt - das ist derselbe Zwillingsfall: was gerade bearbeitet wurde, ist
    abgesichert, der strukturell gleiche Nachbar nicht."""

    def _update(self, dev, **patches):
        out = io.StringIO()
        with ExitStack() as es:
            for name, replacement in patches.items():
                es.enter_context(mock.patch.object(mrt, name, replacement))
            es.enter_context(mock.patch.object(mrt.time, "sleep", lambda s: None))
            es.enter_context(redirect_stdout(out))
            result = mrt.update_device(dev)
        return result, out.getvalue()

    def test_missing_ip_is_a_failure(self):
        result, out = self._update({"name": "W"})
        self.assertFalse(result)
        self.assertIn("No IP address", out)

    def test_failed_token_fetch_is_a_failure(self):
        def _boom(host):
            raise RuntimeError("cloud down")

        result, out = self._update({"name": "W", "ip": "1.2.3.4", "id": 1},
                                   fetch_candidate_credentials=_boom)
        self.assertFalse(result)
        self.assertIn("cloud down", out)

    def test_missing_device_id_is_a_failure(self):
        # Kandidaten da, aber weder in devices.json noch von der Cloud eine ID:
        # ohne ID laesst sich keine Verbindung aufbauen, es darf also kein
        # Erfolg gemeldet und nichts gespeichert werden.
        dev = {"name": "W", "ip": "1.2.3.4"}
        result, out = self._update(
            dev, fetch_candidate_credentials=lambda host: ([("k", "t")], None))
        self.assertFalse(result)
        self.assertIn("No device ID", out)
        self.assertNotIn("token", dev)


class ExitCodeFromTheRealUpdateTests(_ConfigPathMixin):
    """Exit-Code 2 EINMAL mit der echten update_device statt einer Attrappe.

    Erst diese Kombination belegt, dass ein Geraetefehler tatsaechlich bis zum
    Exit-Code durchschlaegt: die uebrigen Exit-Code-Tests geben das Ergebnis
    selbst vor."""

    def test_a_real_device_failure_yields_exit_2(self):
        self.path.write_text(
            json.dumps({"devices": [{"name": "W", "ip": "1.2.3.4", "id": 1}]}),
            encoding="utf-8")

        def _boom(host):
            raise RuntimeError("cloud down")

        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "fetch_candidate_credentials", _boom), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        self.assertEqual(cm.exception.code, 2)


class ExitCodeTests(_ConfigPathMixin):
    """Exit-Codes sind die Schnittstelle zu Cron und Monitoring - ein stiller
    Erfolg bei kaputtem Geraet macht jede Ueberwachung wertlos. Sie waren in
    beiden Werkzeugen ungetestet."""

    def _run_main(self, argv, devices, update_result=True, save_raises=None):
        self.path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

        def _save(cfg):
            if save_raises is not None:
                raise save_raises

        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", lambda dev: update_result), \
                mock.patch.object(mrt, "save_config", _save), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                mock.patch.object(mrt.sys, "argv", ["x"] + argv), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        return cm.exception.code

    def test_success_is_zero(self):
        self.assertEqual(self._run_main(
            ["--all"], [{"name": "W", "ip": "1.2.3.4", "id": 1}]), 0)

    def test_device_failure_is_two(self):
        self.assertEqual(self._run_main(
            ["--all"], [{"name": "W", "ip": "1.2.3.4", "id": 1}],
            update_result=False), 2)

    def test_empty_all_is_one(self):
        self.assertEqual(self._run_main(["--all"], []), 1)

    def test_new_device_without_host_is_one(self):
        self.assertEqual(self._run_main(["--name", "Neu"], []), 1)

    def test_write_failure_is_one(self):
        self.assertEqual(self._run_main(
            ["--all"], [{"name": "W", "ip": "1.2.3.4", "id": 1}],
            save_raises=OSError("read-only")), 1)


class CandidatePacingOrderTests(_LangMixin):
    """Die Pause muss VOR dem naechsten Versuch liegen, nicht danach.

    Ein reiner Zaehltest kann das nicht unterscheiden: 'Pause vor dem Versuch'
    und 'Pause nach dem Versuch' ergeben bei drei Kandidaten beide genau zwei
    Pausen. Verschiebt man sie hinter den Versuch, folgt Kandidat 2 aber
    unmittelbar auf Kandidat 1 - also ohne die Entzerrung, um die es geht.
    Deshalb wird hier die REIHENFOLGE der Ereignisse gepruft."""

    def _event_log(self, count):
        events = []
        seq = [(False, mrt.VERIFY_SILENT, "x")] * count

        async def _verify(ip, port, device_id, key, token):
            events.append("versuch")
            return seq.pop(0)

        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([(f"k{i}", f"t{i}") for i in range(count)], None)), \
                mock.patch.object(mrt, "verify_credentials", _verify), \
                mock.patch.object(mrt.time, "sleep",
                                  lambda s: events.append("pause")), \
                redirect_stdout(io.StringIO()):
            mrt.update_device({"name": "W", "ip": "1.2.3.4", "id": 1})
        return events

    def test_pause_sits_between_attempts(self):
        self.assertEqual(self._event_log(3),
                         ["versuch", "pause", "versuch", "pause", "versuch"])

    def test_no_pause_before_the_first_or_after_the_last(self):
        events = self._event_log(2)
        self.assertEqual(events[0], "versuch")
        self.assertEqual(events[-1], "versuch")
        self.assertEqual(events, ["versuch", "pause", "versuch"])


class DevicePacingOrderTests(_ConfigPathMixin):
    """Zwilling zu CandidatePacingOrderTests: auch die Pause ZWISCHEN GERAETEN
    muss VOR dem naechsten Zugriff liegen.

    DeviceDelayTests unten zaehlt nur (slept == [DEVICE_DELAY] * 2) - verschiebt
    man den sleep HINTER den Aufruf, bleibt das gruen, obwohl Geraet 2 dann
    unmittelbar auf Geraet 1 folgt und die Entzerrung damit wirkungslos ist.
    Genau dieser Unterschied war fuer die Kandidatenpause bereits geloest; der
    Zwilling hier blieb zurueck."""

    def _events(self, count):
        self.path.write_text(json.dumps({"devices": [
            {"name": f"D{i}", "ip": f"1.2.3.{i}", "id": i} for i in range(count)
        ]}), encoding="utf-8")
        events = []

        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device",
                                  lambda dev: events.append(f"geraet {dev['name']}")
                                  or True), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.time, "sleep",
                                  lambda s: events.append(f"pause {s}")), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                mrt.main()
        return events

    def test_the_pause_sits_between_two_devices(self):
        pause = f"pause {mrt.DEVICE_DELAY}"
        self.assertEqual(self._events(3),
                         ["geraet D0", pause, "geraet D1", pause, "geraet D2"])

    def test_no_pause_before_the_first_or_after_the_last(self):
        events = self._events(2)
        self.assertEqual(events[0], "geraet D0")
        self.assertEqual(events[-1], "geraet D1")


class DeviceDelayTests(_ConfigPathMixin):
    """Die Pause ZWISCHEN GERAETEN (DEVICE_DELAY) war bisher voellig ungetestet -
    sie liess sich ersatzlos entfernen, ohne dass die Suite es bemerkt haette."""

    def _run_main(self, device_count):
        self.path.write_text(json.dumps({"devices": [
            {"name": f"D{i}", "ip": f"1.2.3.{i}", "id": i} for i in range(device_count)
        ]}), encoding="utf-8")
        slept = []
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device", lambda dev: True), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.time, "sleep", lambda s: slept.append(s)), \
                mock.patch.object(mrt.sys, "argv", ["x", "--all"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        return cm.exception.code, slept

    def test_pause_between_devices_but_not_before_the_first(self):
        code, slept = self._run_main(3)
        self.assertEqual(code, 0)
        # Drei Geraete -> genau ZWEI Pausen.
        self.assertEqual(slept, [mrt.DEVICE_DELAY] * 2)

    def test_single_device_never_pauses(self):
        code, slept = self._run_main(1)
        self.assertEqual(code, 0)
        self.assertEqual(slept, [])


class ConfigPathAnchorTests(unittest.TestCase):
    """devices.json wird am MODULVERZEICHNIS aufgehaengt, nicht am cwd.

    Beide Werkzeuge laufen aus Cron und aus dem Wrapper heraus mit voellig
    beliebigem Arbeitsverzeichnis. Ein relativer Pfad liesse den Cron-Lauf eine
    leere Konfiguration lesen - und im Fall des Token-Abrufs sogar eine ZWEITE
    devices.json irgendwo anlegen, waehrend die echte veraltet."""

    def test_both_tools_anchor_the_config_at_their_own_directory(self):
        for module in (mrt, mie):
            with self.subTest(module=module.__name__):
                # Die Mixins pinnen CONFIG_PATH auf ein Temp-Verzeichnis; hier
                # zaehlt der Wert aus dem Quelltext, also der beim Import.
                path = Path(module.__file__).resolve().parent / "devices.json"
                self.assertTrue(path.is_absolute())
                self.assertEqual(
                    (REPO_DIR / "devices.json").resolve(), path,
                    f"{module.__name__}: CONFIG_PATH muss am Modulverzeichnis haengen")

    def test_the_config_path_constant_itself_is_absolute(self):
        # Gegenprobe auf die Konstante selbst, ohne den Umweg ueber __file__.
        for source in ("midea_refresh_tokens.py", "midea_ieco_ensure.py"):
            with self.subTest(source=source):
                text = (REPO_DIR / source).read_text(encoding="utf-8")
                self.assertIn('CONFIG_PATH = Path(__file__).parent / "devices.json"',
                              text)


class NewDeviceAndInputTests(_ConfigPathMixin):
    """Kleinere, aber verifizierte Luecken rund um main() und die Extraktion."""

    def test_the_host_argument_lands_in_the_new_entry(self):
        # Ohne --host-Uebernahme entstuende ein Eintrag ohne IP - update_device
        # braeche sofort ab, und der Nutzer saehe nicht, warum.
        self.path.write_text('{"devices": []}', encoding="utf-8")
        seen = []
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"}), \
                mock.patch.dict(sys.modules, {"msmart": mock.MagicMock()}), \
                mock.patch.object(mrt, "update_device",
                                  lambda dev: seen.append(dict(dev)) or True), \
                mock.patch.object(mrt, "save_config", lambda cfg: None), \
                mock.patch.object(mrt.sys, "argv",
                                  ["x", "--name", "Neu", "--host", "9.9.9.9"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mrt.main()
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(seen[0]["ip"], "9.9.9.9")
        self.assertEqual(seen[0]["name"], "Neu")

    def test_calling_without_a_target_is_a_usage_error(self):
        # Die argparse-Gruppe ist required=True: ohne --all/--name soll das
        # Werkzeug NICHT stillschweigend nichts tun, sondern die Nutzung zeigen.
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "midea_refresh_tokens.py")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", (result.stdout + result.stderr).lower())

    def test_a_non_hex_key_is_not_accepted_as_a_candidate(self):
        # key/token sind Hex-Strings. Eine laxere Regex uebernaehme Platzhalter
        # wie "null" aus der Cloud-Antwort und liefe in eine Verifikation, die
        # nur scheitern kann.
        self.assertEqual(mrt.extract_token_key_pairs(
            '"tokenlist": [{"key": "zzzz", "token": "aabb"}]'), [])
        self.assertEqual(mrt.extract_token_key_pairs(
            '"tokenlist": [{"key": "aabb", "token": "nono"}]'), [])

    def test_an_id_mismatch_never_overwrites_the_stored_value(self):
        # Die Meldung sagt "Existing value NOT overwritten" - das muss auch
        # stimmen: eine falsche ID macht das Geraet dauerhaft unerreichbar.
        dev = {"name": "W", "ip": "1.2.3.4", "id": 111}
        with mock.patch.object(mrt, "fetch_candidate_credentials",
                               lambda host: ([("k", "t")], "222")), \
                mock.patch.object(mrt, "verify_credentials",
                                  _fake_verify([(True, "", "")])), \
                mock.patch.object(mrt.time, "sleep", lambda s: None), \
                redirect_stdout(io.StringIO()):
            mrt.update_device(dev)
        self.assertEqual(dev["id"], 111)


class VerifyTimeoutHeadroomTests(unittest.TestCase):
    """#2: Der eigene Deckel darf msmart-ngs interne Wiederholungslogik nicht
    abschneiden - sonst wird ein noch laufender Versuch als 'Zeitlimit'
    fehlgedeutet. msmart-ng 2026.7.0 braucht im Fehlerfall bis zu
    5s (connect) + 3 x 2s (Lesetimeout) = 11s."""

    def test_timeout_exceeds_msmart_worst_case(self):
        msmart_worst_case = 5 + 3 * 2
        self.assertGreater(mrt.VERIFY_TIMEOUT, msmart_worst_case)


if __name__ == "__main__":
    unittest.main()
