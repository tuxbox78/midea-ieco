# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_devices.py - den nicht-verlierenden Merge-Kern hinter dem
--reconfigure-Backup und der Token-Uebernahme des Installers. Deckt die
CORRECT-Matrix ab (Conformance, Ordering, Range, Reference, Existence, Cardinality)
und insbesondere den Teilverlust-Fall, der den urspruenglichen Datenverlust-Bug
ausloeste. Bewusst OHNE Hardware/venv - reine stdlib, per `unittest discover`
auffindbar."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest

# midea_devices.py liegt im Repo-Wurzelverzeichnis (ein Verzeichnis ueber tests/);
# run_all.sh ruft unittest aus der Wurzel auf, ein Direktaufruf aus tests/ nicht -
# den Import daher lageunabhaengig absichern.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import midea_devices as md  # noqa: E402


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestLoadDevices(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str = "devices.json") -> str:
        return os.path.join(self.dir, name)

    def test_valid(self) -> None:
        p = self._path()
        _write(p, '{"devices":[{"name":"A","ip":"1.2.3.4","id":1,"token":"t","key":"k"}]}')
        self.assertEqual(len(md.load_devices(p)), 1)

    def test_missing_file(self) -> None:
        self.assertEqual(md.load_devices(self._path("nope.json")), [])

    def test_broken_json(self) -> None:
        p = self._path()
        _write(p, "{not json")
        self.assertEqual(md.load_devices(p), [])

    def test_devices_null(self) -> None:
        p = self._path()
        _write(p, '{"devices":null}')
        self.assertEqual(md.load_devices(p), [])

    def test_devices_not_array(self) -> None:
        p = self._path()
        _write(p, '{"devices":42}')
        self.assertEqual(md.load_devices(p), [])

    def test_root_not_object(self) -> None:
        p = self._path()
        _write(p, "[1,2,3]")
        self.assertEqual(md.load_devices(p), [])

    def test_non_dict_entries_filtered(self) -> None:
        p = self._path()
        _write(p, '{"devices":[{"id":1},"junk",5,null,{"id":2}]}')
        self.assertEqual(len(md.load_devices(p)), 2)


class TestParseDeviceId(unittest.TestCase):
    def test_int(self) -> None:
        self.assertEqual(md.parse_device_id(12345), "12345")

    def test_str(self) -> None:
        self.assertEqual(md.parse_device_id("12345"), "12345")

    def test_leading_zero_normalised(self) -> None:
        self.assertEqual(md.parse_device_id("007"), "7")

    def test_whitespace(self) -> None:
        self.assertEqual(md.parse_device_id("  42 "), "42")

    def test_unicode_digit_rejected(self) -> None:
        # hochgestellte 5 (U+2075): isdigit()-wahr, aber int() -> ValueError
        self.assertIsNone(md.parse_device_id("⁵"))

    def test_non_numeric(self) -> None:
        self.assertIsNone(md.parse_device_id("abc"))

    def test_none(self) -> None:
        self.assertIsNone(md.parse_device_id(None))

    def test_empty(self) -> None:
        self.assertIsNone(md.parse_device_id(""))


