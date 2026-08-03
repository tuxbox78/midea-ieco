# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_devices.py

Gemeinsame, seiteneffektfreie Bausteine fuer den nicht-verlierenden Umgang mit
devices.json - genutzt vom Installer (install.sh) an zwei Stellen:

  * dem --reconfigure-Backup nach devices.json.bak (merge_devices +
    atomic_write_devices), und
  * der Token/Key/Port-Uebernahme beim Neuschreiben von devices.json
    (credential_index).

Leitidee (Best-Practice fuer die Sicherung UNWIEDERBRINGLICHER Zugangsdaten -
die Cloud-getToken-Endpunkte sind abgeschaltet, ein verlorener Token ist lokal
nicht neu beschaffbar): Eine Sicherung darf NIE Information verlieren. Sie ist
eine Vereinigung ("synthetic full"), kein blindes Snapshot - fuer JEDE Geraete-id
wird das vollstaendigste Zugangsdaten-Tripel behalten, auch wenn nur EINE der
beteiligten Kopien es noch fuehrt (Teilverlust). Das entspricht dem feld-
erhaltenden Read-Modify-Write, wie es u.a. Vault (`patch`) und die Behandlung
unbekannter Felder in Protocol Buffers vormachen.

BEWUSST nur die Standardbibliothek und ausschliesslich reine Funktionen (die
einzige I/O ist lesend in load_devices bzw. der atomare Write): so ist das Modul
ohne venv, ohne msmart/midealocal und ohne Seiteneffekte importierbar - auch aus
den `python3 - <<EOF`-Heredocs des Installers, deren cwd das Installationsver-
zeichnis ist (dort liegt diese Datei) - und isoliert per unittest pruefbar.
"""

from __future__ import annotations

import json
import os
import tempfile


def load_devices(path: str) -> list[dict]:
    """Liest die Geraeteliste aus einer devices.json robust ein und gibt IMMER eine
    Liste (evtl. leer) zurueck. Jeder Lese-/Parse-Fehler und jede unerwartete
    Struktur - fehlende Datei, kaputtes JSON, kein Objekt an der Wurzel, "devices"
    kein Array - faellt auf [] zurueck; Nicht-Objekt-Eintraege werden ausgefiltert.
    So kann kein Aufrufer an einer fehlenden/kaputten Vorgaengerdatei scheitern:
    ein nicht vorhandenes Backup ist schlicht "keine Kandidaten"."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    devices = data.get("devices")
    if not isinstance(devices, list):
        return []
    return [d for d in devices if isinstance(d, dict)]


def parse_device_id(raw: object) -> str | None:
    """Normalisiert eine Geraete-id auf ihre dezimale String-Form (12345 und
    "12345" -> "12345") oder None, wenn sie nicht wohlgeformt ist. isascii() VOR
    isdigit(): Unicode-Ziffern (hochgestellt, arabisch-indisch) sind isdigit()-wahr,
    aber int() wirft darauf ValueError - eine kaputte id soll ihren Eintrag
    ueberspringen, nicht (via Exception) die Uebernahme der Geschwister abreissen."""
    s = str(raw).strip()
    if not (s.isascii() and s.isdigit()):
        return None
    return str(int(s))


def parse_port(raw: object) -> int | None:
    """Validiert einen TCP-Port aus JSON: der int, wenn er in 1..65535 liegt, sonst
    None. bool wird ausgeschlossen (bool ist int-Subklasse, int(True)==1 laege im
    gueltigen Bereich und rutschte sonst als Port 1 durch). Gefangen werden
    TypeError (z.B. None), ValueError (z.B. "64o4") UND OverflowError: Pythons json
    liest die blanken Literale Infinity/-Infinity als float('inf') ein, worauf int()
    einen OverflowError wirft - der ist KEIN Subtyp von ValueError und muss daher
    ausdruecklich mitgefangen werden, sonst reisst ein '"port": Infinity' den
    Aufrufer ab."""
    if isinstance(raw, bool):
        return None
    try:
        p = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return p if 1 <= p <= 65535 else None


