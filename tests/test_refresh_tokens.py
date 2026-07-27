#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_refresh_tokens.py (stdlib unittest, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_refresh_tokens  (aus dem Repo-Root)
oder direkt: python3 tests/test_refresh_tokens.py
"""
import ast
import io
import json
import os
import re
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
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_discover_timeout")

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

    def test_mkdtemp_failure_becomes_runtimeerror(self):
        # Temp-Verzeichnis nicht anlegbar (z.B. voller Datentraeger) -> klarer
        # RuntimeError statt rohem OSError-Traceback.
        with mock.patch("midea_refresh_tokens.tempfile.mkdtemp",
                        side_effect=OSError("no space")):
            with self.assertRaises(RuntimeError) as cm:
                mrt.fetch_candidate_credentials("1.2.3.4")
        self._assert_message(cm, "err_tempdir")

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

    def test_denmark_locale_is_not_mistaken_for_german(self):
        # Gegenprobe zum Praefix-Vergleich: 'da_DK' beginnt nicht mit 'de',
        # darf also nicht als Deutsch durchgehen.
        self.assertEqual(self._resolve(LANG="da_DK.UTF-8"), "en")


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
            self.assertGreater(checked, 20, f"zu wenige t()-Aufrufe in {path.name} gefunden")

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

    def test_marker_match_is_case_insensitive(self):
        self.assertEqual(self._classify("ERROR PACKET RECEIVED.")[0], mrt.VERIFY_REJECTED)


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

    def test_empty_and_unclassifiable_yield_no_invented_hint(self):
        self.assertIsNone(mrt.summarize_failure_hint([]))
        self.assertIsNone(mrt.summarize_failure_hint([mrt.VERIFY_OTHER]))


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
