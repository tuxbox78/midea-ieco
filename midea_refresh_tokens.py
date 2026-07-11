#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_refresh_tokens.py

Holt frische Token/Key-Paare fuer die konfigurierten Geraete und aktualisiert
devices.json. Verifiziert JEDES gefundene Token/Key-Paar mit einer echten
lokalen Verbindung zum Geraet, bevor es gespeichert wird.

Funktionsweise / warum OHNE Cloud-Zugangsdaten:
    Token und Key sind an die UDP-ID des jeweiligen GERAETS gebunden, nicht an
    ein Cloud-Konto. Sie werden ueber `python3 -m midealocal.cli discover
    --host <ip> --debug` bezogen. Die Token-Vergabe laeuft heute ausschliesslich
    ueber die NetHome-Plus-Cloud-API; die getToken-Endpunkte von MSmartHome und
    Meiju hat Midea serverseitig abgeschaltet (sie quittieren errorCode 3004
    "value is illegal" - am 2026-07-11 real gegen ein Geraet verifiziert). Ein
    EIGENES MSmartHome-Konto ist damit fuer den Token-Abruf nutzlos, und auf der
    NetHome-Plus-Cloud existiert es gar nicht. `midealocal` meldet sich deshalb
    mit seinem eingebauten NetHome-Plus-Konto an - genau das tut auch msmart-ng.
    Dieses Skript uebergibt der CLI daher BEWUSST keine Zugangsdaten (die frueher
    abgefragte credentials.json entfiel mit 0.2.0). Der eigentliche Wert liegt
    woanders: jeder Kandidat wird VOR dem Speichern gegen das Geraet verifiziert,
    und bestehende Werte werden nur nach erfolgreicher Verifikation ueberschrieben
    - faellt die Cloud-API eines Tages aus, bleiben die zuletzt gueltigen Tokens
    erhalten und die lokale Steuerung laeuft weiter.
    (Mechanik quellcode-identisch in midea-local 6.6.1 und 6.10.0; real
    verifiziert gegen die gepinnte 6.6.1 am 2026-07-11.)

Nutzung:
    python3 midea_refresh_tokens.py --name Wohnzimmer
    python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
    python3 midea_refresh_tokens.py --all
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "devices.json"
SUBPROCESS_TIMEOUT = 60
VERIFY_TIMEOUT = 10

# Extraktion der (key, token)-Paare aus der rohen --debug-Ausgabe der Cloud.
# Beispielformat (einzeilig):
#   response: b'{"result": {"tokenlist": [{"udpId": "..", "key": "HEX", "token": "HEX"}]}...}'
# Annahmen (entsprechen dem beobachteten Format): tokenlist ist ein Array
# FLACHER Objekte (keine verschachtelten Klammern); key/token sind Hex-Strings
# und koennen innerhalb eines Eintrags in BELIEBIGER Reihenfolge stehen.
# Mehrere Eintraege und mehrere tokenlist-Arrays werden alle beruecksichtigt.
_TOKENLIST_ARRAY_RE = re.compile(r'"tokenlist"\s*:\s*\[(.*?)\]', re.DOTALL)
_ENTRY_RE = re.compile(r"\{(.*?)\}", re.DOTALL)
_KEY_RE = re.compile(r'"key"\s*:\s*"([0-9a-fA-F]+)"')
_TOKEN_RE = re.compile(r'"token"\s*:\s*"([0-9a-fA-F]+)"')
APPLIANCE_ID_RE = re.compile(r"applianceCodes['\"]?\s*[:=]\s*['\"]?(\d+)")


def extract_token_key_pairs(text: str) -> list[tuple[str, str]]:
    """Liefert ALLE (key, token)-Paare aus allen tokenlist-Arrays der rohen
    Cloud-Antwort - reihenfolgeerhaltend und dedupliziert. Beruecksichtigt
    beliebige Feldreihenfolge innerhalb eines Eintrags sowie mehrere Eintraege
    bzw. mehrere tokenlist-Arrays (siehe Formatannahmen oben). Die alte,
    strikt 'key vor token'-erwartende Extraktion war eine echte Teilmenge
    dieser hier."""
    pairs: list[tuple[str, str]] = []
    for array_body in _TOKENLIST_ARRAY_RE.findall(text):
        for entry_body in _ENTRY_RE.findall(array_body):
            key = _KEY_RE.search(entry_body)
            token = _TOKEN_RE.search(entry_body)
            if key and token:
                pairs.append((key.group(1), token.group(1)))
    return list(dict.fromkeys(pairs))


