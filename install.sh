#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# =============================================================================
# install.sh – midea-ieco Setup-Skript
# =============================================================================
set -euo pipefail

DEFAULT_INSTALL_DIR="/opt/local/midea-ieco"
DEFAULT_BIN_DIR="/opt/local/bin"
REPO_URL="https://github.com/tuxbox78/midea-ieco.git"
REPO_ZIP_URL="https://github.com/tuxbox78/midea-ieco/archive/refs/heads/main.zip"
# =============================================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Zentrales Aufraeumen: wird bei JEDEM Skriptende ausgefuehrt (Erfolg, Fehler,
# Abbruch per Strg+C) - verhindert verwaiste Temp-Dateien/Verzeichnisse.
CLEANUP_PATHS=()
cleanup() {
    for p in "${CLEANUP_PATHS[@]:-}"; do
        [[ -n "$p" && -e "$p" ]] && rm -rf "$p"
    done
}
trap cleanup EXIT

echo ""
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}   midea-ieco Installationsskript               ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo ""

# =============================================================================
# 0. Installationsverzeichnisse bestimmen
# =============================================================================
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR=""
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/midea_ieco_ensure.py" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    INSTALL_DIR="${MIDEA_IECO_DIR:-$DEFAULT_INSTALL_DIR}"
fi
BIN_DIR="${MIDEA_IECO_BIN_DIR:-$DEFAULT_BIN_DIR}"

info "Installationsverzeichnis: $INSTALL_DIR"
info "Wrapper-Skript wird abgelegt in: $BIN_DIR"

# =============================================================================
# 1. Plattform und Paketmanager erkennen
# =============================================================================
OS_NAME="$(uname -s)"
case "$OS_NAME" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)      PLATFORM="unknown" ;;
esac

PKG_MGR="none"
if [[ "$PLATFORM" == "linux" ]]; then
    if command -v apt-get &>/dev/null; then PKG_MGR="apt"
    elif command -v dnf &>/dev/null; then PKG_MGR="dnf"
    elif command -v yum &>/dev/null; then PKG_MGR="yum"
    elif command -v pacman &>/dev/null; then PKG_MGR="pacman"
    elif command -v apk &>/dev/null; then PKG_MGR="apk"
    elif command -v zypper &>/dev/null; then PKG_MGR="zypper"
    fi
elif [[ "$PLATFORM" == "macos" ]]; then
    if command -v brew &>/dev/null; then
        PKG_MGR="brew"
    else
        warn "Homebrew nicht gefunden. Installation unter: https://brew.sh"
    fi
fi

info "Erkannte Plattform: $PLATFORM (Paketmanager: $PKG_MGR)"

install_pkg() {
    local pkg="$1"
    case "$PKG_MGR" in
        apt)    sudo apt-get update -qq && sudo apt-get install -y "$pkg" ;;
        dnf)    sudo dnf install -y "$pkg" ;;
        yum)    sudo yum install -y "$pkg" ;;
        pacman) sudo pacman -Sy --noconfirm "$pkg" ;;
        apk)    sudo apk add "$pkg" ;;
        zypper) sudo zypper install -y "$pkg" ;;
        brew)   brew install "$pkg" ;;
        *) return 1 ;;
    esac
}

# Prueft, ob die Kern-Abhaengigkeiten wirklich importierbar sind. Nutzt den nach
# 'source venv/bin/activate' aktiven venv-python (daher erst nach der venv-
# Einrichtung aufrufen). Rueckgabe 0 = beide Module importierbar. Faengt
# insbesondere den Fall ab, dass midea-local 6.6.1 'typing_extensions'
# importiert, es aber nicht als Dependency deklariert.
check_core_imports() {
    python3 -c "import midealocal.cli, msmart.device.AC.device" 2>/dev/null
}

# Schreibt {"username":..,"password":..} atomar und mit Rechten 0600 in die
# Datei $1. Die Zugangsdaten werden ueber Umgebungsvariablen an python3
# uebergeben, NICHT ueber argv - /proc/<pid>/environ ist (anders als
# /proc/<pid>/cmdline) nicht fuer andere lokale Nutzer lesbar. Der atomare
# mkstemp+os.replace-Weg vermeidet ein world-readable-Zeitfenster und einen
# zerstoerten Torso, auch wenn die Zieldatei bereits existierte.
write_credentials_file() {
    local target="$1"
    TARGET="$target" MIDEA_USER="$MIDEA_USER" MIDEA_PASS="$MIDEA_PASS" python3 - <<'PYEOF'
import json, os, tempfile
target = os.environ["TARGET"]
data = {"username": os.environ["MIDEA_USER"], "password": os.environ["MIDEA_PASS"]}
directory = os.path.dirname(os.path.abspath(target)) or "."
fd, tmp = tempfile.mkstemp(dir=directory,
                           prefix="." + os.path.basename(target) + ".", suffix=".tmp")
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
except BaseException:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    raise
PYEOF
}