class TestParsePort(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(md.parse_port(6455), 6455)

    def test_valid_string(self) -> None:
        self.assertEqual(md.parse_port("6444"), 6444)

    def test_zero(self) -> None:
        self.assertIsNone(md.parse_port(0))

    def test_too_high(self) -> None:
        self.assertIsNone(md.parse_port(65536))

    def test_negative(self) -> None:
        self.assertIsNone(md.parse_port(-1))

    def test_garbage_string(self) -> None:
        self.assertIsNone(md.parse_port("64o4"))

    def test_none(self) -> None:
        self.assertIsNone(md.parse_port(None))

    def test_bool_true_not_port_one(self) -> None:
        self.assertIsNone(md.parse_port(True))

    def test_bool_false(self) -> None:
        self.assertIsNone(md.parse_port(False))

    def test_infinity_no_overflow(self) -> None:
        # Pythons json liest das blanke Literal Infinity als float('inf');
        # int(inf) -> OverflowError (kein ValueError). Muss auf None fallen.
        self.assertIsNone(md.parse_port(float("inf")))


class TestBestPair(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(md.best_pair([]), ("", "", 6444))

    def test_single_complete(self) -> None:
        self.assertEqual(
            md.best_pair([{"token": "t", "key": "k", "port": 6455}]),
            ("t", "k", 6455),
        )

    def test_precedence_first_complete_wins(self) -> None:
        recs = [{"token": "NEW", "key": "NK"}, {"token": "OLD", "key": "OK"}]
        self.assertEqual(md.best_pair(recs)[:2], ("NEW", "NK"))

    def test_empty_current_falls_back_to_second(self) -> None:
        recs = [{"token": "", "key": ""}, {"token": "SAVED", "key": "SK"}]
        self.assertEqual(md.best_pair(recs)[:2], ("SAVED", "SK"))

    def test_partial_pair_not_taken(self) -> None:
        # token ohne key zaehlt nicht als vollstaendig
        recs = [{"token": "t", "key": ""}, {"token": "T2", "key": "K2"}]
        self.assertEqual(md.best_pair(recs)[:2], ("T2", "K2"))

    def test_port_independent_of_token(self) -> None:
        # erster Record hat gueltigen Port aber kein Token; zweiter hat das Token
        recs = [{"token": "", "key": "", "port": 6455},
                {"token": "T", "key": "K", "port": 6444}]
        self.assertEqual(md.best_pair(recs), ("T", "K", 6455))

    def test_port_default_when_all_invalid(self) -> None:
        recs = [{"token": "T", "key": "K", "port": 999999}]
        self.assertEqual(md.best_pair(recs)[2], 6444)


class TestCredentialIndex(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mk(self, name: str, text: str) -> str:
        p = os.path.join(self.dir, name)
        _write(p, text)
        return p

    def test_partial_loss_recovered_from_bak(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"id":11111,"token":"ATOK","key":"AKEY"},'
                       '{"id":22222,"token":"","key":""}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"id":11111,"token":"ATOK","key":"AKEY"},'
                       '{"id":22222,"token":"BTOK","key":"BKEY"}]}')
        idx = md.credential_index([cur, bak])
        self.assertEqual(idx["22222"][:2], ("BTOK", "BKEY"))
        self.assertEqual(idx["11111"][:2], ("ATOK", "AKEY"))

    def test_current_precedence(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"id":1,"token":"NEW","key":"NK"}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"id":1,"token":"OLD","key":"OK"}]}')
        self.assertEqual(md.credential_index([cur, bak])["1"][:2], ("NEW", "NK"))

    def test_full_triple_incl_port_restored_from_bak(self) -> None:
        # current ist token- UND portlos, die .bak haelt beides: nicht nur token/key,
        # sondern auch der id-verankerte Port wird aus der .bak mit-restauriert.
        cur = self._mk("devices.json", '{"devices":[{"id":7,"token":"","key":""}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"id":7,"token":"T","key":"K","port":8899}]}')
        self.assertEqual(md.credential_index([cur, bak])["7"], ("T", "K", 8899))

    def test_id_only_in_bak(self) -> None:
        cur = self._mk("devices.json", '{"devices":[]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"id":9,"token":"T","key":"K"}]}')
        self.assertEqual(md.credential_index([cur, bak])["9"][:2], ("T", "K"))

    def test_broken_id_skipped(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"id":1,"token":"T","key":"K"},'
                       '{"id":"⁵","token":"X","key":"Y"}]}')
        idx = md.credential_index([cur])
        self.assertIn("1", idx)
        self.assertEqual(len(idx), 1)

    def test_missing_files(self) -> None:
        self.assertEqual(md.credential_index([self._mk("a", "{bad") ,
                                              os.path.join(self.dir, "nope")]), {})


