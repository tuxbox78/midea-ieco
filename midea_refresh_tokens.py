#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
"""
midea_refresh_tokens.py

Holt frische Token/Key-Paare aus der Midea-Cloud (ueber den bewaehrten
Befehl `python3 -m midealocal.cli discover --debug`) und aktualisiert
devices.json. Verifiziert JEDES gefundene Token/Key-Paar mit einer
echten lokalen Verbindung, bevor es gespeichert wird.

Zugangsdaten (Benutzername/Passwort) werden standardmaessig aus
credentials.json im Skriptverzeichnis gelesen (Vorlage:
credentials.example.json; danach `chmod 600 credentials.json`). Alternativ
lassen sie sich per --username/--password uebergeben oder - bei
interaktivem Aufruf ohne hinterlegte Datei - direkt eingeben.

Sicherheitshinweis: Standardmaessig werden die Zugangsdaten dem midealocal-
Unterprozess ueber eine nur fuer den aktuellen Nutzer lesbare midea-local.json
(0600) in einem privaten, pro Aufruf frisch angelegten Temp-Verzeichnis
uebergeben - NICHT auf der Kommandozeile. Nur falls
dieser Weg kein Ergebnis liefert, wird einmalig auf die Kommandozeilen-
Uebergabe zurueckgefallen; dann ist das Passwort waehrend der Laufzeit des
Unterprozesses kurzzeitig ueber `ps aux` bzw. /proc/<pid>/cmdline sichtbar.
Betreibe dieses Skript vorsichtshalber nur auf vertrauenswuerdigen
Einzelbenutzer-Servern.

Nutzung:
    python3 midea_refresh_tokens.py --name Wohnzimmer
    python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
    python3 midea_refresh_tokens.py --all
"""

import argparse
import asyncio
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "devices.json"
CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"

# Platzhalterwerte aus credentials.example.json: werden wie "nicht gesetzt"
# behandelt, damit ein versehentlich unbearbeitetes Beispiel klar abgewiesen
# wird, statt in einem verwirrenden Cloud-Authentifizierungsfehler zu enden.
PLACEHOLDER_VALUES = frozenset({"", "dein@account.example", "DEIN_PASSWORT", "HIER_PASSWORT_EINTRAGEN"})
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


def _clean_credential_value(value: object) -> str | None:
    """Verwirft Nicht-Strings und bekannte Platzhalterwerte (PLACEHOLDER_VALUES).

    Gilt gleichermassen fuer Werte aus credentials.json UND fuer per
    --username/--password uebergebene CLI-Argumente - ein versehentlich aus
    credentials.example.json kopierter Platzhalter (z.B. 'DEIN_PASSWORT') soll
    in keinem der beiden Faelle unbemerkt an die Cloud gesendet werden."""
    return value if isinstance(value, str) and value not in PLACEHOLDER_VALUES else None


