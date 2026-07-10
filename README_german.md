# midea-ieco

> 🇬🇧 **English:** The full English documentation is here: [README.md](README.md)

Kleine, zuverlässige Kommandozeilen-Werkzeuge zur lokalen Steuerung des **iECO-Modus** (und des allgemeinen Ein-/Ausschaltzustands) von Midea-Klimaanlagen, einschließlich der Midea PortaSplit und kompatibler Modelle von Comfee, Toshiba, Carrier, Klimaire und anderen. Die Werkzeuge vermeiden im Normalbetrieb die Abhängigkeit von einer instabilen Cloud-Verbindung.

`msmart-ng` kann iECO direkt über das lokale Netzwerk steuern. Midea-Cloud-Zugangsdaten werden nur einmalig zur Beschaffung gültiger Gerätezugangsdaten benötigt sowie erneut, wenn diese aufgefrischt werden müssen.

## Warum dieses Projekt existiert

### ECO vs. iECO — zwei unterschiedliche, leicht verwechselte Modi

Midea-Klimaanlagen wie die PortaSplit besitzen **zwei getrennte Energiesparmodi**, die häufig verwechselt werden — auch in früheren Entwürfen dieser Dokumentation. Eine korrekte Unterscheidung ist wichtig:

| | **ECO** (Taste/Fernbedienung) | **iECO** (nur App/Cloud) |
|---|---|---|
| Aktivierung | Physische Taste am Gerät oder Fernbedienung | Ausschließlich über die MSmartHome- / Midea Smarthome-App |
| Zieltemperatur | **Wird automatisch fix auf 24 °C** gesetzt, Lüfter auf Auto | **Bleibt bei der vom Nutzer eingestellten Zieltemperatur** (z. B. 21 °C, 25 °C usw.) — nicht fix |
| Mechanismus | Einfacher fester Sollwert | Cloud-verbundener, adaptiver Algorithmus, der die Verdichterleistung feinfühlig um den vom Nutzer gewählten Sollwert herum regelt |
| Automatische Abschaltung | Kann nach Inaktivitätsphase am Sollwert automatisch abschalten | Verlässt den Modus automatisch nach acht Stunden und kehrt zum normalen Auto-Modus zurück |
| Verfügbarkeit | Auch offline verfügbar, funktioniert mit der IR-Fernbedienung | Erfordert eine bestehende WLAN-/Cloud-Verbindung während der Aktivierung |

Kurz gesagt: **iECO erzwingt keine 24 °C.** Es arbeitet bei jeder beliebigen, am Gerät eingestellten Temperatur — es lässt den Verdichter lediglich sanfter und effizienter um diesen Sollwert herum regeln, statt mit voller, uneingeschränkter Leistung zu laufen. Dieses Projekt behandelt gezielt **iECO**, nicht den einfacheren, tastenaktivierten ECO-Modus.

### Was iECO bewirkt

Midea bewirbt iECO damit, bis zu 60 % Energie im Vergleich zum Normalbetrieb einzusparen – bis zu acht Stunden Betrieb mit nur 1,2 kWh bei typischen Einstellungen ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)). Ein deutscher Zehn-Stunden-Praxistest mit der PortaSplit ergab rund 100 W niedrigeren Verbrauch im iECO-Modus gegenüber dem Auto-Modus bei gleichzeitig angenehmer Raumtemperatur von 24,5–25,7 °C ([4-Happy-Home auf YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)). Community-Berichte bestätigen zudem, dass iECO auch bei anderen Zieltemperaturen wie 21 °C erfolgreich läuft, mit entsprechend angepasstem – nicht fixem – Energieverbrauch.

Messungen im Rahmen dieses Projekts ergaben rund 4 kWh **pro Tag** Mehrverbrauch im Dauerbetrieb bei einem gegebenen Sollwert ohne aktiven iECO-Modus, ohne erkennbaren Komfort- oder Kühlvorteil durch den Betrieb ohne iECO.

### Das Problem: iECO verschwindet nach manuellem Eingriff

iECO verlässt den Modus automatisch nach acht Stunden und kehrt zum normalen Auto-Modus zurück. Noch wichtiger: iECO lässt sich derzeit **ausschließlich über die MSmartHome-App (Midea Smarthome)** aktivieren; es gibt keine physische iECO-Taste an der Fernbedienung oder am Gerät (die dort vorhandene Taste steuert nur den einfacheren, fest auf 24 °C gesetzten ECO-Modus).

Wird die Klimaanlage anschließend manuell ausgeschaltet und wieder eingeschaltet – direkt am Gerät oder mit der Fernbedienung – bleibt iECO deaktiviert. Das fällt leicht nicht auf, weil die Anlage ansonsten normal zu funktionieren scheint und weiterhin die zuletzt eingestellte Zieltemperatur hält. Anstatt nach jedem manuellen Neustart daran zu denken, die App zu öffnen und iECO erneut zu aktivieren, automatisiert dieses Projekt diese Aufgabe zuverlässig im Hintergrund.

