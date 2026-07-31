#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Unit-Tests fuer midea_apply.py (stdlib unittest, keine Hardware).

Ausfuehren: python3 -m unittest tests.test_apply  (aus dem Repo-Root)

Zwei Schichten:

1. Verhalten (ApplyIecoOnlyTests): an formtreuen Attrappen, die NUR die private
   Property-Write-Kette nachbilden - bewusst OHNE apply()/SetStateCommand, damit
   ein Voll-Zustands-Write an der Attrappe gar nicht moeglich ist. Genau das ist
   die Zusage: der reine Property-Write kann power/temp/mode nicht ueberschreiben.
2. Abhaengigkeitsvertrag (MsmartPropertyContractTests): die private Kette
   (device._apply_properties als Koroutine, _PROPERTY_MAP mit PropertyId.IECO,
   die Arm-Kette device.ieco=True -> _updated_properties) existiert in der ECHTEN
   Bibliothek. Laeuft im SUBPROZESS, weil die uebrige Suite msmart stubbt.
"""
import inspect
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import midea_apply  # noqa: E402

_LANG_PATCHER = None


def setUpModule():
    global _LANG_PATCHER
    _LANG_PATCHER = mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "en"})
    _LANG_PATCHER.start()


def tearDownModule():
    if _LANG_PATCHER is not None:
        _LANG_PATCHER.stop()


# --------------------------------------------------------------------------
# 1. Verhalten
# --------------------------------------------------------------------------

class _PropOnlyDevice:
    """Formtreue Attrappe: bildet NUR die Kette nach, die apply_ieco_only
    braucht (_apply_properties/_PROPERTY_MAP/_updated_properties). KEIN apply(),
    kein SetStateCommand - an dieser Attrappe ist ein Voll-Zustands-Write gar
    nicht moeglich, was die strukturelle Zusage des Moduls widerspiegelt.

    Der Property-Schluessel ist ein Sentinel-String statt einer echten
    PropertyId: in dieser Suite ist msmart gestubbt, und apply_ieco_only nutzt
    ausschliesslich die geraeteeigenen Member - ein echter PropertyId-Import
    findet also gar nicht statt."""

    IECO = "IECO"
    #: Eine weitere property-basierte Groesse, die NICHT scharfgeschaltet ist -
    #: damit laesst sich zusichern, dass apply_ieco_only nur die ARMIERTEN
    #: Properties sendet und nicht wahllos den ganzen _PROPERTY_MAP.
    OTHER = "OTHER"

    def __init__(self, *, ieco_number=1, ieco=True, arm_ieco=True,
                 apply_raises=None):
        self._ieco_number = ieco_number
        self._ieco = ieco
        self._other = 7
        self._PROPERTY_MAP = {
            self.IECO: lambda s: (s._ieco_number, s._ieco),
            self.OTHER: lambda s: s._other,
        }
        self._updated_properties = {self.IECO} if arm_ieco else set()
        self._apply_raises = apply_raises
        #: Liste der an _apply_properties uebergebenen Property-Dikts.
        self.applied = []

    async def _apply_properties(self, properties):
        self.applied.append(dict(properties))
        if self._apply_raises is not None:
            raise self._apply_raises


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class ApplyIecoOnlyTests(unittest.TestCase):

    def test_sends_only_the_armed_ieco_property_and_returns_true(self):
        device = _PropOnlyDevice(ieco_number=3, ieco=True)
        self.assertTrue(_run(midea_apply.apply_ieco_only(device)))
        # Genau EIN Property-Write, und darin AUSSCHLIESSLICH iECO - die nicht
        # scharfgeschaltete OTHER-Property ist NICHT dabei, power/temp/mode gibt
        # es an dieser Attrappe ohnehin nicht.
        self.assertEqual(device.applied, [{device.IECO: (3, True)}])

    def test_does_not_send_unarmed_properties(self):
        device = _PropOnlyDevice()
        _run(midea_apply.apply_ieco_only(device))
        (sent,) = device.applied
        self.assertNotIn(device.OTHER, sent)

    def test_network_error_from_the_write_propagates(self):
        # Ein Sendefehler ist KEIN struktureller Ausfall: er wird durchgereicht,
        # damit die Retry-Schleife in ensure_ieco ihn wie einen fehlgeschlagenen
        # apply() behandelt (schliessen, neu verbinden, neu validieren).
        device = _PropOnlyDevice(apply_raises=OSError("Socket weg"))
        with self.assertRaises(OSError):
            _run(midea_apply.apply_ieco_only(device))

    def test_missing_apply_properties_falls_back_and_warns(self):
        class NoApplyProps:
            _PROPERTY_MAP = {"IECO": lambda s: (1, True)}
            _updated_properties = {"IECO"}

        with self.assertLogs(midea_apply.__name__, level="WARNING") as logs:
            self.assertFalse(_run(midea_apply.apply_ieco_only(NoApplyProps())))
        self.assertIn("_apply_properties", logs.output[0])

    def test_missing_property_map_falls_back_and_warns(self):
        class NoMap:
            async def _apply_properties(self, properties):
                pass
            _updated_properties = {"IECO"}

        with self.assertLogs(midea_apply.__name__, level="WARNING") as logs:
            self.assertFalse(_run(midea_apply.apply_ieco_only(NoMap())))
        self.assertIn("_PROPERTY_MAP", logs.output[0])

    def test_a_synchronous_apply_properties_is_refused_instead_of_awaited(self):
        # Wuerde msmart _apply_properties synchron machen, liefe ein await ins
        # Leere. Lieber laut zurueckfallen, statt still nichts zu senden.
        class SyncApplyProps:
            _PROPERTY_MAP = {"IECO": lambda s: (1, True)}
            _updated_properties = {"IECO"}

            def _apply_properties(self, properties):  # KEINE Koroutine
                pass

        with self.assertLogs(midea_apply.__name__, level="WARNING") as logs:
            self.assertFalse(_run(midea_apply.apply_ieco_only(SyncApplyProps())))
        self.assertIn("coroutine", logs.output[0].lower())

    def test_nothing_property_writable_armed_falls_back_without_warning(self):
        # Kein Warnfall (die Bibliothek ist in Ordnung), sondern schlicht: es ist
        # keine property-basierte Aenderung scharfgeschaltet -> der Aufrufer
        # nimmt den vollen apply()-Weg. Kein Property-Write darf abgehen.
        device = _PropOnlyDevice(arm_ieco=False)
        with self.assertNoLogs(midea_apply.__name__, level="WARNING"):
            self.assertFalse(_run(midea_apply.apply_ieco_only(device)))
        self.assertEqual(device.applied, [])


# --------------------------------------------------------------------------
# 2. Abhaengigkeitsvertrag gegen die ECHTE Bibliothek
# --------------------------------------------------------------------------

_CONTRACT_PROBE = r"""
import asyncio, inspect, json, sys
try:
    from msmart.device.AC.device import AirConditioner
    from msmart.device.AC.command import PropertyId, SetStateCommand
    from importlib.metadata import version
