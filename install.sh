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
if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
    error "Python 3.10+ erforderlich (gefunden: $PY_MAJOR.$PY_MINOR)."
fi
ok "Python $PY_MAJOR.$PY_MINOR gefunden."

if ! python3 -m venv --help &>/dev/null; then
    warn "venv-Modul fehlt. Versuche Installation..."
    case "$PKG_MGR" in
        apt) install_pkg "python3-venv" || install_pkg "python${PY_MAJOR}.${PY_MINOR}-venv" ;;
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
# 3. Zielverzeichnisse anlegen
# =============================================================================
# WICHTIG: Nur das ZIEL-Unterverzeichnis selbst wird bei Bedarf per chown
# in den Besitz des aktuellen Nutzers ueberfuehrt - NIEMALS ein bereits
# existierender, potenziell gemeinsam genutzter Elternordner wie /opt/local
# selbst. So bleibt der Besitz von /opt/local unangetastet, falls dort
# bereits andere, root-verwaltete Software liegt.
ensure_target_dir() {
    local dir="$1"
    local parent
    parent="$(dirname "$dir")"

    if [[ ! -d "$parent" ]]; then
        if ! mkdir -p "$parent" 2>/dev/null; then
            info "Benoetige sudo, um $parent anzulegen..."
            sudo mkdir -p "$parent"
        fi
    fi

    if [[ -d "$dir" && -w "$dir" ]]; then
        return 0
    fi

    if [[ ! -d "$dir" ]]; then
        if mkdir -p "$dir" 2>/dev/null; then
            return 0
        fi
        info "Benoetige sudo, um $dir anzulegen..."
        sudo mkdir -p "$dir"
        sudo chown "$(id -u):$(id -g)" "$dir"
    elif [[ ! -w "$dir" ]]; then
        info "Benoetige sudo, um Schreibrechte fuer $dir zu erhalten..."
        sudo chown "$(id -u):$(id -g)" "$dir"
    fi
}

ensure_target_dir "$INSTALL_DIR"
ensure_target_dir "$BIN_DIR"

# =============================================================================
# 4. Projekt-Dateien besorgen
# =============================================================================
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
        TMP_DIR="$(mktemp -d)"
        CLEANUP_PATHS+=("$TMP_DIR")
        TMP_ZIP="$TMP_DIR/midea-ieco.zip"
        curl -fsSL "$REPO_ZIP_URL" -o "$TMP_ZIP"
        unzip -q "$TMP_ZIP" -d "$TMP_DIR/extract"
        cp -R "$TMP_DIR"/extract/*/. "$INSTALL_DIR"/
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
pip install --quiet msmart-ng midea-local
ok "Abhaengigkeiten installiert (msmart-ng, midea-local)."

MSMART_VER=$(pip show msmart-ng 2>/dev/null | grep '^Version' | awk '{print $2}')
MIDEALOCAL_VER=$(pip show midea-local 2>/dev/null | grep '^Version' | awk '{print $2}')
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
echo -e "${YELLOW}--- Midea-Zugangsdaten ---${NC}"
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
# Passwort NICHT per argv an discover uebergeben: kurz eine midea-local.json
# (0600) schreiben, die die CLI aus dem aktuellen Verzeichnis liest, danach
# sofort wieder entfernen.
write_credentials_file midea-local.json || error "midea-local.json konnte nicht geschrieben werden."
DISCOVER_OUTPUT=$(python3 -m midealocal.cli discover 2>&1) || true
rm -f midea-local.json
echo "$DISCOVER_OUTPUT"
echo ""

if ! echo "$DISCOVER_OUTPUT" | grep -qE '([0-9]{1,3}\.){3}[0-9]{1,3}'; then
    warn "Keine Geraete automatisch erkannt."
    warn "Bitte IP-Adressen und Geraete-IDs manuell aus der Ausgabe oben entnehmen."
fi

# =============================================================================
# 9. devices.json interaktiv anlegen (ueber python3/json fuer sichere Escapes)
# =============================================================================
echo ""
echo -e "${YELLOW}--- Geraetekonfiguration ---${NC}"
read -r -p "  Anzahl der Klimaanlagen: " DEVICE_COUNT
[[ "$DEVICE_COUNT" =~ ^[1-9][0-9]*$ ]] || error "Ungueltige Anzahl: '$DEVICE_COUNT'."

IP_REGEX='^([0-9]{1,3}\.){3}[0-9]{1,3}$'
DEVICE_NAMES=(); DEVICE_IPS=(); DEVICE_IDS=()

for (( i=1; i<=DEVICE_COUNT; i++ )); do
    echo ""
    echo "  Geraet $i von $DEVICE_COUNT:"
    read -r -p "    Name (z.B. Wohnzimmer)  : " DEV_NAME
    [[ -z "$DEV_NAME" ]] && error "Gerätename darf nicht leer sein."

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

# JSON wird ueber python3/json erzeugt statt per String-Konkatenation - das
# macht Sonderzeichen in Geraetenamen (Anfuehrungszeichen, Backslashes)
# automatisch sicher. Die Datei wird direkt mit Rechten 0600 angelegt
# (os.open), es entsteht also kein kurzes Zeitfenster, in dem sie
# world-readable waere.
NAMES_JOINED=$(printf '%s\x1e' "${DEVICE_NAMES[@]}")
IPS_JOINED=$(printf '%s\x1e' "${DEVICE_IPS[@]}")
IDS_JOINED=$(printf '%s\x1e' "${DEVICE_IDS[@]}")

python3 - "$NAMES_JOINED" "$IPS_JOINED" "$IDS_JOINED" <<'PYEOF'
import json, os, sys
names = sys.argv[1].split('\x1e')[:-1]
ips = sys.argv[2].split('\x1e')[:-1]
ids = sys.argv[3].split('\x1e')[:-1]
devices = [
    {"name": n, "ip": ip, "port": 6444, "id": int(i), "token": "", "key": ""}
    for n, ip, i in zip(names, ips, ids)
]
fd = os.open("devices.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump({"devices": devices}, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.chmod("devices.json", 0o600)  # falls die Datei bereits existierte
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

WRAPPER_PATH="$BIN_DIR/midea-ieco"
cat > "$WRAPPER_PATH" <<EOF
#!/usr/bin/env bash
# Automatisch von install.sh erzeugter Wrapper.
exec "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/midea_ieco_ensure.py" "\$@"
EOF
chmod +x "$WRAPPER_PATH"
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
CRON_LINE_LOGROTATE="0 0 1 * * truncate -s 0 $IDQ/ieco.log $CRON_MARKER"

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