### Warum nicht einfach die Midea-App nutzen?

Die App bietet keine bedingte Logik wie „iECO nur aktivieren, wenn die Anlage schon läuft", und sie stellt auch keine öffentliche, dokumentierte API für Drittanbieter-Automatisierungen wie Cron-Jobs oder Siri bereit. Die hier verwendeten Bibliotheken (`msmart-ng` und `midea-local`) kommunizieren mit dem Gerät direkt über das lokale Netzwerk und ermöglichen so die Steuerung von iECO ohne Cloud-Abhängigkeit im Regelbetrieb.

## Enthaltene Dateien

| Datei | Zweck |
|---|---|
| `install.sh` | Einmal-Installer: richtet venv, Abhängigkeiten, devices.json, Tokens und Cron-Job ein |
| `midea_ieco_ensure.py` | Prüft und setzt den Einschaltzustand und iECO für ein oder alle konfigurierten Geräte |
| `midea_refresh_tokens.py` | Holt frische Token-/Key-Paare von der Midea Cloud und aktualisiert `devices.json` |
| `devices.json` | Zentrale Konfiguration: Name, IP-Adresse, Port, Geräte-ID, Token und Key je Gerät |

## Voraussetzungen

- Python 3.10 oder neuer
- Ein Midea-Cloud-Konto (**MSmartHome** oder **Midea Smarthome**), in dem die Geräte bereits eingerichtet und funktionsfähig sind
- Der steuernde Computer muss sich im selben lokalen Netzwerk wie die Klimaanlagen befinden (Port 6444/TCP muss erreichbar sein – keine Client-Isolation oder VLAN-Trennung)

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

Falls `/opt/local` für deinen Benutzer nicht beschreibbar ist, nutzt der Installer einmalig `sudo`, um das Verzeichnis anzulegen und dir anschließend den Besitz zu übertragen — danach sind keine weiteren `sudo`-Aufrufe mehr nötig.

> **Bevor du beginnst:** Halte deinen **MSmartHome- Benutzernamen und dein Passwort** bereit – dieselben Zugangsdaten wie in der offiziellen Midea-App. Sie werden einmalig während der Installation zum Abholen der Geräte-Token abgefragt.

> **Empfohlen, aber nicht zwingend:** Vergib im Router feste IP-Adressen (DHCP-Reservierung nach MAC-Adresse) für deine Klimaanlagen, bevor du den Installer startest. Das verhindert, dass sich die IP nach einem Geräte- oder Router-Neustart ändert. Überspringst du diesen Schritt, oder ändert sich die IP später trotzdem, kannst du sie jederzeit direkt in `devices.json` nachtragen — ohne Neuinstallation.

Der Installer erledigt automatisch:

1. Erkennung von Betriebssystem und Paketmanager, Installation fehlender Voraussetzungen (`python3`, `python3-venv`, `git`/`curl`)
2. Anlegen einer virtuellen Python-Umgebung und Installation von `msmart-ng` und `midea-local`
3. Abfrage deiner Midea-Cloud-Zugangsdaten und Ausführung der Geräteerkennung
4. Interaktive Eingabe von Gerätenamen, IPs und IDs zum Aufbau von `devices.json`
5. Abruf der Token-/Key-Paare und sichere Speicherung (`chmod 600`)
6. Anlegen eines `midea-ieco`-Wrapper-Befehls sowie optionaler Testlauf und optionale Cron-Job-Einrichtung

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
pip install msmart-ng midea-local

# 3. Geräte ermitteln und IDs sowie IP-Adressen notieren
python3 -m midealocal.cli discover --username "DEINE_SMARTHOME_EMAIL" --password "DEIN_SMARTHOME_PASSWORT"

# 4. Im Router feste IP-Adressen für die Klimaanlagen vergeben
#    (DHCP-Reservierung nach MAC-Adresse), damit die Konfiguration dauerhaft stabil bleibt.
#    Nicht zwingend — die IP kann später auch direkt in devices.json geändert werden.

# 5. devices.json anhand der Vorlage erstellen (siehe „Einmalige Einrichtung" unten)

# 6. Zugangsdaten in midea_refresh_tokens.py hinterlegen, dann Zugriff einschränken:
#    DEFAULT_USERNAME und DEFAULT_PASSWORD am Anfang der Datei anpassen.
chmod 600 midea_refresh_tokens.py

# 7. Token-/Key-Paare für alle Geräte abrufen
python3 midea_refresh_tokens.py --all

