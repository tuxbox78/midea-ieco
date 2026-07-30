# On-Device-E2E-Validierung — device_id-Fix + `--host`-IP-Update

**Datum:** 2026-07-30 · **Ergebnis: bestanden, keine echten Bugs.**

> Hinweis: Dieses Repo ist öffentlich. Die reale Geräte-ID (an die die Cloud-Tokens
> gebunden sind) ist hier **maskiert**; der Prüfpunkt ist stets „die extrahierte ID
> ist identisch mit dem in `devices.json` gespeicherten Wert".

## Ziel
Den device_id-Extraktionsfix gegen **echte Hardware + reale Bibliothek** bestätigen — was die Unit-Tests nicht leisten können (sie nutzen eine *Rekonstruktion* der `discover --debug`-Ausgabe).

## Umgebung
| | |
|---|---|
| Testserver | Raspberry Pi (Linux aarch64), Zugriff via SSH |
| Produktivinstallation | git-Checkout; lief zum Testzeitpunkt noch mit dem **alten, fehlerhaften** Code (`APPLIANCE_ID_RE`/`applianceCodes`) |
| venv | midea-local **6.6.1** (gepinnt), msmart-ng **2026.7.0** (= Projekt-Pin), Python 3.13.5 |
| Zielgerät | eine reale Midea-Anlage (Name/IP/ID lokal bekannt; ID hier maskiert) |
| Interferenz | kein midea-Cron aktiv |

## Sicherheitsmaßnahmen
- Echte `devices.json` vorab gesichert + SHA-256; vor/nach jedem Schritt geprüft → **unverändert**.
- **Test A** in isoliertem Tempordner (leere `midea-local.json`); Rohausgabe (Token/Key enthalten) mode `600`, sofort per `trap` gelöscht.
- **Test B** in Temp-Sandbox mit *eigener* `devices.json`. Isolation strukturell garantiert durch `CONFIG_PATH = Path(__file__).parent/"devices.json"` (kein Env-Override) — belegt durch „devices.json updated: /tmp/…" **und** unveränderte Prüfsumme der echten Konfig.
- Geheimnisse nie ausgegeben (nur Booleans/Anzahlen; 32+-Hex defensiv redigiert).
- Entzerrungspause (60 s) zwischen den Geräteberührungen (Firmware: nur **eine** lokale Verbindung).
- Alle Artefakte am Ende entfernt; Ausgangszustand per Prüfsumme + Verzeichnislisting verifiziert.

## Test A — Regex gegen die *reale* discover-Ausgabe
`python -m midealocal.cli discover --host <Zielgerät> --debug` → exit 0; Analyse der ~27 KB Ausgabe:

| Prüfung | Ergebnis |
|---|---|
| Literal `applianceCodes` vorhanden | **NEIN** |
| → ALTE Regex `APPLIANCE_ID_RE` | `[]` → appliance_id = **None** *(= der Bug)* |
| Literal `device_id` vorhanden | **JA** |
| → NEUE Regex `DEVICE_ID_RE` (erster Nicht-Null) | **die korrekte, in `devices.json` gespeicherte Geräte-ID** |
| (key,token)-Kandidaten | 2 (Cloud-Abruf ok) |

**⇒ An realer Hardware + midea-local 6.6.1 emittiert die Bibliothek `device_id`, nie `applianceCodes`. Die alte Regex scheitert, die neue greift korrekt.**

## Test B — voller End-to-End-Lauf (der ursprüngliche Bug-Fall)
Der gefixte Code, **neues** Gerät: `midea_refresh_tokens.py --name <Testname> --host <Zielgerät>` → exit 0:
```
[<Testname>] 2 candidate(s) found, verifying one by one ...
[<Testname>] Candidate 1/2 verified successfully and saved.
```
Ergebnis-Eintrag in der Sandbox-`devices.json`: derselbe Testname, die korrekte Geräte-ID, token+key gesetzt.

**⇒ Der Ablauf, der vor dem Fix mit „No device ID" (exit 2) abbrach, legt jetzt das Gerät korrekt an — gegen das echte Gerät verifiziert.**

## Fazit & Restzustand
- Der Fix ist **an echter Hardware end-to-end bestätigt** (device_id-Extraktion **und** vollständiger Neu-Gerät-Ablauf).
- Die Produktivinstallation wurde **bewusst nicht angetastet** (lief weiter mit dem alten Code); der Fix liegt auf `main` bereit zum Ausrollen.
- Echte `devices.json` unverändert (SHA-256 vor == nach); sämtliche Test-Artefakte entfernt; Ausgangszustand wiederhergestellt.