except Exception as exc:
    print(json.dumps({"available": False, "reason": str(exc)}))
    sys.exit(0)

device = AirConditioner(ip="192.0.2.1", port=6444, device_id=1)
apply_props = getattr(device, "_apply_properties", None)
prop_map = getattr(device, "_PROPERTY_MAP", None)
ieco = getattr(PropertyId, "IECO", None)

# Die Arm-Kette: device.ieco = True MUSS PropertyId.IECO in _updated_properties
# fuehren - sonst haette apply_ieco_only nichts zu senden.
arm_error = None
try:
    device.ieco = True
except Exception as exc:
    arm_error = "%s: %s" % (type(exc).__name__, exc)
armed = bool(ieco is not None
             and ieco in getattr(device, "_updated_properties", set()))

# Der Wertbauer aus _PROPERTY_MAP: fuer IECO ein 2-Tupel (ieco_number, bool).
ieco_value_is_2_tuple = False
try:
    if isinstance(prop_map, dict) and ieco in prop_map:
        v = prop_map[ieco](device)
        ieco_value_is_2_tuple = isinstance(v, tuple) and len(v) == 2
except Exception:
    pass

# iSense heisst im Protokoll und in msmart-ng "Follow Me". Es ist KEINE
# Property, sondern ein Feld im vollen SetStateCommand. Die Sonde prueft die
# oeffentliche Setter->apply()->Command-Kette, auf die --ensure-isense baut,
# ohne ein Netzwerk oder ein echtes Geraet zu benoetigen.
follow_me_property_exists = isinstance(
    getattr(AirConditioner, "follow_me", None), property)
follow_me_set_error = None
follow_me_command_value = None
sent_commands = []

async def capture_commands(commands):
    sent_commands.extend(commands if isinstance(commands, list) else [commands])
    return []

try:
    device.follow_me = True
    device._send_commands_get_responses = capture_commands
    asyncio.run(device.apply())
    state_commands = [c for c in sent_commands if isinstance(c, SetStateCommand)]
    if state_commands:
        follow_me_command_value = state_commands[0].follow_me
except Exception as exc:
    follow_me_set_error = "%s: %s" % (type(exc).__name__, exc)

