#!/usr/bin/env bash
# =============================================================================
# install.sh – midea-ieco Setup-Skript
# =============================================================================
# Kann auf zwei Arten genutzt werden:
#
#   A) Direktausfuehrung per curl (empfohlen fuer Erstinstallation):
#      bash -c "$(curl -fsSL https://raw.githubusercontent.com/tuxbox78/midea-ieco/main/install.sh)"
#
#   B) Lokal nach dem Klonen des Repos:
#      cd midea-ieco && ./install.sh
#
# Installationsort: $HOME/midea-ieco (Standard, per curl-Aufruf), bzw. das
# aktuelle Verzeichnis, wenn lokal aus einem bereits vorhandenen Repo-Ordner
# gestartet. Ueberschreibbar per Umgebungsvariable MIDEA_IECO_DIR.
#
# Unterstuetzte Plattformen: Debian/Ubuntu/Raspberry Pi OS, Fedora/RHEL,
# Arch Linux, Alpine, openSUSE, macOS (mit Homebrew).
# =============================================================================

set -euo pipefail

# --- Repository-Adresse: HIER ggf. anpassen, falls das Repo verschoben wird ---
REPO_URL="https://github.com/tuxbox78/midea-ieco.git"
REPO_ZIP_URL="https://github.com/tuxbox78/midea-ieco/archive/refs/heads/main.zip"
# -------------------------------------------------------------------------

# --- Farben -------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}   midea-ieco Installationsskript               ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo ""

# =============================================================================
# 0. Installationsverzeichnis bestimmen (funktioniert lokal UND per curl-Pipe)
# =============================================================================
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    # Skript liegt als echte Datei vor (lokal ausgefuehrt)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    # Skript wurde per "curl | bash" gestreamt – BASH_SOURCE ist dann leer/ungueltig
    SCRIPT_DIR=""
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/midea_ieco_ensure.py" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    INSTALL_DIR="${MIDEA_IECO_DIR:-$HOME/midea-ieco}"
    info "Installiere nach: $INSTALL_DIR (aendern mit: MIDEA_IECO_DIR=/pfad bash -c \"...\")"
fi

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

# =============================================================================
# 2. Grundwerkzeuge sicherstellen: python3, git/curl, unzip
# =============================================================================
if ! command -v python3 &>/dev/null; then
    warn "python3 nicht gefunden. Versuche automatische Installation..."
    case "$PKG_MGR" in
        pacman) install_pkg python ;;
        *)      install_pkg python3 ;;
    esac || error "python3 konnte nicht automatisch installiert werden. Bitte manuell nachinstallieren."
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
    error "Python 3.10+ erforderlich (gefunden: $PY_MAJOR.$PY_MINOR). Bitte Python aktualisieren."
fi
ok "Python $PY_MAJOR.$PY_MINOR gefunden."

if ! python3 -m venv --help &>/dev/null; then
    warn "venv-Modul fehlt. Versuche Installation..."
    case "$PKG_MGR" in
        apt) install_pkg "python3-venv" || install_pkg "python${PY_MAJOR}.${PY_MINOR}-venv" ;;
        *)   true ;;  # venv ist bei den meisten anderen Distros/macOS bereits Teil von python3
    esac
fi
python3 -m venv --help &>/dev/null || error "venv-Modul fehlt weiterhin. Bitte manuell installieren (z. B. 'sudo apt-get install python3-venv')."
ok "venv-Modul verfuegbar."

if ! command -v git &>/dev/null && ! command -v curl &>/dev/null; then
    warn "Weder git noch curl gefunden. Versuche curl zu installieren..."
    install_pkg curl || error "curl konnte nicht installiert werden. Bitte git oder curl manuell bereitstellen."
fi

# =============================================================================
# 3. Projekt-Dateien besorgen (lokal vorhanden vs. per curl-Pipe gestartet)
# =============================================================================
mkdir -p "$INSTALL_DIR"

