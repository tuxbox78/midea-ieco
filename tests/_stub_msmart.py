"""Test-Hilfsmodul: registriert ein minimales Fake-``msmart``-Paket in
``sys.modules``, damit die Zielmodule ohne die echte Abhaengigkeit importierbar
sind. VOR dem Import von ``midea_ieco_ensure`` (Top-Level-Import von msmart)
bzw. vor einem Aufruf von ``verify_credentials`` importieren.

Die Fake-``AirConditioner``-Klasse ist bewusst minimal: Tests, die konkretes
Geraeteverhalten brauchen, ersetzen stattdessen ``connect_and_refresh`` bzw.
uebergeben eigene Fake-Geraete - die Stub-Klasse dient nur dazu, den Import
gelingen zu lassen.
"""
import sys
import types


def install() -> None:
    if "msmart.device.AC.device" in sys.modules:
        return

    msmart = types.ModuleType("msmart")
    device_pkg = types.ModuleType("msmart.device")
    ac_pkg = types.ModuleType("msmart.device.AC")
    dev_mod = types.ModuleType("msmart.device.AC.device")

    class AirConditioner:
        def __init__(self, *, ip=None, port=None, device_id=None):
            self.ip = ip
            self.port = port
            self.device_id = device_id

    dev_mod.AirConditioner = AirConditioner
    msmart.device = device_pkg
    device_pkg.AC = ac_pkg
    ac_pkg.device = dev_mod

    sys.modules["msmart"] = msmart
    sys.modules["msmart.device"] = device_pkg
    sys.modules["msmart.device.AC"] = ac_pkg
    sys.modules["msmart.device.AC.device"] = dev_mod


install()