# Quotet einen Pfad fuer die sichere Verwendung im Kommando-Feld eines
# crontab-Eintrags. cron fuehrt das Kommando ueber /bin/sh aus und wandelt ein
# UNescaptes '%' in einen Zeilenumbruch/stdin-Trenner um (man 5 crontab). Daher:
# (1) den Pfad in Single-Quotes einschliessen und enthaltene ' als '\'' escapen,
# (2) danach jedes '%' als '\%' escapen. Ein Pfad mit echtem Zeilenumbruch ist
# in einer crontab-Zeile nicht darstellbar und wird abgelehnt.
shell_quote_for_cron() {
    local s="$1"
    # error() bricht das Skript ab (exit) - ein Pfad mit Zeilenumbruch kommt
    # hier also nicht weiter.
    case "$s" in
        *$'\n'*) error "Installationspfad enthaelt einen Zeilenumbruch - fuer einen Cron-Eintrag ungeeignet." ;;
    esac
    local q="'\\''"
    s=${s//\'/$q}          # jedes ' -> '\''
    s="'$s'"               # Ergebnis in Single-Quotes einschliessen
    printf '%s' "${s//%/\\%}"   # unescaptes % wuerde cron zu Newline machen
}

# Prueft einen Geraetenamen. Rueckgabe 0 = gueltig; sonst 1 mit einem
# Ablehnungsgrund auf stderr. Abgelehnt werden: leer, fuehrendes '-'
# (sonst als Option missdeutet), das reservierte 'all' (steht fuer 'alle
# Geraete') und Steuerzeichen (die u.a. eine spaetere Weiterverarbeitung
# zerlegen koennten).
is_valid_device_name() {
    local name="$1"
    if [[ -z "$name" ]]; then
        echo "Name darf nicht leer sein." >&2; return 1
    elif [[ "$name" == -* ]]; then
        echo "Name darf nicht mit '-' beginnen (sonst als Option missdeutet)." >&2; return 1
    elif [[ "$name" == "all" ]]; then
        echo "'all' ist reserviert (steht fuer 'alle Geraete') - bitte anders benennen." >&2; return 1
    elif [[ "$name" == *[[:cntrl:]]* ]]; then
        echo "Name darf keine Steuerzeichen enthalten." >&2; return 1
    fi
    return 0
}

# Zerlegt die "<IP>\t<Geraete-ID>"-Zeilenliste des Discovery-Snippets (Abschnitt
# 8) in die globalen Arrays DISC_IPS und DISC_IDS. Leere oder unvollstaendige
# Zeilen werden uebersprungen. Ausgelagert, damit die Zuordnung testbar ist.
parse_discovered() {  # $1 = mehrzeilige "IP<TAB>ID"-Liste
    DISC_IPS=(); DISC_IDS=()
    local _ip _id
    while IFS=$'\t' read -r _ip _id; do
        [[ -n "$_ip" && -n "$_id" ]] && { DISC_IPS+=("$_ip"); DISC_IDS+=("$_id"); }
    done <<< "$1"
}

# =============================================================================
# 2. Grundwerkzeuge: python3, git/curl, unzip
# =============================================================================
if ! command -v python3 &>/dev/null; then
    warn "python3 nicht gefunden. Versuche automatische Installation..."
    case "$PKG_MGR" in
        pacman) install_pkg python ;;
        *)      install_pkg python3 ;;
    esac || error "python3 konnte nicht automatisch installiert werden."
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    error "Python 3.11+ erforderlich (gefunden: $PY_MAJOR.$PY_MINOR).
  Grund: die gepinnte midea-local 6.6.1 setzt Python 3.11 voraus (aktuelle
  Raspberry Pi OS 'Bookworm' liefert 3.11)."
fi
ok "Python $PY_MAJOR.$PY_MINOR gefunden."

if ! python3 -m venv --help &>/dev/null; then
    warn "venv-Modul fehlt. Versuche Installation..."
    case "$PKG_MGR" in
        # '|| true', damit ein fehlgeschlagener Installationsversuch unter
        # 'set -e' NICHT hier abbricht - die verbindliche Pruefung (mit
        # handlungsleitender Meldung) folgt unmittelbar danach.
        apt) install_pkg "python3-venv" || install_pkg "python${PY_MAJOR}.${PY_MINOR}-venv" || true ;;
        *)   true ;;
    esac
fi
python3 -m venv --help &>/dev/null || error "venv-Modul fehlt weiterhin. Bitte manuell installieren."
ok "venv-Modul verfuegbar."

if ! command -v git &>/dev/null && ! command -v curl &>/dev/null; then
    warn "Weder git noch curl gefunden. Versuche curl zu installieren..."
    install_pkg curl || error "curl konnte nicht installiert werden."