# 8. Test: iECO für ein Gerät aktivieren (Gerät muss im Netz erreichbar sein)
python3 midea_ieco_ensure.py Wohnzimmer

# Bash-Wrapper ausführbar machen (wird von Cron und Shortcuts verwendet):
chmod +x midea_ieco_ensure.sh
```

## Einmalige Einrichtung (Details zum manuellen Weg)

### 1. Geräte-IDs und IP-Adressen ermitteln

```bash
python3 -m midealocal.cli discover --username "DEIN_KONTO" --password "DEIN_PASSWORT"
```

Notiere Geräte-ID (`id`) und IP-Adresse für jedes Gerät.

### 2. `devices.json` anlegen

```json
{
  "devices": [
    {
      "name": "Wohnzimmer",
      "ip": "192.168.0.186",
      "port": 6444,
      "id": 153931629346858,
      "token": "",
      "key": ""
    },
    {
      "name": "Schlafzimmer",
      "ip": "192.168.0.185",
      "port": 6444,
      "id": 152832117825892,
      "token": "",
      "key": ""
    }
  ]
}
```

Token und Key können anfangs leer bleiben; `midea_refresh_tokens.py` holt sie im nächsten Schritt.

### 3. Zugangsdaten in `midea_refresh_tokens.py` hinterlegen

Am Anfang der Datei eintragen:

```python
DEFAULT_USERNAME = "dein@konto.beispiel"
DEFAULT_PASSWORD = "deinPasswort"
```

Da diese Datei dein Cloud-Passwort im Klartext enthält, anschließend den Zugriff einschränken:

```bash
chmod 600 midea_refresh_tokens.py
```

### 4. Token-/Key-Paare abrufen

```bash
python3 midea_refresh_tokens.py --all
```

Das Skript führt `python3 -m midealocal.cli discover --debug` aus, extrahiert Token und Key aus dessen Ausgabe per regulärem Ausdruck und schreibt sie zurück in `devices.json`. Es setzt dabei auch automatisch `chmod 600` auf die Konfigurationsdatei.

> **Warum `midea-local` statt `msmart-ng discover`?** `midea-local` meldet sich mit deinem eigenen Midea-Konto an und erhält daher gerätespezifische Zugangsdaten, die mit diesem Konto verknüpft sind. `msmart-ng discover --auto` kann ein internes Hilfskonto verwenden und liefert ggf. Zugangsdaten, die sich bei jedem Aufruf ändern oder schnell ablaufen – damit ungeeignet für den unbeaufsichtigten Dauerbetrieb.

Ein neues Gerät lässt sich auch direkt über Name und IP-Adresse hinzufügen:

```bash
python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
```

## Tägliche Nutzung

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

Falls ein Gerät `Connection reset`, einen Timeout oder ein Zugangsdatenproblem meldet:

```bash
python3 midea_refresh_tokens.py --name Wohnzimmer
python3 midea_refresh_tokens.py --all
```

In der Praxis bleiben Zugangsdaten oft lange gültig. Auffrischen ist sinnvoll, wenn sich die App-Session grundlegend ändert (z. B. nach einer Passwortänderung beim Midea-Konto) oder wenn ein Gerät neu mit dem Netzwerk verbunden wurde.

## Cron-Automatisierung

Falls du nicht die automatische Cron-Einrichtung von `install.sh` genutzt hast, Crontab bearbeiten mit `crontab -e`:

```cron
# Alle 20 Minuten: iECO reaktivieren, ohne Geräte einzuschalten
*/20 * * * * cd /opt/local/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Jeden Sonntag um 03:00 Uhr: Zugangsdaten vorsorglich auffrischen
0 3 * * 0 cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Log-Rotation nicht vergessen, z. B. mit `logrotate` oder einfach:

```cron
0 0 1 * * truncate -s 0 /opt/local/midea-ieco/ieco.log
```

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
5. Per Siri aufrufen, z. B.: *„Hey Siri, starte Eco-Modus im Wohnzimmer."*

> **Tipp für nicht-interaktive SSH-Sitzungen:** Verwende `venv/bin/python3` direkt statt `source venv/bin/activate && python3`. Das ist zuverlässiger, weil nicht-interaktive Shells `source` unterschiedlich behandeln können.

Für alle Geräte `all` anstelle des Gerätenamens verwenden. `--only-if-on` hinzufügen, wenn Siri keine absichtlich ausgeschaltete Anlage einschalten soll.

### Alternative: Homebridge und HomeKit

Wer reguläre Schalter, Statusanzeige, Szenen und Automatisierungen in Apple Home bevorzugt, kann Homebridge mit `homebridge-cmd4` nutzen. Damit lassen sich beliebige Shell-Befehle auf Ein-/Aus-/Statusoperationen abbilden, z. B. `midea_ieco_ensure.py Wohnzimmer` als „Ein"-Aktion. Das ist aufwändiger als die SSH-Kurzbefehle-Lösung, bietet aber vollständige HomeKit-Integration.