def best_pair(records: list[dict]) -> tuple[str, str, int]:
    """Waehlt aus mehreren Records fuer DIESELBE id - in Praezedenz-Reihenfolge, die
    bevorzugte (neueste, z.B. die aktuelle devices.json) Quelle zuerst - das
    vollstaendigste Zugangsdaten-Tripel:

      token/key: das erste Paar, in dem BEIDE nicht-leer sind (sonst "", "");
      port:      der erste gueltige Port (parse_port), sonst der Default 6444.

    token/key und port werden UNABHAENGIG voneinander gewaehlt: ein Record mit
    gueltigem Port aber leerem Token kann so den Port beisteuern, ohne die
    Token-Wahl zu belegen. Ein uebernommener, evtl. veralteter Token ist
    ungefaehrlich - die Werkzeuge verifizieren token/key vor jeder Nutzung, ein
    falscher scheitert dort sichtbar, nie schlechter als ein leeres Feld."""
    token, key = "", ""
    for r in records:
        tok, k = r.get("token"), r.get("key")
        if tok and k:
            token, key = tok, k
            break
    port = 6444
    for r in records:
        p = parse_port(r.get("port"))
        if p is not None:
            port = p
            break
    return token, key, port


def _group_by_id(*device_lists: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    """Gruppiert Geraete-Records mehrerer Quellen nach normalisierter id, unter
    Wahrung der Reihenfolge (Quellen in Argumentfolge, Records in Dateifolge).
    Liefert (reihenfolge, id -> [records]); wohlgeformte ids nur (parse_device_id),
    kaputte werden uebersprungen. Gemeinsamer Kern von credential_index und
    merge_devices - so gibt es die Gruppierung genau einmal."""
    per_id: dict[str, list[dict]] = {}
    order: list[str] = []
    for devices in device_lists:
        for d in devices:
            did = parse_device_id(d.get("id", ""))
            if did is None:
                continue
            if did not in per_id:
                per_id[did] = []
                order.append(did)
            per_id[did].append(d)
    return order, per_id


def credential_index(paths: list[str]) -> dict[str, tuple[str, str, int]]:
    """Baut aus einer oder mehreren devices.json-Dateien - in Praezedenz-Reihenfolge,
    die massgebliche zuerst (z.B. die aktuelle devices.json VOR der .bak) - einen
    Index id -> (token, key, port). Pro id werden die Records aus ALLEN Quellen
    gesammelt und via best_pair zum vollstaendigsten Tripel verschmolzen. Grundlage
    der nicht-verlierenden Uebernahme beim Rewrite: ein in der aktuellen Datei
    token-los gewordenes Geraet bekommt seine Werte aus der .bak zurueck."""
    order, per_id = _group_by_id(*(load_devices(p) for p in paths))
    return {did: best_pair(per_id[did]) for did in order}


def merge_devices(primary_path: str, secondary_path: str) -> list[dict]:
    """Verschmilzt zwei devices.json-Staende zu EINER vollstaendigen Geraeteliste,
    ohne je Information zu verlieren (synthetic-full-Prinzip). primary hat Vorrang
    (z.B. die aktuelle devices.json vor der bisherigen .bak): fuer die Vereinigung
    aller ids liefert best_pair token/key/port; name/ip stammen aus der ersten
    Quelle, die die id fuehrt. Ein Geraet, das nur in EINER Quelle steht - etwa ein
    bei einem frueheren Reconfigure aus der aktuellen Liste gefallenes -, bleibt
    erhalten. Deterministische Reihenfolge: primary-ids in ihrer urspruenglichen
    Folge, dann die nur in secondary vorhandenen. Rueckgabe: Liste von
    device-dicts, geeignet fuer atomic_write_devices."""
    order, per_id = _group_by_id(load_devices(primary_path), load_devices(secondary_path))
    merged: list[dict] = []
    for did in order:
        records = per_id[did]
        token, key, port = best_pair(records)
        first = records[0]  # name/ip aus der bevorzugten (ersten) Quelle
        merged.append({
            "name": first.get("name", ""),
            "ip": first.get("ip", ""),
            "port": port,
            "id": int(did),
            "token": token,
            "key": key,
        })
    return merged


def atomic_write_devices(path: str, devices: list[dict]) -> None:
    """Schreibt {"devices": devices} atomar und mit Rechten 0600 nach 'path':
    mkstemp im Zielverzeichnis (damit os.replace auf demselben Dateisystem bleibt),
    0600 setzen, JSON schreiben, flush + fsync auf Platte zwingen, dann os.replace
    (POSIX-atomar) an den endgueltigen Namen. Kein world-readable-Zeitfenster
    (Token/Key stehen im Klartext) und kein halb geschriebener Torso, auch wenn
    'path' bereits existierte. Bei JEDEM Fehler wird die Temp-Datei entfernt und die
    Ausnahme weitergereicht - der bisherige 'path' bleibt dann unveraendert."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory,
                               prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"devices": devices}, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