fi

# =============================================================================
# 3. Installationsverzeichnis anlegen
# =============================================================================
# WICHTIG: Ein bereits BESTEHENDES Verzeichnis wird NIEMALS automatisch per
# chown uebernommen - es koennte fremde/root-verwaltete Software enthalten
# (z.B. ist der Default /opt/local/bin auf macOS das MacPorts-Bin-Verzeichnis).
# Nur ein von uns SELBST frisch angelegtes Zielverzeichnis wird dem aktuellen
# Nutzer uebereignet. Ein vorhandenes, aber nicht beschreibbares Verzeichnis
# fuehrt zu einem klaren Abbruch mit Handlungsoptionen (kein Besitz-Takeover).
ensure_install_dir() {
    local dir="$1"
    if [[ -d "$dir" && -w "$dir" ]]; then
        return 0
    fi
    if [[ -d "$dir" && ! -w "$dir" ]]; then
        local hint=""
        [[ -f "$dir/midea_ieco_ensure.py" ]] && hint=" Das sieht nach einer frueheren Installation unter einem anderen Benutzer aus."
        error "Installationsverzeichnis $dir existiert, ist aber nicht beschreibbar.$hint
  Bitte eine Option waehlen:
    - anderes Verzeichnis nutzen:   MIDEA_IECO_DIR=/dein/pfad  (Installer erneut ausfuehren)
    - Rechte selbst korrigieren:    sudo chown -R $(id -un) $(printf '%q' "$dir")
    - Verzeichnis entfernen, falls es nicht mehr benoetigt wird."
    fi
    # Verzeichnis existiert noch nicht: zuerst die Elternkette anlegen (bei
    # Bedarf per sudo, aber OHNE chown), dann das Blatt SELBST anlegen.
    local parent; parent="$(dirname "$dir")"
    if [[ ! -d "$parent" ]]; then
        mkdir -p "$parent" 2>/dev/null || { info "Benoetige sudo, um $parent anzulegen..."; sudo mkdir -p "$parent"; }
    fi
    # 'mkdir' OHNE -p: ein zwischenzeitlich von Dritten angelegtes Verzeichnis
    # (TOCTOU) wird NICHT als Erfolg gewertet - dann scheitert auch das folgende
    # 'sudo mkdir' (ebenfalls ohne -p) und wir uebernehmen NICHTS Fremdes.
    if mkdir "$dir" 2>/dev/null; then
        return 0
    fi
    info "Benoetige sudo, um $dir anzulegen..."
    sudo mkdir "$dir"
    sudo chown "$(id -u):$(id -g)" "$dir"
}

ensure_install_dir "$INSTALL_DIR"
# BIN_DIR wird NICHT hier vorbereitet - es wird in Schritt 12 bei Bedarf
# angelegt und der Wrapper per 'install'/sudo hineingelegt, ohne dessen
# Besitzverhaeltnisse zu aendern.