## Netzwerk-Fehlerbehebung

Erscheinen bei jeder Anfrage Meldungen wie `No response from host`, liegen die häufigsten Ursachen hier:

- **Client-Isolation / AP-Isolation** im Router für das WLAN, mit dem die Klimaanlage verbunden ist, ist aktiviert — für das betreffende Netzwerksegment deaktivieren
- **VLAN-Trennung** zwischen IoT-Geräten und Computern — sicherstellen, dass Server und Klimaanlagen im selben VLAN sind, oder eine Firewall-Regel für TCP-Port 6444 anlegen
- **IP-Adresse hat sich geändert** — immer feste IP-Adressen (DHCP-Reservierung nach MAC-Adresse) im Router vergeben, oder `devices.json` manuell aktualisieren, falls sie sich geändert hat
- **Gerät befindet sich im WLAN-Energiesparmodus / Schlafmodus** — Erreichbarkeit prüfen mit `ping 192.168.x.x` und `nc -zv 192.168.x.x 6444`
- **Firewall auf dem Server** blockiert ausgehende Verbindungen zu Port 6444 — prüfen mit `iptables -L` oder `ufw status`

## Erkenntnisse aus der Entwicklung

Diese Tabelle dokumentiert spezifische Beobachtungen aus der Entwicklung dieses Setups mit `msmart-ng` im Jahr 2026. Sie dient als Referenz, nicht als allgemeine Fehlerbehebungsanleitung – interne APIs können sich zwischen Versionen ändern. Im Zweifelsfall die tatsächlich installierte Version prüfen:

```bash
python3 -c "import inspect; from msmart.device.AC.device import AirConditioner as AC; print(inspect.signature(AC.__init__))"
```

| Symptom | In der Entwicklung beobachtete Ursache | Lösung |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | Die `midea-local`-API hat sich geändert | Installierte Signatur prüfen: `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` |
| `Device is not capable of property IECO` | Capabilities wurden nicht abgefragt oder mit einem beschädigten Objekt abgefragt | `get_capabilities()` vor `refresh()`/`apply()` auf einem neuen `AC`-Objekt aufrufen |
| Capability-Abfrage läuft ab / `Failed to query capabilities` obwohl Zugangsdaten korrekt sind | Das Gerät beantwortet `get_capabilities()` nur im eingeschalteten Zustand | Erst einschalten (`power_state = True` plus `apply()`), dann Capabilities abfragen; `midea_ieco_ensure.py` hält diese Reihenfolge ein |
| `[Errno 104] Connection reset by peer` nach mehreren Versuchen | Ein fehlgeschlagener Verbindungsversuch hinterließ das `AC`-Objekt mit einem defekten Socket-Zustand | Bei jedem Wiederholungsversuch ein **neues** `AC`-Objekt erstellen |
| Token/Key funktionieren plötzlich nicht mehr | Meist das vorige Socket-Problem, seltener echte Zugangsdaten-Invalidierung | `midea_refresh_tokens.py --name <Gerät>` ausführen |
| `msmart-ng discover` liefert Zugangsdaten, die kurz danach nicht mehr funktionieren | `--auto` verwendet ein internes Hilfskonto mit temporären Schlüsseln | `midea_refresh_tokens.py` über `midea-local` verwenden, um dauerhafte Zugangsdaten zu erhalten |
| Verwechslung zwischen „ECO" und „iECO" in Logs/UI | Mideas eigene Doku und App verwenden für zwei unterschiedliche Mechanismen ähnliche Bezeichnungen | Merke: normales ECO = fix 24 °C via Taste/Fernbedienung; iECO = eigener Sollwert via App/Cloud-Algorithmus |

## Sicherheitshinweise

- `devices.json` enthält sensible Token-/Key-Werte: `chmod 600 devices.json` ausführen.
- `midea_refresh_tokens.py` enthält dein Cloud-Passwort im Klartext: `chmod 600 midea_refresh_tokens.py` ausführen.
- Für Siri über SSH SSH-Schlüssel-Authentifizierung verwenden und SSH **nicht** per Port-Weiterleitung ins Internet freigeben. Für Remote-Zugriff stattdessen ein VPN (z. B. Tailscale) nutzen.

## Lizenz und Weitergabe

Diese Skripte dürfen frei geteilt und angepasst werden. Sie verwenden die Open-Source-Bibliotheken `msmart-ng` und `midea-local` und ersetzen nicht den offiziellen Midea-Support.

---

> 🇬🇧 **English documentation:** [README.md](README.md)
