# midea-ieco

> 🇬🇧 **English:** The full English documentation is here: [README.md](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg) ![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-informational.svg)

**Hält iECO auf deiner Midea PortaSplit zuverlässig angeschaltet — automatisch, lokal, ohne App.**

Kleine, zuverlässige Kommandozeilen-Werkzeuge zur lokalen Steuerung des **iECO-Modus** (und des allgemeinen Ein-/Ausschaltzustands) von Midea-Klimaanlagen, einschließlich der Midea PortaSplit und kompatibler Modelle von Comfee, Toshiba, Carrier, Klimaire und anderen. Die Werkzeuge vermeiden im Normalbetrieb die Abhängigkeit von einer instabilen Cloud-Verbindung.

`msmart-ng` steuert iECO direkt über das lokale Netzwerk. Token und Key, die es dazu je Gerät braucht, werden lokal mit `midea-local` beschafft und in `devices.json` gespeichert — **ein Midea-Cloud-Passwort ist nie nötig**, denn diese Tokens sind an das Gerät gebunden, nicht an dein Konto (siehe *[Token-/Key-Paare abrufen](#3-token-key-paare-abrufen)* unten).

**Was es tut**

- Reaktiviert **iECO** automatisch, sobald eine Anlage nicht mehr darin läuft — typischerweise nach einem manuellen Aus/Ein
- Läuft vollständig im **lokalen Netzwerk** (im Normalbetrieb keine Cloud-Abhängigkeit), ausgelöst per cron, Siri-Kurzbefehl oder Homebridge
- **Ändert nie deine Zieltemperatur** — stellt nur sicher, dass iECO bei dem von dir gewählten Sollwert aktiv ist

> ℹ️ **ECO ≠ iECO.** Die ECO-Taste der Fernbedienung (fix 24 °C) und das nur per App aktivierbare iECO (dein eigener Sollwert, adaptiv) sind *verschiedene* Modi. Dieses Projekt behandelt **iECO** — die vollständige Abgrenzung steht unter [Hintergrund: ECO vs. iECO](#hintergrund-eco-vs-ieco).

## Kompatibilität & Voraussetzungen

- **Klimageräte:** Midea PortaSplit und iECO-fähige Midea-Rebrands (Comfee, Toshiba, Carrier, Klimaire, …), bereits in der **MSmartHome- / Midea-Smarthome-App** eingebunden und per WLAN verbunden.
- **Betriebsmodus:** Das Gerät muss im Modus **Kühlen** oder **Heizen** laufen. iECO ist an den Betriebsmodus gebunden und wird in **Auto**, **Entfeuchten** und **Nur Lüften** stillschweigend verworfen — vom Gerät selbst, nicht von diesem Werkzeug. Siehe [Welche Modi iECO unterstützen](#welche-modi-ieco-unterstützen).
- **Ausführender Rechner:** ein kleiner, **dauerhaft laufender** Computer im selben LAN wie die Anlage — Raspberry Pi, Heimserver, NAS oder Mac. **Python 3.11+** (aktuelles Raspberry Pi OS liefert es). (Nicht das iPhone selbst.)
- **Netzwerk:** Der Host muss jede Anlage auf **TCP-Port 6444** erreichen, ohne Client-/AP-Isolation in diesem Segment. Eine **feste IP** (DHCP-Reservierung) wird empfohlen, ist aber nicht zwingend — du kannst sie jederzeit später in `devices.json` ändern. Eine VLAN-Trennung zwischen IoT-Geräten und Computern ist unproblematisch, solange Routing- und Firewall-Regeln diesen Port zulassen; siehe [Netzwerk-Fehlerbehebung](#netzwerk-fehlerbehebung).
- **Midea-App (nur einmalig):** Jede Anlage muss bereits in der **MSmartHome- / Midea-Smarthome-App** eingerichtet und online sein — so kommt sie ins WLAN. Danach wird die App nicht mehr gebraucht, und **diese Skripte fragen nie nach deinem Midea-Konto oder Passwort**: Geräte-Tokens werden lokal beschafft (siehe *[Token-/Key-Paare abrufen](#3-token-key-paare-abrufen)*).

## Zweck & Alternativen

Dieses Tool macht **eine Sache gut**: iECO (und den einfachen Ein-/Aus-/Status) auf deinen Geräten setzen — per Cron oder Kurzbefehl, **ohne Home Assistant oder sonstige dauerlaufende Smart-Home-Serversoftware**. Das ist sein gesamter Zweck.

Wer die volle Klimasteuerung will (Temperatur, Modus, Lüfter, Dashboards, komplexe Automatisierungen) oder **ohnehin Home Assistant betreibt**, nutzt besser eine Home-Assistant-Integration. Die beiden verbreiteten unterscheiden sich allerdings genau in dem Punkt, um den es hier geht:

- [`midea-ac-py`](https://github.com/mill1000/midea-ac-py) basiert auf `msmart-ng` — derselben Bibliothek, die dieses Projekt zur Steuerung nutzt — und **bietet iECO** als Steuerung an (unter den „Advanced controls").
- [`midea_ac_lan`](https://github.com/wuwentao/midea_ac_lan) bietet viele Steuerungen, **kann iECO aber nicht setzen**. Es basiert auf `midea-local`, das überhaupt keine iECO-Eigenschaft kennt (geprüft gegen 6.6.1 und 6.11.0 im Juli 2026); das dort angebotene `eco`-Preset ist der einfache, fest auf 24 °C gesetzte ECO-Modus von oben — eine leichte Verwechslung, die genau dieser Dokumentation selbst passiert ist, bis ein Nutzer darauf hinwies.

In beiden Fällen liefert eine Integration nur die *Steuerung*. iECO nach einem Verlust wieder zu aktivieren — typischerweise nach einem Aus/Ein außerhalb der App — erfordert zusätzlich eine Automatisierung. Dieses Projekt ist für den engeren Fall gedacht: „Ich habe einen Raspberry Pi / Mac / kleinen Server, will kein Home Assistant und einfach nur, dass iECO an bleibt."

## Schnellinstallation per Einzeiler

Der schnellste Weg ist der automatisierte Installer. Er funktioniert auf Debian/Ubuntu/Raspberry Pi OS, Fedora/RHEL, Arch Linux, Alpine, openSUSE und macOS (mit Homebrew):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
```

Standardmäßig legt der Installer alle Programmdateien in `/opt/local/midea-ieco` ab und einen kleinen Wrapper-Befehl namens `midea-ieco` in `/opt/local/bin`. Beide Pfade sind konfigurierbar — entweder durch Anpassen der Variablen `DEFAULT_INSTALL_DIR` / `DEFAULT_BIN_DIR` am Anfang von `install.sh` (nützlich, wenn das Skript manuell heruntergeladen wurde), oder per Umgebungsvariable ganz ohne Bearbeitung:

```bash
MIDEA_IECO_DIR=/eigener/pfad MIDEA_IECO_BIN_DIR=/eigener/bin \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
```

Installer, Update-Modus, das gesamte Onboarding **und beide Kommandozeilen-Werkzeuge zur Laufzeit** sind **zweisprachig (Deutsch/Englisch)** — inklusive `--help` und sämtlicher Status- und Fehlermeldungen. Die Sprache richtet sich automatisch nach deiner Locale — Deutsch bei einer `de_*`-Locale (`LANG` / `LC_ALL` / `LC_MESSAGES`), sonst Englisch. **Wichtig:** Viele Raspberry Pis laufen mit einer `en_*`-Locale (z. B. `en_GB`) und zeigen dann Englisch; auch Cron-Jobs laufen meist ganz ohne Locale. Erzwinge Deutsch über die Umgebungsvariable `MIDEA_IECO_LANG=de` – praktisch direkt beim Einzeiler oder als Zeile in der Crontab:

```bash
MIDEA_IECO_LANG=de \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
```

Ein manuell heruntergeladenes Skript akzeptiert zusätzlich das Flag `--lang en|de` (z. B. `install.sh --lang de`).

Existiert das Installationsverzeichnis noch nicht, legt der Installer es an und überträgt dir den Besitz (`sudo` nur, falls der übergeordnete Ordner nicht beschreibbar ist). Ein bereits bestehendes Verzeichnis wird nie übernommen: ist das Installationsverzeichnis vorhanden, aber nicht beschreibbar, bricht der Installer ab und zeigt deine Optionen (anderen Pfad über `MIDEA_IECO_DIR` wählen, Rechte selbst korrigieren oder Verzeichnis entfernen). Der kleine `midea-ieco`-Wrapper wird bei einem root-eigenen Bin-Verzeichnis (z. B. MacPorts’ `/opt/local/bin`) mit einem einzigen `sudo install`-Schritt abgelegt, ohne dessen Besitzverhältnisse zu ändern.

> **Bevor du beginnst:** Stelle sicher, dass jede Anlage bereits in der **MSmartHome- / Midea-Smarthome-App** eingebunden und per WLAN verbunden ist. Der Installer braucht **kein Midea-Konto und kein Passwort** — er erkennt die Geräte im lokalen Netzwerk und holt deren Tokens direkt.

Der Installer erledigt automatisch:

1. Erkennung von Betriebssystem und Paketmanager, Installation fehlender Voraussetzungen (`python3`, `python3-venv`, `git`/`curl`)
2. Anlegen einer virtuellen Python-Umgebung und Installation von `msmart-ng` und `midea-local`
3. Geräteerkennung im lokalen Netzwerk (ein UDP-Broadcast — kein Cloud-Login)
4. Bestätigung der erkannten IP/Geräte-ID je Anlage und nur noch Namensvergabe (oder alles von Hand) zum Aufbau von `devices.json`
5. Abruf der Token-/Key-Paare und sichere Speicherung (`chmod 600`)
6. Anlegen der Wrapper-Befehle `midea-ieco`, `midea-ieco-update` und `midea-ieco-refresh-tokens`, Angebot zur Aufnahme des Bin-Verzeichnisses in den `PATH` sowie optionaler Testlauf und optionale Cron-Job-Einrichtung

### Manuelle Installation (Alternative)

Wer alles selbst einrichten möchte, statt `install.sh` zu nutzen:

```bash
# 1. Repository klonen oder als ZIP herunterladen
git clone https://github.com/tuxbox78/midea-ieco.git
cd midea-ieco

# 2. Virtuelle Python-Umgebung anlegen und Abhängigkeiten installieren
# (Pflicht auf Debian/Ubuntu/Raspberry Pi OS – pip NICHT direkt als Root verwenden!)
sudo apt-get install -y python3-venv   # nur nötig, falls python3-venv noch nicht installiert ist
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Geräte im lokalen Netzwerk ermitteln und je IP und Geräte-ID notieren
#    (ein lokaler UDP-Broadcast — kein Cloud-Login):
python3 -c "from midealocal.discover import discover; [print(d.get('ip_address'), d.get('device_id')) for d in (discover() or {}).values()]"

# 4. Im Router feste IP-Adressen für die Klimaanlagen vergeben
#    (DHCP-Reservierung nach MAC-Adresse), damit die Konfiguration dauerhaft stabil bleibt.
#    Nicht zwingend — die IP kann später auch direkt in devices.json geändert werden.

# 5. devices.json aus der Vorlage erstellen, dann bearbeiten (siehe „Einmalige Einrichtung" unten)
cp devices.example.json devices.json

# 6. Token-/Key-Paare für alle Geräte abrufen (kein Midea-Konto/Passwort nötig)
python3 midea_refresh_tokens.py --all

# 7. Test: iECO für ein Gerät aktivieren (Gerät muss im Netz erreichbar sein)
python3 midea_ieco_ensure.py Wohnzimmer

# Der SSH-/Kurzbefehle-Wrapper midea_ieco_ensure.sh ist bereits ausführbar;
# die Cron-Beispiele weiter unten rufen den venv-Python direkt auf und
# brauchen ihn nicht. Falls die Download-Methode das Ausführbar-Bit
# verworfen hat, so wiederherstellen:
chmod +x midea_ieco_ensure.sh
```

## Einmalige Einrichtung (Details zum manuellen Weg)

### 1. Geräte-IDs und IP-Adressen ermitteln

```bash
python3 -c "from midealocal.discover import discover; [print(d.get('ip_address'), d.get('device_id')) for d in (discover() or {}).values()]"
```

Das ist ein lokaler UDP-Broadcast — kein Cloud-Login. Notiere Geräte-ID und IP-Adresse für jedes Gerät.

### 2. `devices.json` anlegen

```json
{
  "devices": [
    {
      "name": "Wohnzimmer",
      "ip": "192.168.0.186",
      "port": 6444,
      "id": 888888888888881,
      "token": "",
      "key": ""
    },
    {
      "name": "Schlafzimmer",
      "ip": "192.168.0.185",
      "port": 6444,
      "id": 888888888888882,
      "token": "",
      "key": ""
    }
  ]
}
```

Token und Key können anfangs leer bleiben; `midea_refresh_tokens.py` holt sie im nächsten Schritt.

### 3. Token-/Key-Paare abrufen

```bash
python3 midea_refresh_tokens.py --all
```

Das Skript führt `python3 -m midealocal.cli discover --debug` aus, extrahiert Token und Key aus dessen Ausgabe, **verifiziert jeden Kandidaten gegen das tatsächliche Gerät** und schreibt den funktionierenden zurück in `devices.json` (atomar, mit `chmod 600`). **Ein Midea-Konto oder Passwort ist dabei nicht im Spiel.**

> **Woher kommen Token und Key — und warum kein Midea-Passwort?** Sie sind an das **Gerät** (dessen UDP-ID) gebunden, nicht an ein Cloud-Konto, und jede authentifizierte Cloud-Session darf sie für jedes beliebige Gerät anfordern. Heute vergibt nur noch die **NetHome-Plus**-Cloud-API sie: Midea hat den `getToken`-Endpunkt der MSmartHome- und Meiju-Clouds serverseitig abgeschaltet (sie antworten mit `errorCode 3004 "value is illegal"` — im Juli 2026 real gegen ein Gerät verifiziert), und dein MSmartHome-Konto existiert auf der NetHome-Plus-Cloud gar nicht. `midea-local` meldet sich deshalb — genau wie `msmart-ng` — mit seinem **eingebauten** Hilfskonto an, und dieses Projekt übergibt **gar keine Zugangsdaten**. Der eigentliche Schutz liegt woanders: Jeder Kandidat wird vor dem Speichern gegen das Gerät verifiziert, und ein bestehender Wert wird nur bei Erfolg überschrieben — sollte Midea auch diese letzte API abschalten, bleiben deine zuletzt gültigen Tokens erhalten und die lokale Steuerung läuft weiter. (Der Mechanismus ist in `midea-local` 6.6.1 und 6.10.0 identisch; gegen die gepinnte 6.6.1 verifiziert.)

Ein neues Gerät lässt sich auch direkt über Name und IP-Adresse hinzufügen:

```bash
python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
```

Derselbe Aufruf **aktualisiert** auch die gespeicherte IP eines bereits unter diesem Namen vorhandenen Geräts: Die neue Adresse wird für diesen Refresh verwendet und nur bei Erfolg übernommen — eine vertippte, nicht erreichbare IP ersetzt so nie eine funktionierende. (Bei `--all` oder wenn zwei Geräte denselben Namen tragen, lässt sich `--host` nicht eindeutig zuordnen und wird nicht angewendet.)

## Aktualisieren

Um eine bestehende Installation auf den neuesten Stand zu bringen:

```bash
midea-ieco-update
```

Das erneuert **den Code, die gepinnten Abhängigkeiten und die Wrapper-Befehle** und rührt `devices.json` sowie Cron-Jobs **nicht** an. Es funktioniert unabhängig davon, ob der Installer per `git` oder per ZIP-Download eingerichtet hat. Der Befehl wird bei der Installation automatisch angelegt, direkt neben `midea-ieco` im Bin-Verzeichnis (liegt also im `PATH`, sofern dieses Verzeichnis darin ist). Liegt aus 0.1.x noch eine `credentials.json` auf der Platte, weist der Updater darauf hin, dass sie nun ungenutzt ist und entfernt werden kann.

Intern startet er den Installer in einem eigenen Update-Modus (`install.sh --update`): keine Einrichtungsfragen, keine Neukonfiguration. Er lädt die neuen Dateien zuerst und startet dann die frisch geladene Skriptkopie neu, sodass die laufende Update-Ausführung nie die Datei ist, die gerade überschrieben wird. Anschließend zeigt er die Versionsänderung an und verweist auf [`CHANGELOG.md`](CHANGELOG.md).

**Ein erneuter Installer-Lauf ist ebenfalls sicher.** Startest du `install.sh` auf einem bereits eingerichteten System erneut, erkennt es die vorhandene `devices.json`, überspringt das Onboarding, frischt Code/Abhängigkeiten/Wrapper auf und beendet sich — deine Gerätekonfiguration wird nicht überschrieben. Zum bewussten Neu-Einrichten dient `install.sh --reconfigure` (die vorhandene `devices.json` wird vorher nach `devices.json.bak` gesichert).

> **Hinweis:** Wer auf `git`-basierte Updates setzt, sollte `install.sh` (oder andere versionierte Dateien) nicht direkt editieren — lokale Änderungen lassen den Fast-Forward-`git pull` aussetzen; der Updater meldet das und macht mit den vorhandenen Dateien weiter. Nutze stattdessen die Umgebungsvariablen `MIDEA_IECO_DIR` / `MIDEA_IECO_BIN_DIR`.

## Tägliche Nutzung

### Anzeigen, was konfiguriert ist

`midea-ieco` ohne Argument – oder mit `list` – zeigt sofort eine netzwerkfreie Übersicht: was das Werkzeug tut, wo die Konfiguration liegt, welche Geräte konfiguriert sind (nur Name, IP und Port, **niemals** Token oder Key) und die wichtigsten Befehle:

```bash
midea-ieco
midea-ieco list
```

Es wird dabei kein Gerät kontaktiert, die Ausgabe erscheint also sofort – auch wenn gerade nichts erreichbar ist. Ist das Bin-Verzeichnis nicht im `PATH`, nutze `venv/bin/python3 midea_ieco_ensure.py list`.

### iECO sicherstellen (schaltet Gerät bei Bedarf ein)

```bash
python3 midea_ieco_ensure.py Wohnzimmer
python3 midea_ieco_ensure.py all
```

Dies ändert **nicht** die Zieltemperatur. Es stellt nur sicher, dass iECO bei der bereits eingestellten Temperatur aktiv ist.

### iECO nur reaktivieren, wenn das Gerät bereits läuft

Empfohlen für Cron-Jobs:

```bash
python3 midea_ieco_ensure.py all --only-if-on
```

Mit `--only-if-on` schaltet das Skript keine Anlage ein. Eine ausgeschaltete Anlage wird nicht angerührt; iECO wird nur gesetzt, wenn eine Anlage gerade läuft und iECO deaktiviert ist. So sind häufige Cron-Ausführungen sicher, ohne eine absichtlich ausgeschaltete Anlage zu starten.

### Token-/Key-Werte auffrischen

Falls ein Gerät `Connection reset`, einen Timeout oder ein Token-/Key-Problem meldet:

```bash
midea-ieco-refresh-tokens --name Wohnzimmer
midea-ieco-refresh-tokens --all
```

Dieser Wrapper-Befehl wird neben `midea-ieco` installiert; ohne Bin-Verzeichnis im `PATH` lautet das Äquivalent `venv/bin/python3 midea_refresh_tokens.py --all`. Er braucht **kein Midea-Konto und kein Passwort**.

In der Praxis bleiben Geräte-Tokens oft lange gültig. Auffrischen ist sinnvoll, wenn eine Anlage neu gekoppelt oder neu mit dem Netzwerk verbunden wurde oder wenn die lokale Authentifizierung zu scheitern beginnt. Dein Midea-Konto-Passwort spielt hier keine Rolle — es wird nie verwendet.

## Cron-Automatisierung

Falls du nicht die automatische Cron-Einrichtung von `install.sh` genutzt hast, Crontab bearbeiten mit `crontab -e`:

```cron
# Alle 20 Minuten: iECO reaktivieren, ohne Geräte einzuschalten
*/20 * * * * cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Jeden Sonntag um 03:00 Uhr: Geräte-Tokens vorsorglich auffrischen
0 3 * * 0 cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Log-Rotation nicht vergessen, z. B. mit `logrotate` oder einfach:

```cron
0 0 1 * * truncate -s 0 /opt/local/midea-ieco/ieco.log /opt/local/midea-ieco/refresh.log
```

### Einen verpassten Token-Refresh nachholen

Die Wochenzeile oben hilft nur, wenn der Rechner in genau diesem Moment läuft. Ein Host, der sonntags um 03:00 Uhr ausgeschaltet ist, überspringt den Refresh **still** — und abgelaufene Tokens sind der häufigste Weg, auf dem dieses Werkzeug ausfällt. Eine weitere Zeile schließt die Lücke:

```cron
# Bei jedem Start: Tokens auffrischen, aber nur wenn der Wochenlauf wirklich ausfiel
@reboot sleep 120 && cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all --only-if-due >> refresh.log 2>&1
```

`--only-if-due` ist das, was diese Zeile bei jedem Start unbedenklich macht. Das Werkzeug führt neben `devices.json` eine kleine Datei `refresh_state.json` und frischt nur auf, wenn der letzte **vollständige** Lauf mindestens 6 Tage und der letzte Versuch mindestens 24 Stunden zurückliegt. Sonst schreibt es eine Zeile und endet mit 0, ohne ein einziges Gerät anzusprechen:

```text
Token-Refresh ist noch nicht faellig - nichts getan (letzter vollstaendiger Lauf: 2026-07-26T03:00:11Z, letzter Versuch: 2026-07-26T03:00:04Z).
```

Beide Wächter werden gebraucht. `@reboot` bedeutet „der cron-Daemon wurde gestartet", nicht „das System wurde gebootet" — schon ein Paket-Update des cron-Dienstes löst es aus. Ohne den zweiten Wächter liefe ein oft neu startender Rechner also jedes Mal mit einem vollen Refresh gegen deine Geräte. Das `sleep 120` gibt dem Netzwerk einen Moment zum Hochkommen; es ist eine Kulanzfrist, kein Warten auf das Netz — scheitert der Lauf trotzdem, bleibt die Wochenzeile der Auffang.

Jeder `--all`-Lauf hält diese Datei aktuell, auch der wöchentliche und der, den der Installer bei der Einrichtung ausführt — auf einer laufenden Installation findet der Nachholer daher nichts zu tun und bleibt still.

> **Bei einer Neuinstallation trägt der Installer diese Zeile mit ein** — er bietet alle vier Zeilen gemeinsam an. Eine Installation, die den Marker `# midea-ieco-managed` bereits trägt, bleibt grundsätzlich unangetastet; eine *bestehende* Einrichtung bekommt die Zeile also nicht automatisch, sondern ergänzt sie von Hand aus dem Block oben. `--only-if-due` setzt `--all` voraus — zusammen mit `--name` endet es mit einem Nutzungsfehler, denn der gespeicherte Zustand ist eine Aussage über alle Geräte, nicht über ein einzelnes.

### Was der Installer zu einer bestehenden Crontab sagt

Läuft `install.sh` auf einem bereits eingerichteten Rechner — meist als schlichter
erneuter Aufruf des `curl … | bash`-Einzeilers —, liest er deine Crontab und zeigt
unter Umständen einen der folgenden Hinweise. **Keiner der drei Hinweise ändert
etwas** — sie zeigen nur Zeilen zum Übernehmen an.

Das ist eine Aussage über die Hinweise, nicht über den Installer insgesamt. Trägt
deine Crontab den Marker `# midea-ieco-managed`, wird gar nichts hineingeschrieben.
Trägt sie ihn **nicht** und du beantwortest die Cron-Frage mit *ja*, hängt der
Installer seine vier Zeilen an das an, was schon darin steht. Er hängt an, statt zu
ersetzen, und er schreibt nichts, wenn zweimaliges Lesen der Crontab
Unterschiedliches ergab — aber er schreibt. Für eine Crontab, an der dir liegt,
lohnt vorher ein `crontab -l > crontab.backup`.

- **„mindestens ein Job ist nicht aktiv"** — deine Crontab trägt den Marker
  `# midea-ieco-managed`, gilt dem Installer damit als eingerichtet und wird nicht
  angefasst. Einer der beiden Jobs wurde aber inzwischen gelöscht oder
  auskommentiert, dieser Teil des Produkts läuft also still gar nicht. Die fehlende
  Zeile wird fertig zum Einfügen angezeigt. Ein Job, der über `midea_ieco_ensure.sh`
  oder über die Befehle `midea-ieco` / `midea-ieco-refresh-tokens` läuft, zählt als
  aktiv — du wirst also nicht aufgefordert, eine zweite Zeile anzulegen. Zwei Formen
  täuschen die Prüfung trotzdem: ein Job aus einer Zeile *ohne* unseren Marker und
  einer, der unter einem Namen läuft, den die Prüfung nicht kennen kann (ein eigenes
  Wrapper-Skript, eine Shell-Variable, eine Kommandosubstitution). Beide lassen sie
  einen Job melden, der in Wahrheit läuft — prüfe deshalb vor dem Übernehmen
  `crontab -l`: eine zweite Zeile bedeutet zwei Verbindungen alle 20 Minuten auf
  eine Anlage, die nur eine verträgt. Die `@reboot`-Nachhol-Zeile zählt bewusst
  **nicht** zu den erwarteten Jobs: sie ist ein Zusatz und kein Ersatz, und sie
  einzufordern hieße, jeder älteren Installation einen fehlenden Job zu melden.
- **„Das Lesen der aktuellen Crontab lieferte zweimal hintereinander
  Unterschiedliches"** — erscheint nur, wenn du die Cron-Frage mit *ja*
  beantwortest, und die stellt der oben beschriebene schlichte erneute Aufruf nie:
  er endet vorher. Zu sehen bekommst du diesen Hinweis bei der Ersteinrichtung
  oder mit `--reconfigure`. `crontab -l` meldet „es gibt keine Crontab" und „ich
  konnte sie nicht lesen" auf genau dieselbe Weise; hielte man den zweiten Fall
  für den ersten, würde deine Crontab durch unsere vier Zeilen ersetzt. Der
  Installer liest sie deshalb zweimal und schreibt bei abweichendem Ergebnis
  nichts, sondern bittet dich, die Zeilen von Hand zu ergänzen.
- **„Deine bestehenden Cron-Jobs setzen `MIDEA_IECO_LANG` nicht"** — die Jobs stammen
  aus der Zeit vor der Sprachweitergabe und protokollieren auf Englisch, weil Cron
  ohne Locale läuft. Die korrigierten Zeilen werden angezeigt; ob du sie übernimmst,
  entscheidest du.

## Logs

Die Cron-Jobs sind das Einzige, was überhaupt Logdateien schreibt. `midea_ieco_ensure.py` und `midea_refresh_tokens.py` geben nur auf die Standard-Aus-/Fehlerausgabe aus; die Cron-Zeilen oben lenken das per `>> …log 2>&1` in Dateien um. **Ein manueller Aufruf (oder per SSH/Siri) schreibt nichts in eine Datei** — die Ausgabe landet stattdessen im Terminal. Ohne eingerichteten Cron-Job existieren keine Logdateien.

### Welche Dateien, und wo

| Datei | Geschrieben von | Inhalt |
|---|---|---|
| `ieco.log` | dem 20-Minuten-Job `midea_ieco_ensure.py all --only-if-on` | ein Block pro Lauf: Vorher-/Nachher-Zustand jedes Geräts und das Gesamtergebnis |
| `refresh.log` | dem wöchentlichen Job `midea_refresh_tokens.py --all` | ein Block pro Lauf: Token-Abruf und -Verifikation je Gerät |

Beide liegen im Installationsverzeichnis — standardmäßig `/opt/local/midea-ieco/`, bzw. dort, wohin `MIDEA_IECO_DIR` zeigt. Beide sind git-ignoriert (`*.log`) und werden daher nie versioniert.

Um einen **manuellen** Lauf genauso festzuhalten, selbst umleiten:

```bash
cd /opt/local/midea-ieco
venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1
```

### Wie ein Lauf aussieht

Ein gesunder `ieco.log`-Block — das Wohnzimmergerät war an, iECO aus (wird also aktiviert); das Schlafzimmer war aus und bleibt unangetastet:

```text
[Wohnzimmer] Status vor Aktion: power=True, mode=COOL (2), ieco=False, eco=False
[Wohnzimmer] Status nach Aktion: power=True, mode=COOL (2), ieco=True, eco=False
[Wohnzimmer] OK: iECO ist aktiv (vom Geraet bestaetigt).
[Schlafzimmer] --only-if-on aktiv und Geraet ist aus. Keine Aktion, keine weiteren Abfragen.
Gesamtergebnis: OK.
```

Ein Gerät, das in einem Modus läuft, der iECO nicht trägt, wird benannt und in Ruhe gelassen — mit `--only-if-on` ist das kein Fehler (siehe [Welche Modi iECO unterstützen](#welche-modi-ieco-unterstützen)):

```text
[Buero] Betriebsmodus AUTO (1) unterstuetzt kein iECO (nur COOL oder HEAT).
[Buero] --only-if-on aktiv: keine Aktion, kein Fehler.
```

Jede Zeile mit **FEHLER** kennzeichnet ein Problem bei genau diesem Gerät; `Gesamtergebnis: FEHLER` bedeutet, dass mindestens ein Gerät nicht in Ordnung war. Da die Cron-Zeilen `2>&1` nutzen, landen auch Warnungen der zugrunde liegenden `msmart-ng`-Bibliothek in derselben Datei. Die Logs enthalten Gerätenamen, IP-Adressen und den Ein-/iECO-Zustand; deine Geräte-Token und Keys stehen in der `chmod 600`-`devices.json`, und die Werkzeuge geben sie nicht aus. In jeder Meldung, die durch den Meldungskatalog eines der beiden Werkzeuge läuft, werden in jedem eingesetzten Wert Folgen von 32 oder mehr Hex-Zeichen durch eine Markierung wie `[hex:128]` ersetzt. Das ist keine Formsache: Die rohe Cloud-Antwort, die nach einem gescheiterten Token-Abruf zitiert wird, enthält die Token-Liste der Cloud, und `msmart-ng` wirft Fehler mit rohen Paket-Abzügen darin (`Packet is too short: <hex>`).

Der Katalog ist nicht der einzige Weg in diese Datei, deshalb schließen zwei weitere Wächter zwei weitere Kanäle: Was eine Bibliothek über Pythons `logging` schreibt, läuft durch einen schwärzenden Log-Handler, und ein Traceback aus einer ungefangenen oder verketteten Ausnahme durch einen schwärzenden `excepthook`. Das ist praktisch relevant — gegen das gepinnte `msmart-ng` 2026.7.0 geprüft: Kein `warning`/`error`-Eintrag nennt Token oder Key, aber vier von ihnen geben einen rohen Empfangspuffer der Geräteverbindung aus — auf `WARNING`, also aktiv bei der Stufe, die die Werkzeuge einstellen. Auf `INFO` protokolliert die Bibliothek den lokalen Schlüssel des Geräts im Klartext.

Zwei Grenzen sind wichtig, denn nichts davon ist eine Zusage über die ganze Datei:

- Die Schwärzung erkennt nur *zusammenhängendes* Hex. Derselbe Wert mit Leerzeichen, in Gruppen oder als Python-Byte-String dargestellt wird nicht erkannt. Das Muster zu erweitern ginge nicht, ohne auch Geräte-IDs und Prüfsummen zu verbergen — deshalb ist es dokumentiert statt halb repariert.
- Die Logdateien entstehen mit deiner normalen umask, sind also meist für alle lesbar — anders als `devices.json`.

`tests/KNOWN_GAPS.md` (Lücke 11) listet die bisher gefundenen Kanäle auf, welcher Wächter sie jeweils abdeckt und was offen bleibt — auch Wege, die kein Wächter sieht, etwa `warnings.warn()` und Zugangsdaten, die nie hexadezimal waren.

> **In welcher Sprache deine Logs stehen.** Beide Werkzeuge sind zweisprachig und
> nutzen **Englisch** als Voreinstellung; Deutsch erscheint nur, wenn
> `MIDEA_IECO_LANG=de` gesetzt ist (oder die Locale `de_*` lautet). Der Installer
> schreibt die bei der Einrichtung ermittelte Sprache in die Cron-Zeile, deine
> Logdateien behalten sie also bei. Richtest du auf einem System mit englischer
> Locale ein — etwa einem Raspberry Pi mit `en_GB` —, stehen dort `Status before
> action`, `OK: iECO is active`, `Overall result: OK.` und im Fehlerfall `ERROR`.
> Die Suchbefehle unten decken deshalb beide Sprachen ab.

### Lesen und Beobachten

```bash
tail -n 40 /opt/local/midea-ieco/ieco.log     # letzter Lauf (neueste Zeilen unten)
tail -f  /opt/local/midea-ieco/ieco.log       # live mitverfolgen
grep -nE 'FEHLER|ERROR' /opt/local/midea-ieco/*.log    # direkt zu Problemen springen
grep -E 'Gesamtergebnis|Overall result' /opt/local/midea-ieco/ieco.log | tail -n 5
```

> **Keine Zeitstempel.** Die Skripte geben kein Datum/keine Uhrzeit aus; Einträge sind nur nach Anhänge-Reihenfolge sortiert (neueste unten). Zum Voranstellen eines Zeitstempels durch `ts` leiten (aus dem Paket `moreutils`) — Achtung: Cron behandelt `%` besonders, daher als `\%` escapen:
>
> ```cron
> */20 * * * * cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on 2>&1 | ts '\%Y-\%m-\%d \%H:\%M:\%S' >> ieco.log
> ```

### Gegen unbegrenztes Wachstum: Rotation

Ohne Rotation wächst `ieco.log` alle 20 Minuten um einen Block. Zwei Wege:

- **Einfach (mitgeliefert):** der optionale monatliche `truncate`-Cron-Job aus [Cron-Automatisierung](#cron-automatisierung) leert beide Dateien am 1. jedes Monats. Sie bleiben winzig, es gibt aber **keine** Historie — direkt nach dem Lauf sind die Dateien leer, das ist normal.
- **Mit Historie:** stattdessen `logrotate` nutzen (und dann den `truncate`-Job weglassen). `/etc/logrotate.d/midea-ieco` anlegen:

  ```
  /opt/local/midea-ieco/*.log {
      weekly
      rotate 8
      compress
      missingok
      notifempty
      copytruncate
  }
  ```

  `copytruncate` ist hier wichtig: Das Log ist eine per Append beschriebene Datei ohne Daemon, dem man ein Signal geben könnte — logrotate kopiert es daher und leert es an Ort und Stelle, statt es umzubenennen.

> **Rechte.** Cron legt die Logs typischerweise für alle lesbar an (Modus 0644, abhängig von der umask des Daemons); die Konfigdateien mit Geheimnissen bleiben bei 0600. Die Logs enthalten keine Geheimnisse — falls dir Gerätenamen/IPs aber sensibel sind, einmalig `chmod 600 /opt/local/midea-ieco/*.log` ausführen: Anhängen und `truncate` erhalten die Rechte, es bleibt also bestehen.

## Siri und iOS Kurzbefehle

Die einfachste Lösung ohne zusätzliche Serversoftware ist die native iOS-Kurzbefehl-Aktion **Skript per SSH ausführen**.

### Voraussetzungen auf dem Linux-Host

- Ein laufender OpenSSH-Server, der über das lokale Netzwerk oder per VPN erreichbar ist
- Ein dedizierter SSH-Schlüssel für das iPhone (empfohlen anstelle von Passwort-Authentifizierung)

### Einrichtung

1. In der Kurzbefehle-App auf dem iPhone einen neuen Kurzbefehl anlegen und die Aktion **Skript per SSH ausführen** hinzufügen.
2. Host, Benutzernamen und Authentifizierungsmethode eintragen (SSH-Schlüssel empfohlen).
3. Folgenden Befehl eintragen:

   ```bash
   /opt/local/bin/midea-ieco Wohnzimmer
   ```

   oder, ohne den Wrapper:

   ```bash
   cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py Wohnzimmer
   ```

4. Den Kurzbefehl benennen, z. B. **Wohnzimmer iECO**.
5. Per Siri aufrufen, z. B.: *„Hey Siri, starte Wohnzimmer iECO."*

> **Tipp für nicht-interaktive SSH-Sitzungen:** Verwende `venv/bin/python3` direkt statt `source venv/bin/activate && python3`. Das ist zuverlässiger, weil nicht-interaktive Shells `source` unterschiedlich behandeln können.

Für alle Geräte `all` anstelle des Gerätenamens verwenden. `--only-if-on` hinzufügen, wenn Siri keine absichtlich ausgeschaltete Anlage einschalten soll.

### Alternative: Homebridge und HomeKit

Wer reguläre Schalter, Statusanzeige, Szenen und Automatisierungen in Apple Home bevorzugt, kann Homebridge mit `homebridge-cmd4` nutzen. Damit lassen sich beliebige Shell-Befehle auf Ein-/Aus-/Statusoperationen abbilden, z. B. `midea_ieco_ensure.py Wohnzimmer` als „Ein"-Aktion. Das ist aufwändiger als die SSH-Kurzbefehle-Lösung, bietet aber vollständige HomeKit-Integration.

## Welche Modi iECO unterstützen

iECO ist **an den Betriebsmodus gebunden**. Das Gerät nimmt den Befehl in jedem Modus entgegen, verwirft ihn aber stillschweigend in Modi, die iECO nicht tragen — und nichts im Display weist darauf hin.

Gemessen an einer Midea PortaSplit (Modell `2060008E`) am 27. Juli 2026, alle fünf Modi nacheinander über die Fernbedienung durchgeschaltet:

| Betriebsmodus | iECO |
|---|---|
| **Kühlen** (2) | ✅ funktioniert |
| **Heizen** (4) | ✅ funktioniert |
| Auto (1) | ❌ verworfen |
| Entfeuchten (3) | ❌ verworfen |
| Nur Lüften (5) | ❌ verworfen |

Das deckt sich mit der internen Capability-Dekodierung von `msmart-ng` (`1,3,8 - Cool, 3,4,8 - Heat`): Je nach Modell ist iECO an Kühlen, an Heizen oder an beides gebunden — ein anderer Modus taucht dort nie auf. Bei manchen Geräten funktioniert Heizen daher womöglich ebenfalls nicht, aber **kein** Gerät unterstützt Auto, Entfeuchten oder Nur Lüften.

**Die praktische Folge: Wer seine Anlage im Auto-Modus betreibt, kann iECO nicht nutzen** — weder über dieses Werkzeug noch über die Midea-App. Dafür muss auf Kühlen (oder Heizen) umgestellt werden.

`midea_ieco_ensure.py` prüft den Modus, bevor es irgendetwas unternimmt, und versucht keinen Schreibvorgang mehr, der ohnehin nicht gelingen kann:

```text
[Buero] Betriebsmodus AUTO (1) unterstuetzt kein iECO (nur COOL oder HEAT).
```

Mit `--only-if-on` (dem empfohlenen Cron-Job) gilt das bewusst **nicht** als Fehler — ein absichtlich gewählter Modus ist kein Defekt, und der Lauf bleibt ruhig. Beim ausdrücklichen Aufruf (`midea-ieco Buero`) endet das Skript dagegen mit einem Exit-Code ungleich 0, weil du iECO wolltest und nicht bekommen hast; geschaltet wird in dem Fall nichts, auch nicht der Ein-/Aus-Zustand.

> **Eigenes Gerät nachmessen:** Verhält sich dein Modell anders, prüft `tools/probe_ieco_current_mode.py`, ob iECO im gerade eingestellten Modus hält. Modus per Fernbedienung setzen, dann einmal pro Modus aufrufen. Ergebnisse sind als Issue sehr willkommen — sie erweitern diese Tabelle über ein einzelnes Modell hinaus.

## Netzwerk-Fehlerbehebung

Erscheinen bei jeder Anfrage Meldungen wie `No response from host`, liegen die häufigsten Ursachen hier:

- **Client-Isolation / AP-Isolation** im Router für das WLAN, mit dem die Klimaanlage verbunden ist, ist aktiviert — für das betreffende Netzwerksegment deaktivieren
- **VLAN-Trennung** zwischen IoT-Geräten und Computern — sicherstellen, dass Server und Klimaanlagen im selben VLAN sind, oder eine Firewall-Regel für TCP-Port 6444 anlegen
- **IP-Adresse hat sich geändert** — immer feste IP-Adressen (DHCP-Reservierung nach MAC-Adresse) im Router vergeben; falls sie sich doch geändert hat, das Werkzeug mit `midea_refresh_tokens.py --name <Gerät> --host <neueIP>` auf die neue Adresse zeigen (die gespeicherte IP wird bei erfolgreichem Refresh übernommen), oder `devices.json` manuell bearbeiten
- **Gerät befindet sich im WLAN-Energiesparmodus / Schlafmodus** — Erreichbarkeit prüfen mit `ping 192.168.x.x` und `nc -zv 192.168.x.x 6444`
- **Firewall auf dem Server** blockiert ausgehende Verbindungen zu Port 6444 — prüfen mit `iptables -L` oder `ufw status`

### Wenn die Token-Verifikation fehlschlägt

`midea_refresh_tokens.py` nennt für jeden abgelehnten Kandidaten den Grund. Die Ausgabe erfolgt **standardmäßig auf Englisch** und auf Deutsch, sobald die Locale das hergibt — mit `MIDEA_IECO_LANG=de` (oder `en`) lässt sich die Sprache erzwingen. Für Cron-Jobs lohnt sich dieser Eintrag, da Cron meist ohne gesetzte Locale läuft und die Logs sonst englisch wären.

Die Unterscheidung der Ursachen ist wichtig, denn sie haben nichts miteinander zu tun:

| Meldung | Bedeutung | Wo ansetzen |
|---|---|---|
| *Gerät hat den Token aktiv abgelehnt (ERROR-Antwort des Geräts)* | Das Gerät ist erreichbar und hat geantwortet — es weist nur diesen Token zurück. Netzwerk und Firewall sind in Ordnung. | Der Token passt nicht zu diesem Gerät. Mögliche Ursachen: eine Firmware, die die lokale Anmeldung anders handhabt, oder eine udpId-Variante, die die Cloud-Abfrage nicht abdeckt |
| *Gerät hat geantwortet, aber der Key entschlüsselt die Antwort nicht* | Der Handshake kam zurück, es ist auch die richtige Anlage — nur der Key-Teil des Paares gehört nicht dazu | Das deutlichste Zeichen für veraltete Zugangsdaten. Token-Abruf erneut laufen lassen |
| *Gerät nimmt die Verbindung an, antwortet aber nicht* | TCP-Verbindung steht, es kommt aber nichts zurück | Meist die Einzelverbindungs-Grenze unten. Midea-App schließen, einige Minuten warten, erneut versuchen |
| *keine Antwort beim Verbindungsaufbau* | Unter dieser Adresse hat gar nichts geantwortet | Falsche IP in `devices.json`, Anlage aus, oder eine Firewall verwirft die Pakete — prüfen mit `ping <IP>` und `nc -zv <IP> 6444` |
| *es kam keine Verbindung zustande* | Unter Adresse und Port ist überhaupt nichts erreichbar | Falsche IP in `devices.json`, Anlage nicht im Netz, DNS-/Routing-Problem, oder Port 6444 zu |

Die ersten beiden Zeilen sind beides *Antworten*: die Anlage ist da und redet. Endet **jeder** Versuch so, sind Netzwerk und Erreichbarkeit in Ordnung, und die gespeicherten Zugangsdaten gehören schlicht nicht zu dieser Anlage — das Werkzeug sagt das und verweist auf den Token-Abruf.

Ein weiteres Muster sollte man kennen: **Eine Midea-Anlage hält nur eine einzige lokale Verbindung** und antwortet nach schnell aufeinanderfolgenden Versuchen eine Weile gar nicht mehr — auch die Midea-App auf dem Handy belegt genau diese Verbindung. Antwortet die Anlage auf die ersten Kandidaten und danach auf keinen einzigen mehr, wurden die späteren gar nicht mehr wirklich geprüft. Genau deshalb entzerrt das Werkzeug seine Versuche und weist auf dieses Muster hin, wenn es auftritt — aber nur, wenn die Antworten wirklich zuerst kamen und danach nichts mehr beantwortet wurde; sonst schweigt es lieber, statt zu raten. Vor einem Urteil also einige Minuten warten, mit geschlossener App.

## Bekannte Midea-App-/Firmware-Eigenheiten

Von einigen frühen PortaSplit-Geräten wird berichtet, dass sie sich **selbst ein-/ausschalten oder eigenständig den Modus wechseln** — Ursache ist ein Fehler in Midea-App/Cloud, nicht die Hardware ([connect.de](https://www.connect.de/news/midea-portasplit-probleme-stoerung-schaltet-sich-automatisch-ein-und-aus-app-fehler-loesung-3209868.html)). Mideas eigener Workaround ist, dem Gerät **am Router den Internetzugang zu sperren** — genau der Betrieb, für den dieses Projekt gemacht ist: Ist die Cloud gekappt, funktioniert die lokale LAN-Steuerung normal weiter.

Beobachtest du unerwartete Ein-/Aus- oder Moduswechsel, ist das höchstwahrscheinlich dieses geräteseitige Problem und nicht `midea_ieco_ensure.py`: Das Skript schaltet ausschließlich iECO (und, ohne `--only-if-on`, den Ein-/Aus-Zustand) und protokolliert jede Aktion in `ieco.log` — so kannst du genau nachvollziehen, was es getan hat und was nicht.

## FAQ

**Ändert das meine Zieltemperatur?**

Nein. Es stellt nur sicher, dass iECO beim bereits eingestellten Sollwert aktiv ist. Es schreibt nie eine Temperatur.

**iECO lässt sich überhaupt nicht einschalten — woran liegt das?**

Höchstwahrscheinlich am Betriebsmodus. iECO funktioniert nur in **Kühlen** und **Heizen**; in **Auto**, **Entfeuchten** und **Nur Lüften** verwirft das Gerät den Befehl ohne jeden Hinweis — das ist Geräteverhalten, keine Einschränkung dieses Werkzeugs. Seit Version 0.3.0 benennt das Skript das ausdrücklich, statt einen allgemeinen Fehlschlag zu melden. Siehe [Welche Modi iECO unterstützen](#welche-modi-ieco-unterstützen).

**iECO war später wieder aus — ist etwas kaputt?**

Nein, und genau dafür ist das Tool da. Gut belegt ist: iECO geht verloren, sobald die Anlage **außerhalb der App** aus- und wieder eingeschaltet wird — am Gerät selbst oder per IR-Fernbedienung. Es kommt nicht von allein zurück, und am Display weist nichts darauf hin. (Schaltet man *über die App* aus und wieder ein, bleibt es erhalten — deshalb stoßen nicht alle darauf.) Ob iECO *zusätzlich* nach rund acht Stunden ungestörten Betriebs von selbst endet, ist **unbestätigt** — siehe den Hinweis unter [Hintergrund: ECO vs. iECO](#hintergrund-eco-vs-ieco). Für das Tool spielt das ohnehin keine Rolle: Der empfohlene Cron-Job (`--only-if-on`, alle 20 Minuten) liest den tatsächlichen Zustand am Gerät und zieht iECO nur nach, wenn es wirklich aus ist.

**Brauche ich einen Raspberry Pi oder Server?**

Du brauchst irgendeinen Computer, der läuft, wenn die Automatisierung greifen soll — Raspberry Pi, NAS, Heimserver oder einen dauerhaft laufenden Mac — im selben LAN wie die Anlage. Ein iPhone allein kann kein Cron ausführen; für Siri rufst du das Skript per SSH auf einem solchen Host auf (siehe [Siri und iOS Kurzbefehle](#siri-und-ios-kurzbefehle)).

**Funktioniert es, wenn die Midea-Cloud ausfällt?**

Ja. Der Normalbetrieb läuft vollständig lokal über dein LAN. Die Cloud wird nur zum Abholen oder Auffrischen der Geräte-Tokens (`midea_refresh_tokens.py`) kontaktiert, nie für die tägliche iECO-Steuerung.

**Brauche ich mein Midea-Konto oder Passwort?**

Nein. Geräte-Tokens werden über das lokale Netzwerk mit dem eingebauten Hilfskonto von `midea-local` beschafft — Midea vergibt diese Tokens inzwischen nur noch über eine Cloud-API, und das ist nicht die MSmartHome-API; dein MSmartHome-Login könnte sie also ohnehin nicht abholen. Tokens sind an das Gerät gebunden, nicht an dein Konto, deshalb fragt der Installer nie nach einem Passwort und speichert keins. Die MSmartHome-App brauchst du nur einmalig, um die Anlage ins WLAN zu bringen.

**Brauche ich Home Assistant?**

Nein — ohne auszukommen ist gerade der Sinn (siehe **Zweck & Alternativen** oben). Wer bereits Home Assistant nutzt und iECO dort haben möchte, nimmt [`midea-ac-py`](https://github.com/mill1000/midea-ac-py): Es basiert auf derselben `msmart-ng`-Bibliothek und bietet iECO als Steuerung an. `midea_ac_lan` kann iECO trotz größeren Funktionsumfangs gar nicht setzen. In beiden Fällen braucht es zusätzlich eine Automatisierung, die iECO nach einem Aus/Ein wieder aktiviert — genau das erledigt dieses Tool von Haus aus.

## Hintergrund: ECO vs. iECO

<details>
<summary><b>Warum dieses Projekt existiert — die ECO/iECO-Abgrenzung, Energieeinsparung und warum die App nicht reicht</b></summary>

### ECO vs. iECO — zwei unterschiedliche, leicht verwechselte Modi

Midea-Klimaanlagen wie die PortaSplit besitzen **zwei getrennte Energiesparmodi**, die häufig verwechselt werden — auch in früheren Entwürfen dieser Dokumentation. Eine korrekte Unterscheidung ist wichtig:

| | **ECO** (Taste/Fernbedienung) | **iECO** (nur App/Cloud) |
|---|---|---|
| Aktivierung | Physische Taste am Gerät oder Fernbedienung | Ausschließlich über die MSmartHome- / Midea Smarthome-App |
| Zieltemperatur | **Wird automatisch fix auf 24 °C** gesetzt, Lüfter auf Auto | **Bleibt bei der vom Nutzer eingestellten Zieltemperatur** (z. B. 21 °C, 25 °C usw.) — nicht fix |
| Mechanismus | Einfacher fester Sollwert | Cloud-verbundener, adaptiver Algorithmus, der die Verdichterleistung feinfühlig um den vom Nutzer gewählten Sollwert herum regelt |
| Automatische Abschaltung | Kann nach Inaktivitätsphase am Sollwert automatisch abschalten | Geht verloren, sobald die Anlage außerhalb der App aus- und wieder eingeschaltet wird; ein zusätzlicher ~8-Stunden-Timeout wird oft behauptet, ist aber **unbestätigt** (siehe Hinweis unten) |
| Verfügbarkeit | Auch offline verfügbar, funktioniert mit der IR-Fernbedienung | Erfordert eine durchgehende WLAN-/Cloud-Verbindung, solange iECO aktiv ist |

Kurz gesagt: **iECO erzwingt keine 24 °C.** Es arbeitet bei jeder beliebigen, am Gerät eingestellten Temperatur — es lässt den Verdichter lediglich sanfter und effizienter um diesen Sollwert herum regeln, statt mit voller, uneingeschränkter Leistung zu laufen. Dieses Projekt behandelt gezielt **iECO**, nicht den einfacheren, tastenaktivierten ECO-Modus.

### Was iECO bewirkt

Midea bewirbt iECO damit, bis zu 60 % Energie im Vergleich zum Normalbetrieb einzusparen – bis zu acht Stunden Betrieb mit nur 1,2 kWh bei typischen Einstellungen ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)). Achtung: Diese „acht Stunden" sind eine **Verbrauchsangabe** – sie besagen nicht, dass iECO nach acht Stunden abschaltet; beides wird leicht verwechselt (siehe Hinweis weiter unten). Ein deutscher Zehn-Stunden-Praxistest mit der PortaSplit ergab rund 100 W niedrigeren Verbrauch im iECO-Modus gegenüber dem Auto-Modus bei gleichzeitig angenehmer Raumtemperatur von 24,5–25,7 °C ([4-Happy-Home auf YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)). Community-Berichte bestätigen zudem, dass iECO auch bei anderen Zieltemperaturen wie 21 °C erfolgreich läuft, mit entsprechend angepasstem – nicht fixem – Energieverbrauch.

Messungen im Rahmen dieses Projekts – zwei baugleiche Geräte an je einem eigenen Messstecker (Shelly Plus Plug S), zehn Tage im Juni 2026 bei unverändert 23 °C Sollwert – ergaben **2 bis 3,8 kWh pro Tag und Gerät** Mehrverbrauch ohne aktiven iECO-Modus, ohne erkennbaren Komfort- oder Kühlvorteil. Der sauberere der beiden Vergleiche (an allen Tagen gleiches Betriebsmuster) lag bei 3,8 kWh/Tag – also rund der Hälfte des Verbrauchs mit iECO; das zweite Gerät, bei dem an manchen Tagen der Alltag hineinfunkte, bei 2,0–3,2 kWh/Tag.

Was das ist und was nicht: eine Alltagsmessung, kein Labortest. Die Tage mit und ohne iECO sind verschiedene Kalendertage, Außentemperatur und Sonneneinstrahlung wurden nicht mitprotokolliert, der Zeitraum ist kurz. Für die Vergleichbarkeit spricht: Die **nächtliche Grundlast ist an iECO- und Nicht-iECO-Tagen praktisch identisch** (~104–120 Wh beim Büro-Gerät). Der Unterschied kommt also nicht schlicht von „heißeren Tagen", sondern entsteht in den aktiven Kühlstunden – dort verdoppelt bis verdreifacht sich der höchste Stundenwert ohne iECO.

### Das Problem: iECO verschwindet nach manuellem Eingriff

iECO lässt sich derzeit **ausschließlich über die MSmartHome-App (Midea Smarthome)** aktivieren; es gibt keine physische iECO-Taste an der Fernbedienung oder am Gerät (die dort vorhandene Taste steuert nur den einfacheren, fest auf 24 °C gesetzten ECO-Modus).

> **Zu den „acht Stunden":** Man findet vielfach die Aussage, iECO beende sich nach rund acht Stunden von selbst – auch in früheren Fassungen dieses Dokuments. Sie ist mit Vorsicht zu genießen. Mideas eigene Angabe „bis zu acht Stunden mit nur 1,2 kWh" ist eine *Verbrauchsangabe*, kein Timeout, und mehrere PortaSplit-Besitzer berichten, eine solche Abschaltung nie beobachtet zu haben; jede Interaktion (App öffnen, Fernbedienung, Zieltemperatur ändern) setzt einen solchen Timer offenbar ohnehin zurück, sodass er in der Praxis kaum auffiele. In einer kontrollierten Messung wurde das hier nicht isoliert. **Reproduzierbar ist der nächste Absatz** – und dafür existiert dieses Projekt.

Wird die Klimaanlage anschließend manuell ausgeschaltet und wieder eingeschaltet – direkt am Gerät oder mit der Fernbedienung – bleibt iECO deaktiviert. Das fällt leicht nicht auf, weil die Anlage ansonsten normal zu funktionieren scheint und weiterhin die zuletzt eingestellte Zieltemperatur hält. Anstatt nach jedem manuellen Neustart daran zu denken, die App zu öffnen und iECO erneut zu aktivieren, automatisiert dieses Projekt diese Aufgabe zuverlässig im Hintergrund.

### Warum nicht einfach die Midea-App nutzen?

Die App bietet keine bedingte Logik wie „iECO nur aktivieren, wenn die Anlage schon läuft", und sie stellt auch keine öffentliche, dokumentierte API für Drittanbieter-Automatisierungen wie Cron-Jobs oder Siri bereit. Die hier verwendeten Bibliotheken (`msmart-ng` und `midea-local`) kommunizieren mit dem Gerät direkt über das lokale Netzwerk und ermöglichen so die Steuerung von iECO ohne Cloud-Abhängigkeit im Regelbetrieb.

</details>

## Enthaltene Dateien

| Datei | Zweck |
|---|---|
| `install.sh` | Einmal-Installer (zugleich `--update`-Motor hinter `midea-ieco-update`): richtet venv, Abhängigkeiten, `devices.json`, Tokens, die Wrapper-Befehle und Cron-Job ein |
| `midea_ieco_ensure.py` | Prüft und setzt den Einschaltzustand und iECO für ein oder alle konfigurierten Geräte |
| `midea_refresh_tokens.py` | Holt frische Token-/Key-Paare (ohne Cloud-Passwort) und aktualisiert `devices.json` |
| `midea_ieco_ensure.sh` | Wrapper für SSH/Kurzbefehle: startet `midea_ieco_ensure.py` mit dem venv-Python und reicht alle Argumente weiter |
| `midea_i18n.py` | Gemeinsame Deutsch/Englisch-Sprachwahl für beide Python-Werkzeuge |
| `midea_conn.py` | Gibt die LAN-Verbindung eines Geräts frei. `msmart-ng` bietet dafür keine öffentliche API — der einzige Griff in dessen Interna ist hier gekapselt |
| `devices.example.json` | Vorlage für `devices.json` — kopieren, dann eigene Geräte eintragen |
| `devices.json` | Lokale Gerätekonfiguration (Name, IP, Port, ID, Token, Key). Lokal erzeugt, **git-ignoriert** |
| `refresh_state.json` | Wann ein vollständiger Token-Refresh zuletzt begonnen hat und zuletzt gelungen ist. Von jedem `--all`-Lauf geschrieben, der die Geräteschleife erreicht (ein Lauf, der vorher abbricht — keine Geräte, unlesbare Konfiguration — lässt sie unangetastet), bei jedem `--all`-Lauf gelesen und von `--only-if-due` ausgewertet. Nur Zeitstempel, keine Geheimnisse — trotzdem `chmod 600` wie der Nachbar. Lokal erzeugt, **git-ignoriert** |

## Erkenntnisse aus der Entwicklung

Diese Tabelle dokumentiert spezifische Beobachtungen aus der Entwicklung dieses Setups mit `msmart-ng` im Jahr 2026. Sie dient als Referenz, nicht als allgemeine Fehlerbehebungsanleitung – interne APIs können sich zwischen Versionen ändern. Im Zweifelsfall die tatsächlich installierte Version prüfen:

```bash
python3 -c "import inspect; from msmart.device.AC.device import AirConditioner as AC; print(inspect.signature(AC.__init__))"
```

| Symptom | In der Entwicklung beobachtete Ursache | Lösung |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | Die `midea-local`-API hat sich geändert | Installierte Signatur prüfen: `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` |
| `Device is not capable of property IECO` | Capabilities wurden auf dem für `apply()` genutzten Objekt nie abgefragt — `supports_ieco` wird ausschließlich durch `get_capabilities()` befüllt | `get_capabilities()` auf dem frischen, authentifizierten `AC`-Objekt aufrufen, bevor capability-gebundene Properties gesetzt werden und `apply()` läuft. Zum *Setzen* ist die Reihenfolge relativ zu `refresh()` egal; zum *Lesen* des aktuellen Zustands nicht — siehe nächste Zeile |
| iECO wird ohne Fehler gesetzt, das Gerät bleibt aber außerhalb von iECO | Der Betriebsmodus trägt kein iECO. `supports_ieco` sagt nur, dass das *Gerät* iECO grundsätzlich kann — es fasst die modusabhängige Capability von `msmart-ng` (`1,3,8 - Cool, 3,4,8 - Heat`) zu einem einzigen Bool zusammen, der zulässige Modus ist zur Laufzeit also nicht mehr auslesbar. `msmart-ng` sendet den Befehl trotzdem (kein Modus-Guard; `apply()` warnt bei eco/turbo/swing, aber nicht bei iECO), und die Firmware verwirft ihn stillschweigend | iECO nur in **Kühlen** oder **Heizen** setzen; das eigene Gerät mit `tools/probe_ieco_current_mode.py` nachmessen. Siehe [Welche Modi iECO unterstützen](#welche-modi-ieco-unterstützen) |
| `device.ieco` (oder `eco`) liest `False`, obwohl der Modus am Gerät aktiv ist | `refresh()` pollt nur die Properties aus `_supported_properties`, und diese Menge wird von `get_capabilities()` befüllt. Auf einem frischen Objekt, das nur authentifiziert und refresht hat, wird IECO nie gepollt, `device.ieco` bleibt also beim Default `False` | `get_capabilities()` **vor** `refresh()` aufrufen, wann immer der echte Zustand gelesen wird (Statusanzeige, Verifikation nach `apply()`). Beim Initial-Status macht `midea_ieco_ensure.py` genau das. Die Verifikationsrunde fragt **nicht** ein zweites Mal: sie nimmt die Fähigkeit, die das Gerät im selben Lauf bereits gemeldet hat, per `override_capabilities({"additional_capabilities": ["IECO"]}, merge=True)` in das frische Objekt mit — das schaltet denselben Property-Poll scharf, ohne weiteren Roundtrip |
| Capability-Abfrage läuft ab / `Failed to query capabilities` obwohl Token/Key korrekt sind | Anfangs fehlgedeutet als „das Gerät beantwortet `get_capabilities()` nur im eingeschalteten Zustand" — weder der `msmart-ng`-Code noch der finale Ablauf stützen das; eine durch einen vorherigen Fehlversuch defekte Verbindung erzeugt dasselbe Symptom (siehe nächste Zeile) | Mit komplett frischer Verbindung erneut versuchen: `midea_ieco_ensure.py` erstellt bei jedem Retry ein neues `AC`-Objekt und fragt die Capabilities erneut ab. Der ausgelieferte Ablauf fragt Capabilities vor dem Einschalten ab. Aus einer unbeantworteten Abfrage zieht das Werkzeug keinen Schluss mehr über das Gerät — siehe nächste Zeile |
| Im Log steht `Geraet meldet keine iECO-Faehigkeit` für eine Anlage, die iECO nachweislich kann | Ein verlorener Capability-Roundtrip ist von einer negativen Antwort nicht zu unterscheiden. `get_capabilities()` wirft bei Paketverlust nicht — es loggt `Failed to query capabilities` und kehrt zurück (`msmart/device/AC/device.py:602-611`) —, und `Capability.DEFAULT` enthält kein `IECO`; ein frisches Objekt startet deshalb immer bei `supports_ieco == False`. Das folgende `refresh()` baut die Verbindung transparent neu auf und authentifiziert erneut (`msmart/lan.py:589-599`), setzt `online` damit wieder auf `True` und verdeckt den gescheiterten Austausch: `msmart-ng` heilt die Verbindung genau einen Schritt *nach* dem Urteil | Das Werkzeug hält `device.online` **zwischen** `get_capabilities()` und `refresh()` fest und lastet die fehlende Fähigkeit dem Gerät nur an, wenn die Abfrage wirklich beantwortet wurde; sonst meldet es, dass die Abfrage keine verwertbare Antwort ergeben hat, und nennt die iECO-Fähigkeit unbekannt. Geht nur der optionale *additional*-Request verloren, läuft der Durchgang weiter, **sofern iECO bereits im ersten Block kam** — das ist der Regelfall und gilt bewusst nicht als Fehlschlag. Meldet eine Anlage iECO erst im zweiten Block, beendet derselbe Verlust den Lauf sehr wohl, dann aber mit der Meldung „keine verwertbare Antwort" statt mit einer Behauptung über das Gerät. Grenze: `online` zählt *rohe* Antworten — eine Antwort, die ankommt, aber nicht dekodierbar ist, wird weiterhin dem Gerät angelastet, siehe `tests/KNOWN_GAPS.md` |
| `[Errno 104] Connection reset by peer` nach mehreren Versuchen | Ein fehlgeschlagener Verbindungsversuch hinterließ das `AC`-Objekt mit einem defekten Socket-Zustand | Bei jedem Wiederholungsversuch ein **neues** `AC`-Objekt erstellen |
| Ein Token/Key-Paar wird gespeichert und funktioniert sofort nicht, obwohl die Auffrischung Erfolg meldete | `msmart-ng` reicht Netzfehler aus `refresh()` nicht nach oben: `Device._send_command` fängt `ProtocolError`/`TimeoutError`, protokolliert sie und liefert eine leere Antwortliste, `refresh()` kehrt also normal zurück. Ein Gerät, das den Handshake bestand und dann verstummte, sah damit aus wie eine bestandene Verifikation | „Keine Ausnahme" ist kein Beweis. `device.online` ist das beobachtbare Gegenstück — `msmart-ng` setzt es in `_send_commands_get_responses` auf `len(responses) > 0`, dem Trichter, durch den jedes Kommando läuft. `midea_refresh_tokens.py` lehnt einen solchen Kandidaten jetzt ab, statt ihn zu speichern, und `midea_ieco_ensure.py` sagt das, statt es iECO anzulasten |
| Spätere Token-Kandidaten melden „Gerät antwortet nicht", oder die Verifikation nach `apply()` läuft in ein Zeitlimit — obwohl mit dem Gerät alles in Ordnung ist | Verbindungen wurden nie freigegeben. `msmart-ng`s `AirConditioner` hat weder `close` noch `disconnect` noch `stop`; ein Helfer, der genau diese drei Namen suchte, tat also gar nichts. Der Socket blieb offen, und der nächste Schritt baute eine **zweite gleichzeitige** Verbindung zu einer Anlage auf, die nur eine verträgt — das Werkzeug erzeugte die Stille, die es anschließend meldete | Über die private, **synchrone** `device._lan._disconnect()` schließen. Gekapselt in `midea_conn.py`; `tests/test_conn.py` weist gegen einen Loopback-Server nach, dass der Socket wirklich zugeht, und der CI-Job `install-smoke` pinnt den privaten Pfad, damit eine künftige `msmart-ng`-Fassung den Abbau nicht unbemerkt wieder wirkungslos macht |
| Token/Key funktionieren plötzlich nicht mehr | Meist das vorige Socket-Problem, seltener eine echte Token-Invalidierung | `midea_refresh_tokens.py --name <Gerät>` ausführen |
| `msmart-ng discover --auto` liefert gelegentlich Schlüssel, die kurz danach nicht mehr funktionieren | Sowohl `msmart-ng` als auch `midea-local` melden sich mit einem eingebauten Hilfskonto an, und Tokens sind gerätegebunden (nicht kontogebunden) — es geht also nicht um *welches* Konto. Die kurzlebigen Ausfälle waren höchstwahrscheinlich das Frisch-Socket-Problem oben, kein echtes Ablaufen | `midea_refresh_tokens.py` (über `midea-local`) nutzen: es **verifiziert jeden Token gegen das Gerät** vor dem Speichern und behält bei Fehlschlag den zuletzt gültigen Wert |
| `getToken` scheitert / keine `tokenlist`, wenn ein `cloud_name` von MSmartHome oder Meiju genutzt wird | Midea hat die Token-API dieser Clouds abgeschaltet (`errorCode 3004 "value is illegal"`); nur NetHome Plus vergibt noch Tokens (Juli 2026 verifiziert) | `midealocal` sein Default-Hilfskonto (NetHome Plus) nutzen lassen — keinen `cloud_name`/keine Zugangsdaten übergeben. `midea_refresh_tokens.py` tut das und schirmt die CLI zusätzlich gegen jede nutzer-globale Config ab |
| Verwechslung zwischen „ECO" und „iECO" in Logs/UI | Mideas eigene Doku und App verwenden für zwei unterschiedliche Mechanismen ähnliche Bezeichnungen | Merke: normales ECO = fix 24 °C via Taste/Fernbedienung; iECO = eigener Sollwert via App/Cloud-Algorithmus |

## Sicherheitshinweise

- **Es wird nirgends ein Midea-Cloud-Passwort gespeichert.** Seit 0.2.0 holen die Skripte die Geräte-Tokens ohne jegliche Cloud-Zugangsdaten — es gibt also keine Passwortdatei zu schützen. (Wer von 0.1.x aktualisiert, hat evtl. noch eine `credentials.json` auf der Platte — sie ist nun ungenutzt; der Installer weist darauf hin, und du kannst sie gefahrlos `rm`. Sie bleibt **git-ignoriert**.)
- `devices.json` enthält sensible Werte (Token/Key je Gerät): dauerhaft bei `chmod 600` belassen. Sie ist **git-ignoriert**, deine echten Werte werden also nie versioniert — nur die `devices.example.json`-Vorlage. (Ein früher Commit enthielt zwar eine `devices.json`, jedoch ausschließlich funktionslose Dummy-Platzhalter; echte Zugangsdaten liegen an keiner Stelle der Git-History.)
- `midea-local.json` ist eine Wegwerf-0600-Datei, die `midea_refresh_tokens.py` **ausschließlich in einem privaten, pro Aufruf frischen Temp-Verzeichnis** anlegt — ein leeres `{}`, das die `midealocal`-CLI auf eine deterministische, zugangsdatenfreie Abfrage festnagelt (unabhängig von jeder nutzer-globalen Config). Sie landet nie im Projektbaum und wird sofort entfernt; der Name ist als Sicherheitsnetz **git-ignoriert**.
- Für Siri über SSH SSH-Schlüssel-Authentifizierung verwenden und SSH **nicht** per Port-Weiterleitung ins Internet freigeben. Für Remote-Zugriff stattdessen ein VPN (z. B. Tailscale) nutzen.

## In den Medien

Fachmedien, die über dieses Projekt berichtet oder seine Messungen verwendet haben:

| Datum | Medium | Thema |
|---|---|---|
| 25.07.2026 | **techboys.de** — Gary Madeo | [Mideas Token-API-Abschaltung](https://www.techboys.de/portasplit-home-assistant) — ein späteres Update ergänzte das Testprotokoll dieses Projekts dazu, welche Cloud noch Geräte-Token ausgibt |
| 26.07.2026 | **Caschys Blog** — Carsten Knobloch | [Mit iECO Strom sparen bei der PortaSplit](https://stadt-bremerhaven.de/midea-portasplit-wie-ihr-mit-ieco-strom-spart/) — erklärt die ECO/iECO-Unterscheidung und verlinkt dieses Projekt |
| 26.07.2026 | **techboys.de** — Gary Madeo | [Der iECO-Modus in der Praxis](https://www.techboys.de/portasplit-ieco) — aufgebaut auf den Verbrauchsmessungen dieses Projekts (Shelly-Logs, Tagestabellen, die Spanne 2–3,8 kWh) |
| 27.07.2026 | **SmarthomeAssistent** — Dennis Jakobi | [Kostenloses Tool sichert PortaSplit-Zugriff und soll Strom sparen](https://www.smarthomeassistent.de/midea-ieco-kostenloses-tool-sichert-portasplit-zugriff-und-soll-strom-sparen/) — Porträt dieses Projekts im Anschluss an den eigenen Bericht der Redaktion zur Token-API-Abschaltung |

Zur Transparenz: Messdaten, API-Protokoll und Screenshots habe ich `techboys.de` zur Verfügung gestellt und werde dort als Quelle genannt. Der Beitrag von SmarthomeAssistent geht auf eine Presse-E-Mail von mir mit denselben API-Befunden zurück; der Artikel weist offen darauf hin, dass der Redaktion keine unabhängige Prüfung des Tools vorlag. Die Artikel selbst sind redaktionell unabhängig entstanden und wurden von den Autoren eigenständig nachgerechnet — es floss kein Geld, in keine Richtung, und ich hatte keinen Einfluss auf die Formulierungen. Die zugrunde liegenden Rohdaten sind unter [Hintergrund: ECO vs. iECO](#hintergrund-eco-vs-ieco) beschrieben; die Grenzen der Messung stehen dort genauso offen wie in den Artikeln.

## Lizenz und Weitergabe

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE) — die Skripte dürfen frei genutzt, geteilt und angepasst werden, solange der Copyright-Hinweis erhalten bleibt. Auch die verwendeten Bibliotheken [`msmart-ng`](https://github.com/mill1000/midea-msmart) und [`midea-local`](https://github.com/rokam/midea-local) stehen unter MIT. Dieses Projekt ist nicht mit Midea verbunden und ersetzt nicht den offiziellen Midea-Support.

---

> 🇬🇧 **English documentation:** [README.md](README.md)