# =============================================================================
# 4. Projekt-Dateien besorgen
# =============================================================================
# Ermittelt das EINE Wurzelverzeichnis eines entpackten GitHub-Archiv-ZIPs
# (Format <repo>-<branch>/...). Bricht mit klarer Fehlermeldung ab, statt eine
# leere oder mehrdeutige Treffermenge stillschweigend an 'cp -R' zu uebergeben -
# eine kuenftig geaenderte Archivstruktur oder ein beschaedigter Download
# wuerden sonst unbemerkt falsche oder gar keine Dateien kopieren. error() ruft
# exit auf; in einer Command-Substitution (siehe Aufrufstelle unten) beendet
# das nur die Subshell - der Aufrufer verlaesst sich bewusst auf 'set -e', das
# eine fehlgeschlagene Command-Substitution in einer EINFACHEN (nicht mit
# 'local' deklarierten) Zuweisung erkennt und das Hauptskript stoppt. Deshalb
# NICHT in eine 'local'-Deklaration verpacken - das wuerde den Exit-Code
# verschlucken (bekannte Bash-Falle) und liesse EXTRACTED_ROOT leer, was ein
# 'cp -R /. ...' ausloesen wuerde.
resolve_extracted_root_dir() {
    local dir="$1"
    local -a subdirs=()
    local entry
    for entry in "$dir"/*/; do
        [[ -d "$entry" ]] && subdirs+=("${entry%/}")
    done
    case "${#subdirs[@]}" in
        1) printf '%s\n' "${subdirs[0]}"; return 0 ;;
        0) error "Kein Wurzelverzeichnis im heruntergeladenen Archiv gefunden (Download beschaedigt oder Archivformat geaendert)." ;;
        *) error "Unerwartete Archivstruktur: mehrere Wurzelverzeichnisse gefunden (${subdirs[*]})." ;;
    esac
}

if command -v git &>/dev/null && [[ -d "$INSTALL_DIR/.git" ]]; then
    # Bestehender Git-Clone: auf den neuesten Stand bringen, damit Updates (z.B.
    # an requirements.txt) auch bei einer RE-Installation ankommen - ohne das
    # behielte eine vorhandene Installation dauerhaft ihre alten Dateien (genau
    # das liess z.B. einen fehlenden typing_extensions-Pin nie ankommen). Nur
    # Fast-Forward; die Zugangs-/Geraetedateien sind git-ignoriert und bleiben
    # unangetastet. Schlaegt der Pull fehl (lokale Aenderungen, kein Netz), wird
    # mit den vorhandenen Dateien weitergemacht statt abzubrechen - die
    # Import-Pruefung weiter unten faengt echte Abhaengigkeitsluecken ohnehin ab.
    info "Aktualisiere vorhandene Installation (git pull)..."
    if git -C "$INSTALL_DIR" pull --ff-only --quiet; then
        ok "Projekt-Dateien aktualisiert."
    else
        warn "git pull nicht moeglich (lokale Aenderungen o. Netzproblem) - nutze vorhandene Dateien weiter."
    fi
elif [[ ! -f "$INSTALL_DIR/midea_ieco_ensure.py" ]]; then
    info "Lade Projekt-Dateien nach $INSTALL_DIR ..."
    if command -v git &>/dev/null; then
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    elif command -v curl &>/dev/null; then
        command -v unzip &>/dev/null || install_pkg unzip \
            || error "unzip wird fuer den ZIP-Download benoetigt, konnte aber nicht installiert werden (Paketmanager: $PKG_MGR). Bitte git oder unzip manuell installieren."
        TMP_DIR="$(mktemp -d)"
        CLEANUP_PATHS+=("$TMP_DIR")
        TMP_ZIP="$TMP_DIR/midea-ieco.zip"
        curl -fsSL "$REPO_ZIP_URL" -o "$TMP_ZIP"
        unzip -q "$TMP_ZIP" -d "$TMP_DIR/extract"
        EXTRACTED_ROOT="$(resolve_extracted_root_dir "$TMP_DIR/extract")"
        cp -R "$EXTRACTED_ROOT"/. "$INSTALL_DIR"/
    else
        error "Weder git noch curl verfuegbar. Bitte Repository manuell herunterladen."
    fi
    ok "Projekt-Dateien bereitgestellt in $INSTALL_DIR."
else
    ok "Projekt-Dateien bereits vorhanden in $INSTALL_DIR."
fi

cd "$INSTALL_DIR"

# =============================================================================
# 5. Virtuelle Umgebung + Abhaengigkeiten
# =============================================================================
if [[ -d "venv" ]]; then
    info "venv existiert bereits - ueberspringe Erstellung."
else
    info "Erstelle virtuelle Umgebung..."
    python3 -m venv venv
    ok "venv erstellt."
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
# Bevorzugt die gepinnten Versionen aus requirements.txt (reproduzierbar und
# gegen Breaking Changes in msmart-ng/midea-local abgesichert). Fehlt die Datei
# - etwa bei einem unvollstaendigen Download -, wird ungepinnt nachinstalliert.
if [[ -f requirements.txt ]]; then
    pip install --quiet -r requirements.txt
else
    warn "requirements.txt nicht gefunden - installiere msmart-ng/midea-local ungepinnt."
    # typing_extensions explizit mitnehmen: midea-local 6.6.1 importiert es,
    # deklariert es aber NICHT als Dependency (s. requirements.txt-Kommentar).
    pip install --quiet msmart-ng midea-local typing_extensions
fi
ok "Abhaengigkeiten installiert (msmart-ng, midea-local)."

# Sofortige Funktionspruefung der Kern-Abhaengigkeiten, BEVOR nach Zugangsdaten
# gefragt wird. midea-local 6.6.1 importiert 'typing_extensions', deklariert es
# aber nicht - fehlt es, taucht der Fehler sonst erst spaeter als roher Traceback
# mitten in der Geraetesuche auf, nachdem der Nutzer schon sein Passwort
# eingegeben hat. Schlaegt die Pruefung fehl, wird die bekannte Luecke
# AUTOMATISCH geschlossen (typing_extensions nachinstallieren) und erneut
# geprueft - der Installer heilt sich also selbst, statt den Nutzer abzuweisen.
if ! check_core_imports; then
    warn "Kern-Abhaengigkeiten nicht importierbar - installiere fehlende Pakete nach..."
    pip install --quiet typing_extensions || true
    if ! check_core_imports; then
        error "Kern-Abhaengigkeiten weiterhin nicht importierbar - automatische Reparatur fehlgeschlagen.
  Bitte manuell pruefen (Netzwerk?) und erneut starten:
    \"$INSTALL_DIR/venv/bin/pip\" install -r \"$INSTALL_DIR/requirements.txt\""
    fi
    ok "Fehlende Kern-Abhaengigkeit(en) automatisch nachinstalliert."
else
    ok "Kern-Abhaengigkeiten importierbar (midealocal, msmart)."
fi

# Version rein informativ anzeigen - diese Zeilen duerfen den Installer unter
# KEINEN Umstaenden abbrechen. Zwei Fallstricke unter 'set -e -o pipefail':
#  1) KEIN 'exit' im awk. Ein frueher Pipe-Schluss (awk beendet sich nach dem
#     ersten Treffer) toetet den noch schreibenden 'pip show'-Prozess per
#     SIGPIPE; die Pipeline endet dann != 0 (real beobachtet: pip 120/141) und
#     'set -e' bricht die GESAMTE Installation lautlos ab - genau nach dem
#     Abhaengigkeiten-OK, noch vor der ersten Rueckfrage. Ohne 'exit' liest awk
#     die Ausgabe vollstaendig, der Producer laeuft sauber zu Ende. Es gibt je
#     Paket genau eine 'Version:'-Zeile, das Ergebnis bleibt also eindeutig.
#  2) '|| true' als Guard. Liefert 'pip show' selbst einen Fehler (z.B. Paket
#     nicht gefunden), soll die Zuweisung NICHT unter 'set -e' abbrechen -
#     stattdessen greift der ${VAR:-unbekannt}-Fallback unten.
MSMART_VER=$(pip show msmart-ng 2>/dev/null | awk '/^Version:/{print $2}') || true
MIDEALOCAL_VER=$(pip show midea-local 2>/dev/null | awk '/^Version:/{print $2}') || true
info "  msmart-ng    : ${MSMART_VER:-unbekannt}"
info "  midea-local  : ${MIDEALOCAL_VER:-unbekannt}"

# =============================================================================
# 6. Hinweis: Feste IP-Adressen im Router
# =============================================================================
echo ""
echo -e "${YELLOW}=================================================${NC}"
echo -e "${YELLOW}   WICHTIG: Feste IP-Adressen empfohlen          ${NC}"
echo -e "${YELLOW}=================================================${NC}"
echo ""
echo "  Richte im Router idealerweise eine DHCP-Reservierung fuer jede"
echo "  Klimaanlage ein. Das ist KEINE Voraussetzung - die IP kann"
echo "  jederzeit auch nachtraeglich in devices.json angepasst werden."
echo ""
read -r -p "  Weiter mit der Einrichtung? [J/n]: " CONTINUE_SETUP
if [[ "$CONTINUE_SETUP" =~ ^[nN]$ ]]; then
    info "Installation der Abhaengigkeiten ist abgeschlossen."
    exit 0
fi

# =============================================================================
# 7. Midea-Cloud-Zugangsdaten abfragen
# =============================================================================
echo ""
echo -e "${YELLOW}--- Midea-APP-Zugangsdaten ---${NC}"
read -r -p "  E-Mail-Adresse : " MIDEA_USER
# IFS= verhindert, dass fuehrende/abschliessende Leerzeichen im Passwort
# abgeschnitten werden (-r schuetzt zusaetzlich Backslashes).
IFS= read -r -s -p "  Passwort       : " MIDEA_PASS
echo ""

[[ -z "$MIDEA_USER" || -z "$MIDEA_PASS" ]] && error "E-Mail und Passwort duerfen nicht leer sein."

# Zugangsdaten sofort sicher ablegen (0600), noch VOR der Geraetesuche - so
# kann der Discover-Schritt sie ueber eine Config-Datei lesen, statt sie auf
# der Kommandozeile zu uebergeben (sonst waere das Passwort via ps sichtbar).
write_credentials_file credentials.json || error "credentials.json konnte nicht geschrieben werden."
ok "Zugangsdaten in credentials.json gespeichert (chmod 600)."

# =============================================================================
# 8. Geraete im Netzwerk suchen
# =============================================================================
echo ""
info "Suche Midea-Geraete im lokalen Netzwerk (kann etwas dauern)..."
echo ""
# midealocal.discover.discover() macht einen lokalen UDP-Broadcast und liefert
# IP-Adresse + Geraete-ID je Geraet OHNE Cloud-Zugang - genau die zwei Werte,
# die unten fuer devices.json gebraucht werden. Bewusst NICHT das INFO-Log von
# 'python -m midealocal.cli discover' geparst: dessen Ausgabe zeigt nur den
# Geraete-ZUSTAND (Temperatur, Modus, ...) und KEINE IP/ID, weshalb die alte
# IP-Regex faelschlich "keine Geraete" meldete, obwohl welche gefunden wurden.
# Ausgabe je gefundenem Geraet: "<IP>\t<Geraete-ID>". Exit 0 = >=1 Geraet,
# 1 = keins, 2 = Fehler (z.B. discover nicht importierbar) - unter 'set -e'
# ueber '|| DISCOVER_RC=$?' abgefangen, damit ein leeres Ergebnis nicht abbricht.
DISCOVER_RC=0
DISCOVERED=$(python3 - <<'PYEOF' 2>/dev/null
import sys
try:
    from midealocal.discover import discover
    devices = discover() or {}
except Exception:
    sys.exit(2)
rows = [f"{d.get('ip_address')}\t{d.get('device_id')}"
        for d in devices.values() if d.get("ip_address") and d.get("device_id")]
print("\n".join(rows))
sys.exit(0 if rows else 1)
PYEOF
) || DISCOVER_RC=$?

if [[ "$DISCOVER_RC" -eq 0 && -n "$DISCOVERED" ]]; then
    ok "Gefundene Geraete - diese IP und Geraete-ID gleich unten eintragen:"
    printf "    %-15s  %s\n" "IP-ADRESSE" "GERAETE-ID"
    printf "    %-15s  %s\n" "---------------" "----------"
    while IFS=$'\t' read -r disc_ip disc_id; do
        printf "    %-15s  %s\n" "$disc_ip" "$disc_id"
    done <<< "$DISCOVERED"
    echo ""
else
    warn "Keine Geraete automatisch erkannt (Netzwerk-/Client-Isolation? Geraet aus?)."
    warn "IP und Geraete-ID koennen auch manuell eingetragen werden - siehe README, 'Netzwerk-Fehlerbehebung'."
fi

# =============================================================================
# 9. devices.json interaktiv anlegen (ueber python3/json fuer sichere Escapes)
# =============================================================================
echo ""
echo -e "${YELLOW}--- Geraetekonfiguration ---${NC}"

IP_REGEX='^([0-9]{1,3}\.){3}[0-9]{1,3}$'
DEVICE_NAMES=(); DEVICE_IPS=(); DEVICE_IDS=()

# IP+ID der in Abschnitt 8 erkannten Geraete uebernehmen, damit der Nutzer die
# (langen, fehleranfaelligen) Werte nicht von Hand abtippen muss.
DISC_IPS=(); DISC_IDS=()
if [[ "$DISCOVER_RC" -eq 0 && -n "$DISCOVERED" ]]; then
    parse_discovered "$DISCOVERED"
fi

USE_DISCOVERED=""
if [[ "${#DISC_IPS[@]}" -gt 0 ]]; then
    read -r -p "  ${#DISC_IPS[@]} Geraet(e) erkannt - IP/ID automatisch uebernehmen und nur Namen vergeben? [J/n]: " ANS_AUTO
    [[ ! "$ANS_AUTO" =~ ^[nN]$ ]] && USE_DISCOVERED="yes"
fi

if [[ -n "$USE_DISCOVERED" ]]; then
    # Auto-Befuellung: IP/ID stammen aus der Discovery, nur noch Name je Geraet.
    for (( i=0; i<${#DISC_IPS[@]}; i++ )); do
        echo ""
        echo "  Geraet $((i + 1)) von ${#DISC_IPS[@]}:  IP ${DISC_IPS[i]}   ID ${DISC_IDS[i]}"
        while true; do
            read -r -p "    Name (z.B. Wohnzimmer): " DEV_NAME
            if reason="$(is_valid_device_name "$DEV_NAME" 2>&1)"; then
                break
            fi
            warn "$reason"
        done
        DEVICE_NAMES+=("$DEV_NAME"); DEVICE_IPS+=("${DISC_IPS[i]}"); DEVICE_IDS+=("${DISC_IDS[i]}")
    done
else
    # Manuelle Eingabe (Fallback: nichts erkannt oder bewusst abgelehnt).
    read -r -p "  Anzahl der Klimaanlagen: " DEVICE_COUNT
    [[ "$DEVICE_COUNT" =~ ^[1-9][0-9]*$ ]] || error "Ungueltige Anzahl: '$DEVICE_COUNT'."
    for (( i=1; i<=DEVICE_COUNT; i++ )); do
        echo ""
        echo "  Geraet $i von $DEVICE_COUNT:"
        while true; do
            read -r -p "    Name (z.B. Wohnzimmer)  : " DEV_NAME
            if reason="$(is_valid_device_name "$DEV_NAME" 2>&1)"; then
                break
            fi
            warn "$reason"
        done

        while true; do
            read -r -p "    IP-Adresse              : " DEV_IP
            [[ "$DEV_IP" =~ $IP_REGEX ]] && break
            warn "Ungueltiges IP-Format. Beispiel: 192.168.0.186"
        done

        while true; do
            read -r -p "    Geraete-ID (nur Ziffern): " DEV_ID
            [[ "$DEV_ID" =~ ^[0-9]+$ ]] && break
            warn "Geraete-ID muss numerisch sein (siehe 'id' aus der Discover-Ausgabe)."
        done

        DEVICE_NAMES+=("$DEV_NAME"); DEVICE_IPS+=("$DEV_IP"); DEVICE_IDS+=("$DEV_ID")
    done
fi

# JSON wird ueber python3/json erzeugt statt per String-Konkatenation - das
# macht Sonderzeichen in Geraetenamen (Anfuehrungszeichen, Backslashes, Umlaute)
# automatisch sicher. Die Werte werden als flache (name, ip, id)-Tripelfolge per
# argv uebergeben (kein In-Band-Trennzeichen wie \x1e mehr, das ein Name enthalten
# koennte). Geschrieben wird atomar mit mkstemp + os.replace und Rechten 0600 -
# kein world-readable-Zeitfenster und kein zerstoerter Torso, auch wenn
# devices.json bereits existierte.
DEVICE_ARGS=()
for (( i=0; i<${#DEVICE_NAMES[@]}; i++ )); do
    DEVICE_ARGS+=("${DEVICE_NAMES[i]}" "${DEVICE_IPS[i]}" "${DEVICE_IDS[i]}")
done

python3 - "${DEVICE_ARGS[@]}" <<'PYEOF'
import json, os, sys, tempfile
args = sys.argv[1:]
devices = [
    {"name": args[k], "ip": args[k + 1], "port": 6444,
     "id": int(args[k + 2]), "token": "", "key": ""}
    for k in range(0, len(args), 3)
]
target = "devices.json"
directory = os.path.dirname(os.path.abspath(target)) or "."
fd, tmp = tempfile.mkstemp(dir=directory,
                           prefix="." + os.path.basename(target) + ".", suffix=".tmp")
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"devices": devices}, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
except BaseException:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    raise
PYEOF

ok "devices.json geschrieben (chmod 600)."

# =============================================================================
# 10. (Zugangsdaten wurden bereits in Schritt 7 in credentials.json abgelegt.)
# =============================================================================

# =============================================================================
# 11. Token/Key-Paare abrufen
# =============================================================================
echo ""
info "Rufe Token/Key-Paare fuer alle Geraete ab..."
if python3 midea_refresh_tokens.py --all; then
    ok "Token/Key-Paare erfolgreich abgerufen."
else
    warn "Token-Abruf mit Fehlern beendet. Spaeter wiederholen mit:"
    warn "  cd $INSTALL_DIR && venv/bin/python3 midea_refresh_tokens.py --all"
fi

# =============================================================================
# 12. Wrapper in BIN_DIR anlegen
# =============================================================================
chmod +x midea_ieco_ensure.py midea_refresh_tokens.py 2>/dev/null || true

# BIN_DIR bei Bedarf anlegen - OHNE chown. Root-eigene bin-Verzeichnisse (z.B.
# /opt/local/bin) sind der Normalfall; wir legen EINE Datei hinein, statt das
# Verzeichnis zu uebernehmen.
if [[ ! -d "$BIN_DIR" ]]; then
    mkdir -p "$BIN_DIR" 2>/dev/null || { info "Benoetige sudo, um $BIN_DIR anzulegen..."; sudo mkdir -p "$BIN_DIR"; }
fi

WRAPPER_PATH="$BIN_DIR/midea-ieco"
# Wrapper zunaechst in eine temporaere Datei schreiben, dann installieren - so
# laesst sich bei nicht beschreibbarem (root-eigenem) BIN_DIR gezielt EINE Datei
# per sudo ablegen, ohne die Besitzverhaeltnisse des Verzeichnisses zu aendern.
WRAPPER_TMP="$(mktemp)"
CLEANUP_PATHS+=("$WRAPPER_TMP")
# $INSTALL_DIR shell-sicher vorquoten (printf %q), statt es hier nur manuell in
# "..." einzuschliessen: ein Pfad mit " oder $(...) wuerde sonst die Quotierung
# der ERZEUGTEN Wrapper-Datei aufbrechen bzw. beim spaeteren Ausfuehren des
# Wrappers erneut als Shell-Syntax interpretiert. %q liefert bereits eine
# selbst-quotende Form - direkt an die Pfadsuffixe angehaengt ergibt das
# weiterhin EIN zusammenhaengendes Wort (keine zusaetzlichen "..." noetig;
# die wuerden bei einer Single-Quote-Ausgabeform von %q sogar Literal-
# Apostrophe in den Pfad einbauen).
INSTALL_DIR_Q="$(printf '%q' "$INSTALL_DIR")"
cat > "$WRAPPER_TMP" <<EOF
#!/usr/bin/env bash
# Automatisch von install.sh erzeugter Wrapper.
exec ${INSTALL_DIR_Q}/venv/bin/python3 ${INSTALL_DIR_Q}/midea_ieco_ensure.py "\$@"
EOF

if [[ -w "$BIN_DIR" ]]; then
    install -m 0755 "$WRAPPER_TMP" "$WRAPPER_PATH" 2>/dev/null \
        || { cp "$WRAPPER_TMP" "$WRAPPER_PATH" && chmod 0755 "$WRAPPER_PATH"; }
else
    info "Benoetige sudo, um den Wrapper nach $BIN_DIR zu schreiben..."
    sudo install -m 0755 "$WRAPPER_TMP" "$WRAPPER_PATH" 2>/dev/null \
        || { sudo cp "$WRAPPER_TMP" "$WRAPPER_PATH" && sudo chmod 0755 "$WRAPPER_PATH"; }
fi
ok "Wrapper-Skript angelegt: $WRAPPER_PATH"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR ist nicht im PATH. Fuege ggf. hinzu mit: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# =============================================================================
# 13. Schnelltest
# =============================================================================
echo ""
read -r -p "  Testlauf fuer das erste Geraet durchfuehren? [j/N]: " DO_TEST
if [[ "$DO_TEST" =~ ^[jJyY]$ ]]; then
    FIRST_DEVICE=$(python3 -c "import json; print(json.load(open('devices.json'))['devices'][0]['name'])")
    info "Teste Geraet: $FIRST_DEVICE"
    if python3 midea_ieco_ensure.py "$FIRST_DEVICE"; then
        ok "Testlauf erfolgreich!"
    else
        warn "Testlauf fehlgeschlagen. Siehe Abschnitt 'Netzwerk-Fehlerbehebung' in README_german.md."
    fi
fi

deactivate 2>/dev/null || true

# =============================================================================
# 14. Cron-Job-Vorschlag (idempotent - keine Duplikate bei erneutem Lauf)
# =============================================================================
CRON_MARKER="# midea-ieco-managed"
# Pfad cron-sicher quoten (Leerzeichen/Sonderzeichen/%); dieselbe gequotete
# Form speist sowohl die Anzeige als auch den crontab-Eintrag.
IDQ="$(shell_quote_for_cron "$INSTALL_DIR")"
CRON_LINE_IECO="*/20 * * * * cd $IDQ && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> $IDQ/ieco.log 2>&1 $CRON_MARKER"
CRON_LINE_REFRESH="0 3 * * 0 cd $IDQ && venv/bin/python3 midea_refresh_tokens.py --all >> $IDQ/refresh.log 2>&1 $CRON_MARKER"
# truncate akzeptiert mehrere Dateioperanden (GNU wie BSD/macOS) - ein Lauf
# leert beide Logs, statt refresh.log unbegrenzt wachsen zu lassen.
CRON_LINE_LOGROTATE="0 0 1 * * truncate -s 0 $IDQ/ieco.log $IDQ/refresh.log $CRON_MARKER"

echo ""
echo -e "${YELLOW}--- Optionaler Cron-Job ---${NC}"
echo ""
echo "$CRON_LINE_IECO"
echo "$CRON_LINE_REFRESH"
echo "$CRON_LINE_LOGROTATE"
echo ""

if command -v crontab &>/dev/null; then
    read -r -p "  Diese Cron-Jobs jetzt automatisch eintragen? [j/N]: " DO_CRON
    if [[ "$DO_CRON" =~ ^[jJyY]$ ]]; then
        EXISTING_CRON=$(crontab -l 2>/dev/null || true)
        if echo "$EXISTING_CRON" | grep -qF "$CRON_MARKER"; then
            warn "midea-ieco-Cron-Jobs sind bereits eingetragen. Ueberspringe, um Duplikate zu vermeiden."
        else
            { echo "$EXISTING_CRON"
              echo "$CRON_LINE_IECO"
              echo "$CRON_LINE_REFRESH"
              echo "$CRON_LINE_LOGROTATE"
            } | crontab -
            ok "Cron-Jobs eingetragen fuer Benutzer $(whoami)."
        fi
    fi
else
    warn "Kein 'crontab'-Kommando gefunden. Cron-Jobs muessen manuell eingerichtet werden."
fi

# =============================================================================
# Fertig
# =============================================================================
echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   Installation abgeschlossen!                  ${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
echo "  Verzeichnis:        $INSTALL_DIR"
echo "  Wrapper-Befehl:     midea-ieco <Geraetename>   (falls $BIN_DIR im PATH ist)"
echo "  Direkter Aufruf:    cd $INSTALL_DIR && venv/bin/python3 midea_ieco_ensure.py <Geraetename>"
echo "  Alle Geraete:       venv/bin/python3 midea_ieco_ensure.py all"
echo "  Token auffrischen:  venv/bin/python3 midea_refresh_tokens.py --all"
echo ""
echo "  Detaillierte Anleitung: README_german.md / README.md"
echo ""