def load_credentials() -> tuple[str | None, str | None]:
    """Liest Benutzername/Passwort aus credentials.json, sofern vorhanden.

    Rueckgabe: (username, password). Fehlt die Datei, ist sie unlesbar oder
    kein gueltiges JSON-Objekt, oder enthaelt sie nur Platzhalter/leere Werte,
    wird fuer das jeweilige Feld None zurueckgegeben - der Aufrufer entscheidet
    dann ueber CLI-Argumente oder interaktiven Prompt. Ein Syntaxfehler in der
    Datei wird als Warnung gemeldet, damit eine kaputte Datei nicht
    stillschweigend uebergangen wird."""
    if not CREDENTIALS_PATH.exists():
        return None, None
    try:
        with open(CREDENTIALS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError deckt JSONDecodeError UND UnicodeDecodeError (nicht-UTF-8-Datei) ab
        print(f"WARNUNG: {CREDENTIALS_PATH} konnte nicht gelesen werden "
              f"({type(exc).__name__}: {exc}). Nutze Argumente/Prompt.")
        return None, None
    if not isinstance(data, dict):
        print(f"WARNUNG: {CREDENTIALS_PATH} enthaelt kein JSON-Objekt. "
              f"Nutze Argumente/Prompt.")
        return None, None
    return _clean_credential_value(data.get("username")), _clean_credential_value(data.get("password"))


def resolve_credentials(arg_username: str | None, arg_password: str | None) -> tuple[str, str]:
    """Ermittelt die endgueltigen Zugangsdaten nach fester Praezedenz:
    1. explizite CLI-Argumente, 2. credentials.json, 3. interaktiver
    getpass-Prompt - Letzterer NUR wenn ein TTY vorhanden ist. In einem
    nicht-interaktiven Lauf (z.B. Cron) ohne verfuegbare Zugangsdaten wird mit
    klarer Meldung abgebrochen; es wird bewusst nie ohne TTY geprompted, um
    Haenger/EOF-Fehler zu vermeiden.

    CLI-Argumente durchlaufen denselben Platzhalter-Filter wie credentials.json
    (_clean_credential_value) - ein versehentlich uebergebener Platzhalterwert
    faellt dadurch auf Datei/Prompt zurueck statt an die Cloud gesendet zu
    werden. Bricht der Nutzer einen Prompt per Strg+D (EOF) ab, endet das
    Skript - wie jeder andere Zugangsdaten-Fehlerfall - mit einer klaren
    Meldung auf stderr statt einem rohen Traceback. Strg+C (KeyboardInterrupt)
    wird bewusst NICHT abgefangen: das Skript soll dabei wie jedes andere
    Python-Programm sofort abbrechen."""
    file_username, file_password = load_credentials()
    username = _clean_credential_value(arg_username) or file_username
    password = _clean_credential_value(arg_password) or file_password

    if (not username or not password) and sys.stdin.isatty():
        print("Zugangsdaten unvollstaendig - bitte interaktiv eingeben:")
        try:
            if not username:
                username = input("  Midea-E-Mail: ").strip()
            if not password:
                password = getpass.getpass("  Midea-Passwort: ")
        except EOFError:
            print("\nFEHLER: Eingabe abgebrochen (EOF).", file=sys.stderr)
            sys.exit(1)

    if not username or not password:
        print(
            "FEHLER: Keine gueltigen Zugangsdaten verfuegbar. Hinterlege sie in "
            f"{CREDENTIALS_PATH} (Vorlage credentials.example.json kopieren, "
            "ausfuellen, dann 'chmod 600 credentials.json') oder uebergib "
            "--username/--password.",
            file=sys.stderr,
        )
        sys.exit(1)
    return username, password


def _run_discover(host: str, *, use_config: bool,
                  username: str, password: str) -> subprocess.CompletedProcess:
    """Fuehrt `midealocal.cli discover --host <host> --debug` aus.

    use_config=True: schreibt die Zugangsdaten in eine midea-local.json (0600)
    in einem PRO AUFRUF frisch angelegten, privaten Temp-Verzeichnis und macht
    dieses zum CWD des Unterprozesses; --username/--password bleiben WEG. Die
    midealocal-CLI bevorzugt eine midea-local.json im aktuellen Verzeichnis
    (get_config_file_path()) und fuellt daraus nur fehlende Argumente - so
    erscheint das Passwort nicht in der Prozess-argv (verifiziert gegen
    midea-local 6.10.0). Ein eigenes Temp-Verzeichnis je Aufruf isoliert
    gleichzeitige Laeufe (z.B. Wochen-Cron trifft manuellen Aufruf) gegen ein
    Wettrennen um eine gemeinsame Datei und haelt das Projektverzeichnis frei
    von Zugangsdaten-Resten. Es wird in jedem Fall wieder entfernt.
    use_config=False: uebergibt die Zugangsdaten auf der Kommandozeile
    (Fallback; Passwort dabei kurzzeitig via ps sichtbar).

    Wirft RuntimeError bei einem klaren Ausfuehrungsfehler (Timeout, midealocal
    nicht installiert)."""
    cmd = [sys.executable, "-m", "midealocal.cli", "discover", "--host", host, "--debug"]
    run_kwargs = {"capture_output": True, "text": True, "timeout": SUBPROCESS_TIMEOUT}
    tmpdir = None
    try:
        if use_config:
            tmpdir = tempfile.mkdtemp(prefix="midea-local-discover-")
            _atomic_write_json(Path(tmpdir) / "midea-local.json",
                               {"username": username, "password": password})
            run_kwargs["cwd"] = tmpdir
        else:
            cmd[4:4] = ["--username", username, "--password", password]
        try:
            return subprocess.run(cmd, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"discover-Befehl hat nach {SUBPROCESS_TIMEOUT}s nicht reagiert "
                                f"(Geraet unter {host} erreichbar?)") from exc
        except FileNotFoundError as exc:
            raise RuntimeError("midealocal ist im aktuellen Python-Interpreter nicht installiert "
                                "(falsches venv aktiv?)") from exc
    finally:
        if tmpdir is not None:
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


def fetch_candidate_credentials(username: str, password: str, host: str) -> tuple[list[tuple[str, str]], str | None]:
    """Ruft discover --debug auf und gibt ALLE gefundenen (key, token)-
    Kandidaten zurueck, sowie die gemeldete Appliance-ID (falls vorhanden).
    Wirft RuntimeError bei einem klaren Ausfuehrungsfehler.

    Primaerweg: Zugangsdaten via temporaerer midea-local.json (kein Passwort in
    der Prozess-argv). Liefert dieser Weg kein verwertbares Ergebnis - etwa weil
    eine aeltere midealocal-Version die Config-Datei nicht auswertet -, wird
    EINMAL auf die Kommandozeilen-Variante zurueckgefallen (mit deutlicher
    Warnung, da das Passwort dabei kurzzeitig ueber ps sichtbar ist). Ein echter
    Ausfuehrungsfehler (Timeout, midealocal fehlt) loest KEINEN Fallback aus -
    er wuerde ohnehin identisch scheitern."""
    result = _run_discover(host, use_config=True, username=username, password=password)
    try:
        return _parse_discover_output(result)
    except RuntimeError as exc_cfg:
        print(f"WARNUNG: discover ueber midea-local.json lieferte kein Ergebnis "
              f"({exc_cfg}). Falle einmalig auf die Kommandozeilen-Variante "
              f"zurueck (Passwort dabei kurzzeitig via ps sichtbar).")
        result = _run_discover(host, use_config=False, username=username, password=password)
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


def update_device(dev_conf: dict, username: str, password: str) -> bool:
    name = dev_conf.get("name", "unbekannt")
    host = dev_conf.get("ip")
    if not host:
        print(f"[{name}] FEHLER: Keine IP-Adresse in devices.json fuer dieses Geraet hinterlegt.")
        return False

    print(f"[{name}] Hole Token/Key-Kandidaten ueber {host} ...")
    try:
        candidates, appliance_id = fetch_candidate_credentials(username, password, host)
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
    parser = argparse.ArgumentParser(
        description="Holt frische Midea Token/Key-Paare per discover --debug, "
                    "verifiziert sie, und aktualisiert devices.json."
    )
    parser.add_argument("--username", help="Midea-Account (Standard: aus credentials.json)")
    parser.add_argument("--password", help="Midea-Passwort (Standard: aus credentials.json)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Alle Geraete aus devices.json aktualisieren")
    group.add_argument("--name", help="Name des Geraets (neu oder bestehend)")
    parser.add_argument("--host", help="IP-Adresse (nur zusammen mit --name fuer NEUE Geraete)")
    args = parser.parse_args()

    username, password = resolve_credentials(args.username, args.password)

    # Fruehzeitige, klare Meldung statt eines rohen Tracebacks mitten im Lauf,
    # falls msmart-ng im aktiven Interpreter fehlt (verify_credentials importiert
    # es erst spaet per Lazy-Import). Bewusst ERST nach parse_args und
    # resolve_credentials, damit --help und Zugangsdaten-Fehler weiterhin ohne
    # installiertes msmart funktionieren; und VOR jedem Cloud-Kontakt.
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
        success = update_device(dev, username, password)
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
