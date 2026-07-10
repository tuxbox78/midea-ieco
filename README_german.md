# midea-ieco

Kleine, zuverlässige Kommandozeilen-Tools zur lokalen Steuerung des
**iECO-Modus** (und allgemein Power/Status) von Midea-Klimaanlagen
(u.a. Midea PortaSplit, baugleiche Geräte von Comfee, Toshiba, Carrier,
Klimaire etc.), ohne dabei auf instabile Cloud-Verbindungen im
Dauerbetrieb angewiesen zu sein.

Hintergrund: Der iECO-Modus lässt sich in vielen gängigen Midea-Libraries
(z.B. `midea-local`) nicht steuern, weil dort kein passendes Attribut in
`DeviceAttributes` existiert. `msmart-ng` unterstützt iECO hingegen direkt
lokal über LAN — man braucht nur einmalig gültige Token/Key-Werte aus der
Midea-Cloud, die man über `midea-local` bequem beziehen kann.

## Hintergrund: Warum dieses Projekt überhaupt nötig ist

### Was iECO technisch bewirkt

Midea bewirbt iECO offiziell mit einer Ersparnis von bis zu 60% gegenüber
Standardbetrieb und einer Laufzeit von bis zu 8 Stunden mit nur 1,2 kWh
Verbrauch ([Midea Corporate](https://www.midea.com/th-en/news/energy-saving-air-conditioner)).
Ein unabhängiger, deutschsprachiger 10-Stunden-Praxistest mit exakt dem
PortaSplit-Modell misst konkret einen Unterschied von rund 100 Watt pro
Stunde zwischen iECO- und Auto-Modus — bei stabiler, komfortabler
Raumtemperatur zwischen 24,5 und 25,7 °C
([4-Happy-Home, YouTube](https://www.youtube.com/watch?v=ia4gUxGh5ms)).
Technisch fixiert iECO die Zieltemperatur auf 24 °C und die Lüfterstufe auf
automatisch; das Gerät regelt den Kompressor darüber deutlich sanfter als
im freien Auto/Cool-Betrieb.

Eigene Verbrauchsmessungen im Rahmen dieses Projekts zeigen einen
Mehrverbrauch von rund 4 kWh **pro Tag** im Dauerbetrieb, wenn iECO nicht
aktiv ist — ohne erkennbaren Komfort- oder Kühlvorteil gegenüber aktivem
iECO. Bei aktuellen Strompreisen ist das ein spürbarer Betrag, der sich
über einen Sommer schnell auf zweistellige Euro-Beträge summiert.

### Das eigentliche Problem: iECO „verschwindet" bei manueller Bedienung

Der oben verlinkte Test zeigt zusätzlich einen wichtigen Nebeneffekt:
iECO verlässt sich selbst automatisch nach 8 Stunden Laufzeit und wechselt
in den regulären Auto-Modus zurück — mit entsprechend höherem Verbrauch,
ohne dass der Nutzer aktiv etwas geändert hat. Kommt hinzu: iECO lässt
sich (Stand der Recherche zu diesem Projekt) **ausschließlich über die
MSmartHome/美的美居-App** aktivieren — es gibt keinen physischen Knopf auf
der Fernbedienung dafür. Schaltet man das Gerät danach händisch am Gerät
selbst oder per Fernbedienung aus und wieder ein (was die meisten
Nutzer im Alltag ganz selbstverständlich tun), bleibt iECO inaktiv,
ohne dass man das ohne Nachschauen in der App überhaupt merkt.

Das Ergebnis: Wer iECO einmal in der App aktiviert und die Anlage danach
wie gewohnt manuell bedient, bekommt am Monatsende eine unnötig hohe
Stromrechnung — ohne es zu merken, weil auf den ersten Blick „ja alles
wie immer" aussieht. Genau dieses Muster hat den Anstoß für dieses
Projekt gegeben: Statt sich zu merken, nach jedem manuellen Ein-/Ausschalten
wieder in die App zu wechseln und iECO erneut zu aktivieren, übernimmt das
hier vorgestellte Setup das automatisiert und zuverlässig im Hintergrund.

### Warum nicht einfach die App/Automationen von Midea nutzen?

Die App bietet keine Bedingungslogik im Sinne von „aktiviere iECO nur,
wenn das Gerät bereits läuft" und auch keine öffentliche, dokumentierte
API für Drittanbieter-Automatisierung (Home Assistant, Cron, Siri). Die
in diesem Projekt genutzten Bibliotheken (`msmart-ng`, `midea-local`)
sprechen das Gerät stattdessen direkt lokal im eigenen Netzwerk an —
schnell, ohne Cloud-Abhängigkeit im Dauerbetrieb, und mit voller
Kontrolle über die Bedingungen, unter denen iECO gesetzt wird.

## Enthaltene Skripte

| Datei                     | Zweck                                                                                    |
|---------------------------|------------------------------------------------------------------------------------------|
| `midea_ieco_ensure.py`    | Prüft/setzt Power-Status und iECO auf einem oder allen Geräten                           |
| `midea_refresh_tokens.py` | Holt frische Token/Key-Paare aus der Midea-Cloud und aktualisiert `devices.json`         |
| `devices.json`            | Zentrale Konfiguration: Name, IP, Port, Geräte-ID, Token, Key pro Gerät                  |

## Voraussetzungen

- Python 3.10 oder neuer
- Ein Midea-Cloud-Account (App „MSmartHome" oder „美的美居"), über den die
  Geräte bereits eingerichtet sind
- Netzwerkzugriff vom steuernden Rechner zu den Klimaanlagen (gleiches LAN,
  Port 6444/TCP)

### Python-Pakete

```bash
python3 -m venv venv
source venv/bin/activate
pip install msmart-ng midea-local
```

- **`msmart-ng`** — übernimmt die eigentliche lokale Gerätesteuerung
  (inkl. iECO-Attribut).
- **`midea-local`** — wird ausschließlich für den Cloud-Login benutzt, um
  Token/Key-Paare zu beziehen (`midea_refresh_tokens.py` ruft dessen
  `discover --debug`-Befehl als Subprozess auf).

> **Hinweis zu `msmart-ng discover`:** Der Befehl `msmart-ng discover`
> liefert bei Nutzung mit `--auto` oder ohne eigene Account-Angaben nur
> temporär gültige Session-Keys, die sich bei jedem Aufruf ändern können.
> Verwende deshalb **immer** `midea_refresh_tokens.py` (basiert auf
> `midea-local`), um dauerhaft stabile Token/Key-Werte zu erhalten und
> in `devices.json` zu speichern.

## Einmaliges Setup

### 1. Geräte-IDs und IP-Adressen ermitteln

```bash
python3 -m midealocal.cli discover --username "DEIN_ACCOUNT" --password "DEIN_PASSWORT"
```

Notiere dir für jedes Gerät die Geräte-ID (`id`) und die IP-Adresse.

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

Token/Key können leer bleiben — die holt `midea_refresh_tokens.py` im
nächsten Schritt automatisch.

### 3. Zugangsdaten in `midea_refresh_tokens.py` hinterlegen

Am Kopf der Datei:

```python
DEFAULT_USERNAME = "dein@account.de"
DEFAULT_PASSWORD = "deinPasswort"
```

Danach Dateirechte einschränken, da dort dein Cloud-Passwort im Klartext steht:

```bash
chmod 600 midea_refresh_tokens.py
```

### 4. Token/Key erstmalig beziehen

```bash
python3 midea_refresh_tokens.py --all
```

Das Skript ruft intern den bewährten Befehl
`python3 -m midealocal.cli discover --debug` auf, extrahiert Token und
Key aus dessen Ausgabe per Regex und schreibt sie direkt in `devices.json`
zurück (Datei wird danach automatisch mit `chmod 600` abgesichert).

> **Warum `midea-local` statt `msmart-ng discover`?** `midea-local`
> authentifiziert sich mit deinem eigenen Midea-Account und liefert
> dadurch dauerhaft gültige, zu deinem Account gebundene Token/Key-Paare.
> `msmart-ng discover --auto` hingegen nutzt einen internen Hilfs-Account
> und kann dabei Tokens liefern, die sich bei jedem Aufruf ändern oder
> nach kurzer Zeit ungültig werden — ungeeignet für den Dauerbetrieb.

Ein neues Gerät lässt sich auch direkt per Name + IP hinzufügen:

```bash
python3 midea_refresh_tokens.py --name Kueche --host 192.168.0.190
```

## Tägliche Nutzung

### iECO sicherstellen (schaltet bei Bedarf auch ein)

```bash
python3 midea_ieco_ensure.py Wohnzimmer
python3 midea_ieco_ensure.py all
```

### iECO nur nachziehen, wenn das Gerät bereits läuft (empfohlen für Cron)

```bash
python3 midea_ieco_ensure.py all --only-if-on
```

Mit `--only-if-on` wird **nichts** eingeschaltet. Ist ein Gerät aus, wird
es unangetastet übersprungen. Ist es an, wird iECO bei Bedarf aktiviert.
Das macht diesen Aufruf sicher für einen häufig laufenden Cronjob, ohne
ungewollt Klimaanlagen zu starten, die bewusst ausgeschaltet sind.

### Token/Key erneuern (falls ein Gerät mal „Connection reset"/Timeout meldet)

```bash
python3 midea_refresh_tokens.py --name Wohnzimmer
python3 midea_refresh_tokens.py --all
```

In der Praxis bleiben Token/Key sehr lange gültig. Ein erneutes Holen ist
nur nötig, wenn sich z.B. die App-Session grundlegend ändert oder ein
Gerät neu verbunden wird.

## Automatisierung per Cron

Beispiel `crontab -e`:

```cron
# Alle 20 Minuten: iECO nachziehen, ohne Geraete einzuschalten
*/20 * * * * cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> ieco.log 2>&1

# Woechentlich Sonntags 03:00: Token/Key vorsichtshalber erneuern
0 3 * * 0 cd /home/USER/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all >> refresh.log 2>&1
```

Log-Rotation nicht vergessen, z.B. via `logrotate` oder einfach:

```cron
0 0 1 * * truncate -s 0 /home/USER/midea-ieco/ieco.log
```

## Einsatz mit Siri (iOS Shortcuts)

Die einfachste Variante ohne zusätzliche Server-Software ist die native
iOS-Shortcuts-Aktion **„Run Script over SSH"**.

### Voraussetzungen auf dem Linux-Rechner

- OpenSSH-Server läuft und ist im lokalen Netz (oder per VPN) erreichbar
- Ein eigener SSH-Key für das iPhone (empfohlen statt Passwort-Login)

### Einrichtung

1. Auf dem iPhone: **Kurzbefehle-App** → „+" → Aktion **„Skript über SSH ausführen"**
   hinzufügen.
2. Host, Benutzername und Authentifizierung (SSH-Key empfohlen) eintragen.
3. Als Skript hinterlegen, z.B.:

   ```bash
   cd /home/USER/midea-ieco && venv/bin/python3 midea_ieco_ensure.py Wohnzimmer
   ```

4. Kurzbefehl umbenennen, z.B. „Klimaanlage Wohnzimmer iECO".
5. Unter **Automation → Neue Automation → Siri** die Phrase festlegen,
   z.B. „Aktiviere Öko-Modus Wohnzimmer", und den erstellten Kurzbefehl
   verknüpfen.
6. Danach reicht: *„Hey Siri, aktiviere Öko-Modus Wohnzimmer"*.

> **Tipp für SSH in nicht-interaktiven Shells:** Nutze im Skript den
> direkten Pfad `venv/bin/python3` statt `source venv/bin/activate &&
> python3` — das ist robuster, da manche Shells `source` in
> nicht-interaktiven SSH-Sessions unterschiedlich behandeln.

Für den generellen „alle Geräte"-Fall einfach `all` statt des Gerätenamens
verwenden, ggf. mit `--only-if-on`, falls Siri nicht ungewollt einschalten soll.

### Alternative: Homebridge + HomeKit

Wer die Geräte lieber als reguläre Schalter in Apple Home/HomeKit sehen
möchte (inkl. Statusanzeige, Szenen, Automationen), kann Homebridge mit
dem Plugin `homebridge-cmd4` einsetzen. Dabei wird jedem Schalter ein
beliebiger Shell-Befehl für „An"/„Aus"/„Status" zugeordnet, z.B.
`midea_ieco_ensure.py Wohnzimmer` für „An". Das ist aufwendiger einzurichten
als die reine SSH-Shortcuts-Lösung, bietet dafür aber vollwertige
HomeKit-Integration.

## Lessons Learned bei der Entwicklung

Diese Tabelle dokumentiert die konkreten Fehlerbilder, die während der
Entwicklung dieses Setups mit `msmart-ng` (Stand 2026) auftraten und deren
tatsächliche Ursachen — nicht als allgemeine Troubleshooting-Anleitung für
jede Zukunftsversion, sondern als Referenz, falls ähnliche Symptome erneut
auftauchen. Bei neueren `msmart-ng`/`midea-local`-Versionen können sich
interne APIs geändert haben; im Zweifel hilft ein Blick in die tatsächlich
installierte Version:

```bash
python3 -c "import inspect; from msmart.device.AC.device import AirConditioner as AC; print(inspect.signature(AC.__init__))"
```

| Symptom | Ursache (zum Zeitpunkt der Entwicklung) | Lösung |
|---|---|---|
| `TypeError: device_selector() got an unexpected keyword argument` | `midea-local`-API hat sich geändert | `python3 -c "import inspect; from midealocal.devices import device_selector; print(inspect.signature(device_selector))"` prüfen |
| `Device is not capable of property IECO` | Capabilities wurden nicht (oder mit beschädigtem Objekt) abgefragt | Sicherstellen, dass `get_capabilities()` vor `refresh()`/`apply()` auf einem frischen `AC`-Objekt läuft |
| Capabilities-Abfrage liefert Timeout / `Failed to query capabilities` obwohl Token korrekt sind | Das Gerät beantwortet `get_capabilities()` nur im **eingeschalteten** Zustand | Erst einschalten (`power_state = True` + `apply()`), dann Capabilities abfragen — `midea_ieco_ensure.py` macht das bereits in der richtigen Reihenfolge |
| `[Errno 104] Connection reset by peer` bei mehreren Versuchen in Folge | Ein einmal fehlgeschlagener Verbindungsversuch hinterlässt einen kaputten Socket-Zustand im `AC`-Objekt | Bei jedem Retry ein **neues** `AC`-Objekt erzeugen (so umgesetzt in `midea_ieco_ensure.py`) |
| Token/Key funktionieren plötzlich nicht mehr | Meist selbstverursacht durch obigen Socket-Bug, seltener echte Invalidierung | `midea_refresh_tokens.py --name <Geraet>` ausführen |
| `msmart-ng discover` liefert Token, die nach kurzer Zeit nicht mehr funktionieren | `--auto` nutzt einen internen Hilfs-Account, dessen Keys temporär sind | Statt `discover --auto` immer `midea_refresh_tokens.py` (via `midea-local`) für dauerhafte Token nutzen |

## Sicherheitshinweise

- `devices.json` enthält sensible Token/Key-Werte → `chmod 600 devices.json`
- `midea_refresh_tokens.py` enthält dein Cloud-Passwort im Klartext →
  `chmod 600 midea_refresh_tokens.py`
- Für Siri per SSH: SSH-Key-Auth statt Passwort verwenden, SSH-Zugriff
  nicht ungeschützt ins Internet weiterleiten (Port-Forwarding vermeiden,
  stattdessen VPN nutzen, falls Fernzugriff gewünscht ist)

## Lizenz / Weitergabe

Diese Skripte dürfen frei weitergegeben und angepasst werden. Sie basieren
auf den offenen Bibliotheken `msmart-ng` und `midea-local` und ersetzen
keine offizielle Midea-Unterstützung.