def load_config() -> dict:
    """Liest devices.json. Fehlt die Datei, wird eine leere Geraeteliste
    zurueckgegeben (normaler Erstlauf). Ist die Datei hingegen unlesbar, nicht
    als UTF-8 dekodierbar, kein gueltiges JSON oder hat sie nicht die erwartete
    Form ({"devices": [...]}),
    wird mit klarer Meldung auf stderr abgebrochen - ein verstaendlicher
    Hinweis ist im woechentlichen Cron-Lauf deutlich nuetzlicher als ein roher
    Traceback."""
    if not CONFIG_PATH.exists():
        return {"devices": []}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError (nicht-UTF-8-Datei) ab
        print(f"FEHLER: {CONFIG_PATH} konnte nicht gelesen werden "
              f"({type(exc).__name__}: {exc}).", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        print(f"FEHLER: {CONFIG_PATH} hat nicht die erwartete Form "
              '{"devices": [...]}.', file=sys.stderr)
        sys.exit(1)
    return data


def _atomic_write_json(path: Path, data: object) -> None:
    """Schreibt ``data`` als JSON atomar und mit Rechten 0600 nach ``path``.

    Ablauf: in eine temporaere Datei IM SELBEN Verzeichnis schreiben (damit
    os.replace auf demselben Dateisystem bleibt), Rechte auf 0600 setzen, auf
    Platte zwingen (flush + fsync) und erst dann per os.replace an den
    endgueltigen Namen ruecken. Das garantiert zweierlei:
      (a) kein Zeitfenster, in dem die Datei world-readable waere - mkstemp
          legt sie von vornherein nur fuer den aktuellen Nutzer lesbar an;
      (b) kein zerstoerter Torso - bricht das Schreiben ab, bleibt die
          bisherige ``path``-Datei unveraendert; zurueck bleibt hoechstens
          eine harmlose, git-ignorierte .tmp-Waise.
    os.replace ist laut POSIX eine atomare Operation."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort-Aufraeumen der temporaeren Datei; das Original bleibt
        # in jedem Fall unangetastet. Fehler wird weitergereicht.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def save_config(config: dict) -> None:
    """Schreibt devices.json atomar und mit Rechten 0600 (siehe
    _atomic_write_json). Bei einem Schreibfehler bleibt die bisherige Datei
    unveraendert; der Fehler (OSError) wird an den Aufrufer weitergereicht."""
    _atomic_write_json(CONFIG_PATH, config)


def _run_discover(host: str) -> subprocess.CompletedProcess:
    """Fuehrt `midealocal.cli discover --host <host> --debug` in einem privaten,
    pro Aufruf frisch angelegten Temp-Verzeichnis aus und gibt das Ergebnis
    zurueck.

    Bewusst OHNE --username/--password: die Token-Vergabe laeuft ohnehin ueber
    midealocals eingebautes NetHome-Plus-Konto (siehe Modul-Docstring), eigene
    Zugangsdaten waeren wirkungslos. Damit taucht auch garantiert kein Passwort
    in der Prozess-argv auf.

    In das Temp-Verzeichnis wird eine LEERE midea-local.json ({}) geschrieben und
    zum CWD des Unterprozesses gemacht. Grund: get_config_file_path() der CLI
    bevorzugt eine midea-local.json im aktuellen Verzeichnis vor der nutzer-
    globalen Konfiguration (~/.config/midea-local/midea-local.json). Ohne diesen
    Guard koennte eine dort hinterlegte Config (z.B. mit einem cloud_name wie
    "SmartHome") den Abruf auf eine serverseitig abgeschaltete Cloud-API umleiten
    und so je nach Host unterschiedlich scheitern. Die leere {}-Datei
    ueberschreibt NICHTS (der Merge der CLI fuellt nur FEHLENDE Namespace-Felder),
    macht das Verhalten aber deterministisch und unabhaengig von der Host-
    Umgebung. Ein eigenes Temp-Verzeichnis je Aufruf isoliert zudem gleichzeitige
    Laeufe (Wochen-Cron trifft manuellen Aufruf) gegeneinander. Es wird in jedem
    Fall wieder entfernt.

    Wirft RuntimeError bei jedem Ausfuehrungsfehler (Temp-Verzeichnis oder
    Isolations-Konfig nicht anlegbar, Timeout, midealocal nicht installiert,
    sonstiger Subprozess-Startfehler)."""
    cmd = [sys.executable, "-m", "midealocal.cli", "discover", "--host", host, "--debug"]
    try:
        tmpdir = tempfile.mkdtemp(prefix="midea-local-discover-")
    except OSError as exc:
        raise RuntimeError("Temporaeres Arbeitsverzeichnis fuer discover konnte nicht "
                           f"angelegt werden ({type(exc).__name__}: {exc}).") from exc
    try:
        try:
            _atomic_write_json(Path(tmpdir) / "midea-local.json", {})
        except OSError as exc:
            raise RuntimeError("Isolations-Konfig fuer discover konnte nicht geschrieben "
                               f"werden ({type(exc).__name__}: {exc}).") from exc
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=SUBPROCESS_TIMEOUT, cwd=tmpdir)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"discover-Befehl hat nach {SUBPROCESS_TIMEOUT}s nicht reagiert "
                                f"(Geraet unter {host} erreichbar?)") from exc
        except FileNotFoundError as exc:
            # FileNotFoundError ist eine OSError-Unterklasse und MUSS vor dem
            # generischen 'except OSError' stehen, damit hier die spezifische
            # Meldung ("midealocal nicht installiert") greift.
            raise RuntimeError("midealocal ist im aktuellen Python-Interpreter nicht installiert "
                                "(falsches venv aktiv?)") from exc
        except OSError as exc:
            # Jeder sonstige Startfehler des Unterprozesses (z.B. PermissionError)
            # wird ebenfalls als RuntimeError gewrappt - update_device faengt nur
            # RuntimeError; ohne dieses Wrapping schluege ein solcher Fehler als
            # roher Traceback durch und beendete einen ganzen 'all'-Lauf.
            raise RuntimeError("discover-Befehl konnte nicht gestartet werden "
                               f"({type(exc).__name__}: {exc}).") from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _parse_discover_output(result: subprocess.CompletedProcess) -> tuple[list[tuple[str, str]], str | None]:
    """Extrahiert (key, token)-Kandidaten und Appliance-ID aus einem discover-
    Ergebnis. Wirft RuntimeError bei Nicht-Null-Exit oder fehlender tokenlist."""
    combined_output = result.stdout + result.stderr
    if result.returncode != 0:
        tail = combined_output[-800:] if combined_output else "(keine Ausgabe)"
        raise RuntimeError(f"discover-Befehl endete mit Exit-Code {result.returncode}. "
                            f"Letzte Ausgabe: {tail}")
    matches = extract_token_key_pairs(combined_output)
    if not matches:
        tail = combined_output[-800:] if combined_output else "(keine Ausgabe)"
        raise RuntimeError(f"Kein tokenlist-Eintrag in der Ausgabe gefunden. Letzte Ausgabe: {tail}")
    appliance_ids = APPLIANCE_ID_RE.findall(combined_output)
    return matches, (appliance_ids[0] if appliance_ids else None)


def fetch_candidate_credentials(host: str) -> tuple[list[tuple[str, str]], str | None]:
    """Ruft discover --debug auf und gibt ALLE gefundenen (key, token)-
    Kandidaten zurueck, sowie die gemeldete Appliance-ID (falls vorhanden).
    Wirft RuntimeError bei einem klaren Ausfuehrungsfehler (Timeout, kein
    tokenlist-Eintrag in der Ausgabe, midealocal nicht installiert).

    Es gibt genau EINEN Weg (siehe _run_discover): der discover-Aufruf ohne
    Zugangsdaten. Ein frueherer Kommandozeilen-Fallback (Passwort via argv)
    entfiel mit 0.2.0, weil eigene Zugangsdaten fuer den Token-Abruf ohnehin
    wirkungslos sind - siehe Modul-Docstring."""
    result = _run_discover(host)
    return _parse_discover_output(result)


async def verify_credentials(ip: str, port: int, device_id: int, key: str, token: str) -> bool:
    """Testet ein Token/Key-Paar mit einer echten, minimalen Verbindung,
    BEVOR es in devices.json gespeichert wird. Verhindert, dass ein
    falscher Kandidat (z.B. 'method 2' statt 'method 1') unbemerkt
    gespeichert wird."""
    # Lazy-Import: haelt das Modul auch ohne installiertes msmart importierbar
    # (z.B. fuer isolierte Unit-Tests der Zugangsdaten-Logik).
    from msmart.device.AC.device import AirConditioner as AC

    device = AC(ip=ip, port=port, device_id=device_id)
    try:
        await asyncio.wait_for(device.authenticate(token, key), timeout=VERIFY_TIMEOUT)
        await asyncio.wait_for(device.refresh(), timeout=VERIFY_TIMEOUT)
        return True
    except Exception:
        return False
    finally:
        for method_name in ("close", "disconnect", "stop"):
            method = getattr(device, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass


def update_device(dev_conf: dict) -> bool:
    """Frischt Token/Key EINES Geraets auf: holt Kandidaten per credential-freiem
    discover, verifiziert sie der Reihe nach gegen das echte Geraet und speichert
    das erste funktionierende (key, token)-Paar in-place in dev_conf. Rueckgabe
    True bei Erfolg, sonst False. Bestehende Werte werden NUR bei erfolgreicher
    Verifikation ueberschrieben - schlaegt alles fehl, bleibt dev_conf
    unveraendert (kein kaputter Eintrag nach einem Fehlversuch)."""
    name = dev_conf.get("name", "unbekannt")
    host = dev_conf.get("ip")
    if not host:
        print(f"[{name}] FEHLER: Keine IP-Adresse in devices.json fuer dieses Geraet hinterlegt.")
        return False

    print(f"[{name}] Hole Token/Key-Kandidaten ueber {host} ...")
    try:
        candidates, appliance_id = fetch_candidate_credentials(host)
    except RuntimeError as exc:
        print(f"[{name}] FEHLER beim Token-Abruf: {exc}")
        return False

    existing_id = str(dev_conf.get("id", "")).strip()
    if appliance_id is not None:
        if existing_id and existing_id != appliance_id:
            print(f"[{name}] WARNUNG: In devices.json steht id={existing_id}, "
                  f"aber die Cloud meldet id={appliance_id}. Bestehenden Wert NICHT ueberschrieben.")
        elif not existing_id:
            dev_conf["id"] = int(appliance_id)

    device_id_str = str(dev_conf.get("id", "")).strip()
    if not device_id_str:
        print(f"[{name}] FEHLER: Keine Geraete-ID verfuegbar (weder in devices.json noch von der "
              f"Cloud gemeldet). Eintrag wird NICHT gespeichert.")
        return False
    device_id = int(device_id_str)
    port = int(dev_conf.get("port", 6444))

    print(f"[{name}] {len(candidates)} Kandidat(en) gefunden, verifiziere der Reihe nach ...")
    for idx, (key, token) in enumerate(candidates, start=1):
        ok = asyncio.run(verify_credentials(host, port, device_id, key, token))
        if ok:
            dev_conf["token"] = token
            dev_conf["key"] = key
            dev_conf.setdefault("port", 6444)
            print(f"[{name}] Kandidat {idx}/{len(candidates)} erfolgreich verifiziert und gespeichert.")
            return True
        print(f"[{name}] Kandidat {idx}/{len(candidates)} lieferte keine gueltige Verbindung, "
              f"versuche naechsten ...")

    print(f"[{name}] FEHLER: Keiner der {len(candidates)} Kandidaten liess sich verbinden. "
          f"Bestehende Werte in devices.json bleiben unveraendert.")
    return False


def main() -> None:
    """CLI-Einstieg: wertet --all bzw. --name/--host aus, prueft die msmart-
    Verfuegbarkeit VOR jedem Cloud-Kontakt, frischt die betroffenen Geraete auf
    (update_device) und schreibt devices.json atomar zurueck. Exit-Code: 0 =
    Erfolg, 2 = mindestens ein Geraet fehlgeschlagen, 1 = Nutzungs-/Konfig-Fehler
    (msmart fehlt, leere Geraeteliste bei --all, neues Geraet ohne --host,
    Schreibfehler)."""
    parser = argparse.ArgumentParser(
        description="Holt frische Midea Token/Key-Paare per discover --debug, "
                    "verifiziert sie, und aktualisiert devices.json."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Alle Geraete aus devices.json aktualisieren")
    group.add_argument("--name", help="Name des Geraets (neu oder bestehend)")
    parser.add_argument("--host", help="IP-Adresse (nur zusammen mit --name fuer NEUE Geraete)")
    args = parser.parse_args()

    # Fruehzeitige, klare Meldung statt eines rohen Tracebacks mitten im Lauf,
    # falls msmart-ng im aktiven Interpreter fehlt (verify_credentials importiert
    # es erst spaet per Lazy-Import). Bewusst ERST nach parse_args, damit --help
    # weiterhin ohne installiertes msmart funktioniert; und VOR jedem Cloud-Kontakt.
    try:
        import msmart  # noqa: F401  (reine Verfuegbarkeitspruefung)
    except ImportError:
        print("FEHLER: msmart-ng ist im aktiven Python-Interpreter nicht "
              "installiert. Ist die venv aktiv? Installation z.B. mit: "
              "venv/bin/pip install msmart-ng", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    devices = config.setdefault("devices", [])

    # Nicht-Objekt-Eintraege (nur durch Hand-Edit moeglich) koennen keine
    # Geraete sein: ueberspringen und melden, statt spaeter mit einem
    # AttributeError (d.get auf einem Nicht-Objekt) abzubrechen. Sie bleiben in
    # config erhalten - save_config schreibt sie unveraendert zurueck.
    valid_devices = [d for d in devices if isinstance(d, dict)]
    skipped = len(devices) - len(valid_devices)
    if skipped:
        print(f"WARNUNG: {skipped} unerwartete(r) Eintrag/Eintraege in "
              f"{CONFIG_PATH.name} uebersprungen (kein Objekt).")

    if args.all:
        if not valid_devices:
            print("devices.json enthaelt noch keine Geraete. Nutze --name/--host fuer ein neues Geraet.")
            sys.exit(1)
        targets = valid_devices
        new_entry = None
    else:
        targets = [d for d in valid_devices if d.get("name") == args.name]
        new_entry = None
        if not targets:
            if not args.host:
                print(f"Geraet '{args.name}' ist neu. Bitte zusaetzlich --host angeben.")
                sys.exit(1)
            new_entry = {"name": args.name, "ip": args.host, "port": 6444, "id": "", "token": "", "key": ""}
            targets = [new_entry]

    ok = True
    successful_new_entry = False
    for dev in targets:
        success = update_device(dev)
        ok = ok and success
        if dev is new_entry and success:
            successful_new_entry = True

    # Ein neues Geraet nur dann dauerhaft speichern, wenn der Abruf
    # tatsaechlich erfolgreich UND verifiziert war. Verhindert kaputte
    # Platzhalter-Eintraege in devices.json nach einem fehlgeschlagenen Versuch.
    if new_entry is not None and successful_new_entry:
        devices.append(new_entry)
    elif new_entry is not None and not successful_new_entry:
        print(f"Neues Geraet '{args.name}' wurde NICHT gespeichert, da der Abruf fehlgeschlagen ist.")

    if new_entry is None or successful_new_entry:
        try:
            save_config(config)
        except OSError as exc:
            print(f"FEHLER: devices.json konnte nicht geschrieben werden "
                  f"({type(exc).__name__}: {exc}).", file=sys.stderr)
            sys.exit(1)
        print(f"devices.json aktualisiert: {CONFIG_PATH}")

    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