class TestMergeDevices(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mk(self, name: str, text: str) -> str:
        p = os.path.join(self.dir, name)
        _write(p, text)
        return p

    def _by_id(self, devices: list[dict]) -> dict:
        return {str(d["id"]): d for d in devices}

    def test_partial_loss_bak_not_impoverished(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"A","ip":"1.1.1.1","id":11111,"token":"ATOK","key":"AKEY"},'
                       '{"name":"B","ip":"2.2.2.2","id":22222,"token":"","key":""}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"name":"A","ip":"1.1.1.1","id":11111,"token":"ATOK","key":"AKEY"},'
                       '{"name":"B","ip":"2.2.2.2","id":22222,"token":"BTOK","key":"BKEY"}]}')
        merged = self._by_id(md.merge_devices(cur, bak))
        self.assertEqual(merged["22222"]["token"], "BTOK")
        self.assertEqual(merged["11111"]["token"], "ATOK")

    def test_device_only_in_secondary_survives(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"A","ip":"1.1.1.1","id":1,"token":"AT","key":"AK"}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"name":"A","ip":"1.1.1.1","id":1,"token":"AT","key":"AK"},'
                       '{"name":"Gone","ip":"9.9.9.9","id":2,"token":"GT","key":"GK"}]}')
        merged = self._by_id(md.merge_devices(cur, bak))
        self.assertIn("2", merged)
        self.assertEqual(merged["2"]["token"], "GT")

    def test_deterministic_order_primary_first(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"A","ip":"1","id":10,"token":"t","key":"k"}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"name":"C","ip":"3","id":30,"token":"t","key":"k"},'
                       '{"name":"A","ip":"1","id":10,"token":"t","key":"k"}]}')
        ids = [d["id"] for d in md.merge_devices(cur, bak)]
        self.assertEqual(ids, [10, 30])   # primary-id zuerst, dann secondary-only

    def test_name_ip_from_primary(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"NEU","ip":"1.1.1.1","id":1,"token":"","key":""}]}')
        bak = self._mk("devices.json.bak",
                       '{"devices":[{"name":"ALT","ip":"9.9.9.9","id":1,"token":"T","key":"K"}]}')
        merged = self._by_id(md.merge_devices(cur, bak))
        self.assertEqual(merged["1"]["name"], "NEU")   # name/ip aus primary
        self.assertEqual(merged["1"]["ip"], "1.1.1.1")
        self.assertEqual(merged["1"]["token"], "T")    # token via best_pair aus bak

    def test_infinity_port_does_not_crash(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"A","ip":"1","id":1,"port":Infinity,"token":"t","key":"k"}]}')
        merged = self._by_id(md.merge_devices(cur, os.path.join(self.dir, "nope")))
        self.assertEqual(merged["1"]["port"], 6444)

    def test_id_type_is_int(self) -> None:
        cur = self._mk("devices.json",
                       '{"devices":[{"name":"A","ip":"1","id":"12345","token":"t","key":"k"}]}')
        merged = md.merge_devices(cur, os.path.join(self.dir, "nope"))
        self.assertIsInstance(merged[0]["id"], int)


class TestAtomicWriteDevices(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_writes_valid_json(self) -> None:
        p = os.path.join(self.dir, "devices.json")
        md.atomic_write_devices(p, [{"name": "A", "ip": "1", "port": 6444,
                                     "id": 1, "token": "t", "key": "k"}])
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["devices"][0]["token"], "t")

    def test_mode_0600(self) -> None:
        p = os.path.join(self.dir, "devices.json")
        md.atomic_write_devices(p, [])
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_overwrites_existing(self) -> None:
        p = os.path.join(self.dir, "devices.json")
        _write(p, '{"devices":[{"old":1}]}')
        md.atomic_write_devices(p, [{"id": 2}])
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["devices"], [{"id": 2}])

    def test_no_tmp_leftover(self) -> None:
        p = os.path.join(self.dir, "devices.json")
        md.atomic_write_devices(p, [])
        leftovers = [n for n in os.listdir(self.dir) if n != "devices.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