print(json.dumps({
    "available": True,
    "version": version("msmart-ng"),
    "apply_properties_callable": callable(apply_props),
    "apply_properties_is_coroutine":
        inspect.iscoroutinefunction(apply_props) if apply_props else None,
    "prop_map_is_dict": isinstance(prop_map, dict),
    "updated_properties_present":
        getattr(device, "_updated_properties", None) is not None,
    "ieco_property_exists": ieco is not None,
    "ieco_in_prop_map": bool(isinstance(prop_map, dict) and ieco in prop_map),
    "arm_error": arm_error,
    "ieco_armed_by_setter": armed,
    "ieco_value_is_2_tuple": ieco_value_is_2_tuple,
    "follow_me_property_exists": follow_me_property_exists,
    "follow_me_set_error": follow_me_set_error,
    "follow_me_command_value": follow_me_command_value,
}))
"""


class MsmartPropertyContractTests(unittest.TestCase):
    """Pinnt die PRIVATE Property-Write-Kette, auf der midea_apply beruht.

    Schlaegt dieser Test nach einem Versions-Sprung fehl, faellt apply_ieco_only
    auf ein volles apply() zurueck (kein Schaden, dank Reconcile), aber die
    strukturelle Clobber-Freiheit des --only-if-on-Nudge ist dahin - dann ist
    die Kette nachzuziehen.

    Ist msmart-ng nicht installiert (Normalfall auf dem Entwicklerrechner), wird
    ehrlich uebersprungen; die CI fuehrt den Vertrag im Job 'install-smoke' mit
    den echten Abhaengigkeiten aus. Gleiche Strenge-Regeln wie in
    tests/test_conn.py: ein Absturz der Sonde ist ein Befund, und unter
    STRICT_ENV ist Ueberspringen verboten."""

    STRICT_ENV = "MIDEA_IECO_REQUIRE_MSMART"

    @classmethod
    def _refuse(cls, reason: str):
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
                "msmart-ng ist nicht installiert - der Vertrag laeuft im "
                "CI-Job 'install-smoke'. Grund der Sonde: "
                f"{cls.probe.get('reason', '(keiner)')}")

    def test_apply_properties_exists_and_is_a_coroutine(self):
        self.assertTrue(self.probe["apply_properties_callable"],
                        "device._apply_properties fehlt oder ist nicht aufrufbar")
        self.assertTrue(self.probe["apply_properties_is_coroutine"],
                        "device._apply_properties ist keine Koroutine mehr - "
                        "apply_ieco_only awaited es")

    def test_the_property_map_carries_ieco(self):
        self.assertTrue(self.probe["prop_map_is_dict"],
                        "device._PROPERTY_MAP ist kein dict mehr")
        self.assertTrue(self.probe["ieco_property_exists"],
                        "PropertyId.IECO ist verschwunden")
        self.assertTrue(self.probe["ieco_in_prop_map"],
                        "PropertyId.IECO steht nicht mehr in _PROPERTY_MAP - "
                        "apply_ieco_only kann iECO nicht mehr als Property bauen")

    def test_the_setter_arms_ieco_as_an_updated_property(self):
        self.assertTrue(self.probe["updated_properties_present"],
                        "device._updated_properties ist verschwunden")
        self.assertIsNone(
            self.probe["arm_error"],
            f"device.ieco = True wirft jetzt: {self.probe['arm_error']}")
        self.assertTrue(
            self.probe["ieco_armed_by_setter"],
            "device.ieco = True fuehrt PropertyId.IECO nicht mehr in "
            "_updated_properties - apply_ieco_only haette nichts zu senden")

    def test_the_ieco_property_value_is_still_a_two_tuple(self):
        self.assertTrue(
            self.probe["ieco_value_is_2_tuple"],
            "_PROPERTY_MAP[IECO](device) liefert kein 2-Tupel "
            "(ieco_number, bool) mehr")

    def test_follow_me_reaches_the_full_state_command(self):
        self.assertTrue(
            self.probe["follow_me_property_exists"],
            "AirConditioner.follow_me ist keine oeffentliche Property mehr")
        self.assertIsNone(
            self.probe["follow_me_set_error"],
            "follow_me=True/apply() ist nicht mehr nutzbar: "
            f"{self.probe['follow_me_set_error']}")
        self.assertIs(
            self.probe["follow_me_command_value"], True,
            "device.follow_me=True landet nicht mehr als True im "
            "SetStateCommand - --ensure-isense haette keine Wirkung")

    def test_the_pinned_version_matches_what_is_installed(self):
        self.assertEqual(self.probe["version"],
                         midea_apply.PINNED_MSMART_VERSION)


# --------------------------------------------------------------------------
# Katalog
# --------------------------------------------------------------------------

class CatalogTests(unittest.TestCase):
    """Wie in den Schwestermodulen: der Katalog muss vollstaendig und in beiden
    Sprachen strukturgleich sein."""

    def _used_keys(self):
        src = (REPO_DIR / "midea_apply.py").read_text(encoding="utf-8")
        return set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', src))

    def test_every_used_key_exists(self):
        missing = self._used_keys() - set(midea_apply._MESSAGES)
        self.assertEqual(missing, set(),
                         f"Verwendet, aber nicht im Katalog: {missing}")

    def test_no_orphan_keys(self):
        orphans = set(midea_apply._MESSAGES) - self._used_keys()
        self.assertEqual(orphans, set(), f"Im Katalog, aber unbenutzt: {orphans}")

    def test_placeholder_count_matches_between_languages(self):
        for key, (english, german) in midea_apply._MESSAGES.items():
            self.assertEqual(english.count("%s"), german.count("%s"), key)

    def test_both_languages_non_empty(self):
        for key, (english, german) in midea_apply._MESSAGES.items():
            self.assertTrue(english.strip(), key)
            self.assertTrue(german.strip(), key)

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            midea_apply.t("does_not_exist")

    def test_german_is_reachable(self):
        with mock.patch.dict(os.environ, {"MIDEA_IECO_LANG": "de"}):
            self.assertIn("WARNUNG",
                          midea_apply.t("prop_path_sync", "x"))


if __name__ == "__main__":
    unittest.main()