if [[ ! -f "$INSTALL_DIR/midea_ieco_ensure.py" ]]; then
    info "Lade Projekt-Dateien nach $INSTALL_DIR ..."
    if command -v git &>/dev/null; then
        if [[ -d "$INSTALL_DIR/.git" ]]; then
            git -C "$INSTALL_DIR" pull --quiet
        else
            git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        fi
    elif command -v curl &>/dev/null; then
        command -v unzip &>/dev/null || install_pkg unzip
        TMP_ZIP="$(mktemp -t midea-ieco.XXXXXX 2>/dev/null || mktemp).zip"
        curl -fsSL "$REPO_ZIP_URL" -o "$TMP_ZIP"
        TMP_EXTRACT="$(mktemp -d)"
        unzip -q "$TMP_ZIP" -d "$TMP_EXTRACT"
        cp -R "$TMP_EXTRACT"/*/. "$INSTALL_DIR"/
        rm -rf "$TMP_ZIP" "$TMP_EXTRACT"
    else
        error "Weder git noch curl verfuegbar. Bitte Repository manuell herunterladen."
    fi
    ok "Projekt-Dateien bereitgestellt in $INSTALL_DIR."
else
    ok "Projekt-Dateien bereits vorhanden in $INSTALL_DIR."
fi

cd "$INSTALL_DIR"

# =============================================================================
# 4. Virtuelle Umgebung + Abhaengigkeiten
# =============================================================================
if [[ -d "venv" ]]; then
    info "venv existiert bereits – ueberspringe Erstellung."
else
    info "Erstelle virtuelle Umgebung..."
    python3 -m venv venv
    ok "venv erstellt."
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet msmart-ng midea-local
ok "Abhaengigkeiten installiert (msmart-ng, midea-local)."

MSMART_VER=$(pip show msmart-ng 2>/dev/null | grep '^Version' | awk '{print $2}')
MIDEALOCAL_VER=$(pip show midea-local 2>/dev/null | grep '^Version' | awk '{print $2}')
info "  msmart-ng    : ${MSMART_VER:-unbekannt}"
info "  midea-local  : ${MIDEALOCAL_VER:-unbekannt}"

# =============================================================================
# 5. WICHTIGER HINWEIS: Feste IP-Adressen im Router
# =============================================================================
echo ""
echo -e "${YELLOW}=================================================${NC}"
echo -e "${YELLOW}   WICHTIG: Feste IP-Adressen empfohlen          ${NC}"
echo -e "${YELLOW}=================================================${NC}"
echo ""
echo "  Bevor du fortfaehrst, richte im Router idealerweise eine"
echo "  DHCP-Reservierung (feste IP nach MAC-Adresse) fuer jede"
echo "  Klimaanlage ein. Andernfalls kann sich die IP-Adresse"
echo "  nach einem Neustart des Geraets oder Routers aendern,"
echo "  und die Steuerung funktioniert dann nicht mehr."
echo ""
echo "  Das ist KEINE Voraussetzung fuer dieses Setup – du kannst"
echo "  die IP-Adresse jederzeit auch NACHTRAEGLICH direkt in der"
echo "  Datei 'devices.json' anpassen, falls sie sich spaeter aendert."
echo ""
read -r -p "  Weiter mit der Einrichtung? [J/n]: " CONTINUE_SETUP
if [[ "$CONTINUE_SETUP" =~ ^[nN]$ ]]; then
    echo ""
    info "Installation der Abhaengigkeiten ist abgeschlossen."
    info "Fuehre './install.sh' erneut aus, sobald feste IPs vergeben sind,"
    info "oder fahre manuell fort mit: source venv/bin/activate"
    exit 0
fi

# =============================================================================
# 6. Midea-Cloud-Zugangsdaten abfragen
# =============================================================================
echo ""
echo -e "${YELLOW}--- Midea-Zugangsdaten ---${NC}"
echo "Bitte deine MSmartHome- / 美的美居-App-Zugangsdaten eingeben."
echo "(Diese werden nur lokal in devices.json und midea_refresh_tokens.py gespeichert.)"
echo ""
read -r -p "  E-Mail-Adresse : " MIDEA_USER
read -r -s -p "  Passwort       : " MIDEA_PASS
echo ""

[[ -z "$MIDEA_USER" || -z "$MIDEA_PASS" ]] && error "E-Mail und Passwort duerfen nicht leer sein."

# =============================================================================
# 7. Geraete im Netzwerk suchen
# =============================================================================
echo ""
info "Suche Midea-Geraete im lokalen Netzwerk (kann etwas dauern)..."
echo ""
DISCOVER_OUTPUT=$(python3 -m midealocal.cli discover --username "$MIDEA_USER" --password "$MIDEA_PASS" 2>&1) || true
echo "$DISCOVER_OUTPUT"
echo ""

FOUND_IPS=$(echo "$DISCOVER_OUTPUT" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | sort -u || true)
if [[ -z "$FOUND_IPS" ]]; then
    warn "Keine Geraete automatisch erkannt."
    warn "Moegliche Ursache: Client-/AP-Isolation im Router oder VLAN-Trennung."
    warn "Bitte IP-Adressen und Geraete-IDs manuell aus der Ausgabe oben entnehmen."
fi

# =============================================================================
# 8. devices.json interaktiv anlegen
# =============================================================================
echo ""
echo -e "${YELLOW}--- Geraetekonfiguration ---${NC}"
read -r -p "  Anzahl der Klimaanlagen: " DEVICE_COUNT
[[ "$DEVICE_COUNT" =~ ^[1-9][0-9]*$ ]] || error "Ungueltige Anzahl: '$DEVICE_COUNT'."

DEVICES_JSON="{
  \"devices\": ["

for (( i=1; i<=DEVICE_COUNT; i++ )); do
    echo ""
    echo "  Geraet $i von $DEVICE_COUNT:"
    read -r -p "    Name (z.B. Wohnzimmer)  : " DEV_NAME
    read -r -p "    IP-Adresse              : " DEV_IP
    read -r -p "    Geraete-ID               : " DEV_ID

    COMMA=""
    [[ $i -lt $DEVICE_COUNT ]] && COMMA=","

    DEVICES_JSON+="
    {
      \"name\": \"$DEV_NAME\",
      \"ip\": \"$DEV_IP\",
      \"port\": 6444,
      \"id\": $DEV_ID,
      \"token\": \"\",
      \"key\": \"\"
    }$COMMA"
done

DEVICES_JSON+="
  ]
}"

printf '%s\n' "$DEVICES_JSON" > devices.json
chmod 600 devices.json
ok "devices.json geschrieben (chmod 600)."
info "Hinweis: IP-Adressen koennen jederzeit direkt in devices.json angepasst werden."

# =============================================================================
# 9. Zugangsdaten in midea_refresh_tokens.py hinterlegen
# =============================================================================
[[ -f "midea_refresh_tokens.py" ]] || error "midea_refresh_tokens.py nicht gefunden."

ESCAPED_USER=$(printf '%s' "$MIDEA_USER" | sed 's/[&/\]/\\&/g')
ESCAPED_PASS=$(printf '%s' "$MIDEA_PASS" | sed 's/[&/\]/\\&/g')

# Kompatibel mit GNU sed (Linux) und BSD sed (macOS)
if sed --version &>/dev/null; then
    sed -i \
        -e "s|DEFAULT_USERNAME = \".*\"|DEFAULT_USERNAME = \"$ESCAPED_USER\"|" \
        -e "s|DEFAULT_PASSWORD = \".*\"|DEFAULT_PASSWORD = \"$ESCAPED_PASS\"|" \
        midea_refresh_tokens.py
else
    sed -i '' \
        -e "s|DEFAULT_USERNAME = \".*\"|DEFAULT_USERNAME = \"$ESCAPED_USER\"|" \
        -e "s|DEFAULT_PASSWORD = \".*\"|DEFAULT_PASSWORD = \"$ESCAPED_PASS\"|" \
        midea_refresh_tokens.py
fi
chmod 600 midea_refresh_tokens.py
ok "Zugangsdaten eingetragen und Datei gesichert (chmod 600)."

# =============================================================================
# 10. Token/Key-Paare abrufen
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
# 11. Skripte ausfuehrbar machen
# =============================================================================
chmod +x midea_ieco_ensure.py midea_refresh_tokens.py 2>/dev/null || true
[[ -f midea_ieco_ensure.sh ]] && chmod +x midea_ieco_ensure.sh
ok "Skripte ausfuehrbar gemacht."

# =============================================================================
# 12. Schnelltest
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
# 13. Cron-Job-Vorschlag
# =============================================================================
CURRENT_USER="$(whoami)"
echo ""
echo -e "${YELLOW}--- Optionaler Cron-Job ---${NC}"
echo ""
echo "*/20 * * * * cd $INSTALL_DIR && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> $INSTALL_DIR/ieco.log 2>&1"
echo "0 3 * * 0 cd $INSTALL_DIR && venv/bin/python3 midea_refresh_tokens.py --all >> $INSTALL_DIR/refresh.log 2>&1"
echo "0 0 1 * * truncate -s 0 $INSTALL_DIR/ieco.log"
echo ""

