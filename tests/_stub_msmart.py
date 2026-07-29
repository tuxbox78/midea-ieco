# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""Test-Hilfsmodul: registriert ein minimales Fake-``msmart``-Paket in
``sys.modules``, damit die Zielmodule ohne die echte Abhaengigkeit importierbar
sind. VOR dem Import von ``midea_ieco_ensure`` (Top-Level-Import von msmart)
bzw. vor einem Aufruf von ``verify_credentials`` importieren.

Die Fake-``AirConditioner``-Klasse ist bewusst minimal: Tests, die konkretes
Geraeteverhalten brauchen, ersetzen stattdessen ``connect_and_refresh`` bzw.
uebergeben eigene Fake-Geraete - die Stub-Klasse dient nur dazu, den Import
gelingen zu lassen.

FORMTREUE: Die Klasse bildet den Aufraeum-Weg des echten Objekts nach - ein
``_lan`` mit synchronem ``_disconnect()`` - und definiert bewusst KEIN
``close``/``disconnect``/``stop``, weil msmart-ngs ``AirConditioner`` diese
Namen nicht kennt (gemessen gegen 2026.7.0). Das ist keine Kosmetik: fuenf
Attrappen, die MEHR konnten als das echte Objekt, haben ueber Monate einen
toten Aufraeum-Pfad gruen gehalten. Eine Attrappe darf aermer sein als die
Wirklichkeit, nie reicher - und ebenso wenig strenger, siehe das refresh() in
tests/test_refresh_tokens.py. tests/test_conn.py sichert das ab.
"""
import sys
import types


class RecordingLAN:
    """Bildet ``msmart.lan.LAN`` so weit nach, wie das Aufraeumen es braucht.

    Traegend ist die FORM: ``_disconnect`` heisst so wie in msmart-ng, ist
    SYNCHRON und mehrfach aufrufbar (das echte ``LAN._disconnect`` prueft intern
    auf ein vorhandenes Protokoll und tut ohne Verbindung nichts).

    Hier zentral definiert und nicht je Testdatei erneut: alle Geraete-Attrappen
    der Suite brauchen genau diese Form, und drei Kopien waeren drei Stellen, an
    denen sie auseinanderlaufen koennte.

    ``on_disconnect`` ist ein optionaler Haken fuer Attrappen, die das
    Schliessen zusaetzlich in ihre eigene Reihenfolgeliste protokollieren."""

    def __init__(self, on_disconnect=None):
        self.disconnect_calls = 0
        self._on_disconnect = on_disconnect

    def _disconnect(self) -> None:
        self.disconnect_calls += 1
        if self._on_disconnect is not None:
            self._on_disconnect()


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
            self._lan = RecordingLAN()

    dev_mod.AirConditioner = AirConditioner
    msmart.device = device_pkg
    device_pkg.AC = ac_pkg
    ac_pkg.device = dev_mod

    sys.modules["msmart"] = msmart
    sys.modules["msmart.device"] = device_pkg
    sys.modules["msmart.device.AC"] = ac_pkg
    sys.modules["msmart.device.AC.device"] = dev_mod


install()