if command -v crontab &>/dev/null; then
    read -r -p "  Diese Cron-Jobs jetzt automatisch eintragen? [j/N]: " DO_CRON
    if [[ "$DO_CRON" =~ ^[jJyY]$ ]]; then
        ( crontab -l 2>/dev/null || true
          echo "*/20 * * * * cd $INSTALL_DIR && venv/bin/python3 midea_ieco_ensure.py all --only-if-on >> $INSTALL_DIR/ieco.log 2>&1"
          echo "0 3 * * 0 cd $INSTALL_DIR && venv/bin/python3 midea_refresh_tokens.py --all >> $INSTALL_DIR/refresh.log 2>&1"
          echo "0 0 1 * * truncate -s 0 $INSTALL_DIR/ieco.log"
        ) | crontab -
        ok "Cron-Jobs eingetragen fuer Benutzer $CURRENT_USER."
    fi
else
    warn "Kein 'crontab'-Kommando gefunden. Cron-Jobs muessen manuell eingerichtet werden (z. B. via launchd auf macOS)."
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
echo "  Manueller Aufruf:   cd $INSTALL_DIR && venv/bin/python3 midea_ieco_ensure.py <Geraetename>"
echo "  Alle Geraete:       venv/bin/python3 midea_ieco_ensure.py all"
echo "  Token auffrischen:  venv/bin/python3 midea_refresh_tokens.py --all"
echo ""
echo "  Detaillierte Anleitung: README_german.md / README.md"
echo ""
