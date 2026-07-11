#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# =============================================================================
# install.sh – midea-ieco Setup- und Update-Skript
#
# Zwei Betriebsmodi (Auswahl ueber Argumente, siehe print_usage):
#   (ohne)         Erstinstallation bzw. interaktive Einrichtung (Onboarding).
#   --update       NUR aktualisieren: Code + Abhaengigkeiten + Wrapper erneuern,
#                  OHNE Onboarding. devices.json/Cron bleiben
#                  unangetastet. Wird vom erzeugten Befehl 'midea-ieco-update'
#                  aufgerufen.
#   --reconfigure  Onboarding erneut durchlaufen, auch wenn schon konfiguriert
#                  (vorhandene devices.json wird vorher nach .bak gesichert).
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
# WICHTIG: 'exec' ersetzt den Prozess und VERWIRFT diesen Trap - Temp-Dateien,
# die eine per exec verlassene Phase anlegt, muessen daher entweder vor dem exec
# selbst entfernt oder ueber eine Umgebungsvariable an die letzte (nicht mehr
# exec'ende) Phase durchgereicht werden. Siehe run_update und
# download_and_overlay_zip.
CLEANUP_PATHS=()
cleanup() {
    for p in "${CLEANUP_PATHS[@]:-}"; do
        [[ -n "$p" && -e "$p" ]] && rm -rf "$p"
    done
    # WICHTIG: immer 0 zurueckgeben. Ein EXIT-Trap, dessen letztes Kommando != 0
    # endet, ueberschreibt sonst den eigentlichen Exit-Code des Skripts (z.B. das
    # 'exit 0' am Ende der Update-apply-Phase). Der Fall tritt auf, sobald der
    # LETZTE CLEANUP_PATHS-Eintrag nicht mehr existiert (etwa ein bereits von
    # install_bin_wrapper selbst entferntes Temp-File) - dann liefert die
    # '[[ ... ]] && rm'-Kurzschluss-Kette 1.
    return 0
}
trap cleanup EXIT

# =============================================================================
# Sprachwahl (i18n). Englisch als Default, Deutsch automatisch bei de_*-Locale.
# Praezedenz: --lang de|en  >  MIDEA_IECO_LANG  >  Locale (LC_ALL/LC_MESSAGES/
# LANG)  >  'en'. Bewusst OHNE assoziative Arrays (declare -A), damit das Skript
# auf der macOS-System-bash 3.2 lauffaehig bleibt (der curl|bash-Einzeiler).
# =============================================================================
resolve_lang() {
    local raw="${LANG_CHOICE_ARG:-${MIDEA_IECO_LANG:-}}"
    if [[ -z "$raw" ]]; then
        raw="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
    fi
    case "$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')" in
        de|de[_-]*|german|deutsch) printf 'de' ;;
        *)                         printf 'en' ;;
    esac
}

# Uebersetzt einen Katalog-Schluessel in die aktive Sprache (LANG_CHOICE) und
# gibt ihn per printf aus. WICHTIG: dynamische Werte NUR als Argumente ($@)
# uebergeben, NIE in den Formatstring - sonst wuerden '%'/'\' aus Nutzerdaten
# interpretiert. Jeder Arm haelt BEIDE Sprachen nebeneinander (per Test auf
# Vollstaendigkeit geprueft, tests/test_install.sh). Neue Sprache: eine Variable,
# ein Picker-Zweig, je Arm ein Wert. Konvention: literales '%' als '%%' schreiben.
t() {
    local key="$1"; shift
    local en="" de="" fmt=""
    # Katalog-Strings sind Daten: ein '$' (z.B. das literale $PATH in den PATH-
    # Hinweisen) ist gewollter Text, keine Shell-Expansion. SC2016 daher fuer den
    # gesamten Katalog-case bewusst aus.
    # shellcheck disable=SC2016
    case "$key" in
        banner_install)  en='midea-ieco Installer';  de='midea-ieco Installationsskript' ;;
        banner_update)   en='midea-ieco Update';      de='midea-ieco Update' ;;
        err_unknown_option) en="Unknown option: '%s'. '--help' shows the options."
                            de="Unbekannte Option: '%s'. '--help' zeigt die Optionen." ;;
        label_install_dir) en='Install directory:';  de='Installationsverzeichnis:' ;;
        label_bin_dir)     en='Wrapper directory:';  de='Wrapper-Verzeichnis:' ;;
        platform_detected) en='Detected platform: %s (package manager: %s)'
                           de='Erkannte Plattform: %s (Paketmanager: %s)' ;;
        homebrew_missing)  en='Homebrew not found. Install it from: https://brew.sh'
                           de='Homebrew nicht gefunden. Installation unter: https://brew.sh' ;;
        ip_banner_title)   en='IMPORTANT: Fixed IP addresses recommended'
                           de='WICHTIG: Feste IP-Adressen empfohlen' ;;
        ip_hint)           en='  Ideally set up a DHCP reservation for each air conditioner in\n  your router. This is NOT required - the IP can also be adjusted\n  later in devices.json at any time.'
                           de='  Richte im Router idealerweise eine DHCP-Reservierung fuer jede\n  Klimaanlage ein. Das ist KEINE Voraussetzung - die IP kann\n  jederzeit auch nachtraeglich in devices.json angepasst werden.' ;;
        prompt_continue_setup) en='  Continue with setup? [Y/n]: '
                               de='  Weiter mit der Einrichtung? [J/n]: ' ;;
        deps_done_abort)   en='Dependency installation is complete.'
                           de='Installation der Abhaengigkeiten ist abgeschlossen.' ;;
        usage) en="midea-ieco install.sh

  (no option)     First-time / interactive setup (onboarding).
  --update        Update only: refresh code + dependencies + wrappers.
                  Does NOT touch devices.json / cron.
                  (Same as the generated command 'midea-ieco-update'.)
  --reconfigure   Re-run setup even if already configured.
                  Backs up an existing devices.json to .bak first.
  --lang en|de    Force the interface language (default: auto by locale).
  -h, --help      Show this help.

Directories overridable via environment variables:
  MIDEA_IECO_DIR       Install directory (default /opt/local/midea-ieco)
  MIDEA_IECO_BIN_DIR   Wrapper directory (default /opt/local/bin)"
               de="midea-ieco install.sh

  (ohne Option)   Erstinstallation bzw. interaktive Einrichtung (Onboarding).
  --update        Nur aktualisieren: Code + Abhaengigkeiten + Wrapper erneuern.
                  Ruehrt devices.json / Cron NICHT an.
                  (Entspricht dem erzeugten Befehl 'midea-ieco-update'.)
  --reconfigure   Einrichtung erneut durchlaufen, auch wenn schon konfiguriert.
                  Sichert eine vorhandene devices.json vorher nach .bak.
  --lang en|de    Sprache erzwingen (Default: automatisch nach Locale).
  -h, --help      Diese Hilfe anzeigen.

Verzeichnisse ueber Umgebungsvariablen ueberschreibbar:
  MIDEA_IECO_DIR       Installationsverzeichnis (Default /opt/local/midea-ieco)
  MIDEA_IECO_BIN_DIR   Wrapper-Verzeichnis      (Default /opt/local/bin)" ;;

        # ---- Grundwerkzeuge / Verzeichnis / Fetch ----
        err_cron_newline)  en='Install path contains a newline - unsuitable for a cron entry.'
                           de='Installationspfad enthaelt einen Zeilenumbruch - fuer einen Cron-Eintrag ungeeignet.' ;;
        dev_name_empty)    en='Name must not be empty.'
                           de='Name darf nicht leer sein.' ;;
        dev_name_dash)     en="Name must not start with '-' (would be mistaken for an option)."
                           de="Name darf nicht mit '-' beginnen (sonst als Option missdeutet)." ;;
        dev_name_reserved) en="'all' and 'list' are reserved words (all devices / show overview) - please choose another name."
                           de="'all' und 'list' sind reservierte Woerter (alle Geraete / Uebersicht) - bitte anders benennen." ;;
        dev_name_ctrl)     en='Name must not contain control characters.'
                           de='Name darf keine Steuerzeichen enthalten.' ;;
        py_not_found)      en='python3 not found. Attempting automatic installation...'
                           de='python3 nicht gefunden. Versuche automatische Installation...' ;;
        py_install_failed) en='python3 could not be installed automatically.'
                           de='python3 konnte nicht automatisch installiert werden.' ;;
        py_too_old)        en='Python 3.11+ required (found: %s.%s).\n  Reason: the pinned midea-local 6.6.1 requires Python 3.11 (current\n  Raspberry Pi OS Bookworm ships 3.11).'
                           de='Python 3.11+ erforderlich (gefunden: %s.%s).\n  Grund: die gepinnte midea-local 6.6.1 setzt Python 3.11 voraus (aktuelle\n  Raspberry Pi OS Bookworm liefert 3.11).' ;;
        py_found)          en='Python %s.%s found.'
                           de='Python %s.%s gefunden.' ;;
        venv_missing_try)  en='venv module missing. Attempting installation...'
                           de='venv-Modul fehlt. Versuche Installation...' ;;
        venv_still_missing) en='venv module still missing. Please install it manually.'
                            de='venv-Modul fehlt weiterhin. Bitte manuell installieren.' ;;
        venv_ok)           en='venv module available.'
                           de='venv-Modul verfuegbar.' ;;
        git_curl_missing)  en='Neither git nor curl found. Attempting to install curl...'
                           de='Weder git noch curl gefunden. Versuche curl zu installieren...' ;;
        curl_install_failed) en='curl could not be installed.'
                             de='curl konnte nicht installiert werden.' ;;
        dir_hint_prev_install) en='This looks like a previous installation under a different user.'
                               de='Das sieht nach einer frueheren Installation unter einem anderen Benutzer aus.' ;;
        err_dir_not_writable) en='Install directory %s exists but is not writable.%s\n  Please choose one option:\n    - use another directory:      MIDEA_IECO_DIR=/your/path  (re-run the installer)\n    - fix the permissions:        sudo chown -R %s %s\n    - remove the directory if it is no longer needed.'
                              de='Installationsverzeichnis %s existiert, ist aber nicht beschreibbar.%s\n  Bitte eine Option waehlen:\n    - anderes Verzeichnis nutzen:   MIDEA_IECO_DIR=/dein/pfad  (Installer erneut ausfuehren)\n    - Rechte selbst korrigieren:    sudo chown -R %s %s\n    - Verzeichnis entfernen, falls es nicht mehr benoetigt wird.' ;;
        sudo_need_mkdir)   en='Need sudo to create %s ...'
                           de='Benoetige sudo, um %s anzulegen...' ;;
        sudo_need_wrapper) en='Need sudo to write the wrapper to %s ...'
                           de='Benoetige sudo, um den Wrapper nach %s zu schreiben...' ;;
        err_zip_no_root)   en='No root directory found in the downloaded archive (corrupt download or changed archive format).'
                           de='Kein Wurzelverzeichnis im heruntergeladenen Archiv gefunden (Download beschaedigt oder Archivformat geaendert).' ;;
        err_zip_multi_root) en='Unexpected archive structure: multiple root directories found (%s).'
                            de='Unerwartete Archivstruktur: mehrere Wurzelverzeichnisse gefunden (%s).' ;;
        err_zip_needs_curl) en='curl is required for the ZIP download but is not available.'
                            de='curl wird fuer den ZIP-Download benoetigt, ist aber nicht verfuegbar.' ;;
        err_zip_needs_unzip) en='unzip is required for the ZIP download but could not be installed (package manager: %s). Please install git or unzip manually.'
                             de='unzip wird fuer den ZIP-Download benoetigt, konnte aber nicht installiert werden (Paketmanager: %s). Bitte git oder unzip manuell installieren.' ;;
        fetch_git_pull)    en='Updating existing installation (git pull)...'
                           de='Aktualisiere vorhandene Installation (git pull)...' ;;
        fetch_local_changes) en='Local changes to tracked files detected - git pull skipped.'
                             de='Lokale Aenderungen an getrackten Dateien erkannt - git pull uebersprungen.' ;;
        fetch_tip_1)       en='  Tip: set paths via MIDEA_IECO_DIR/MIDEA_IECO_BIN_DIR instead of editing install.sh,'
                           de='  Tipp: Pfade ueber MIDEA_IECO_DIR/MIDEA_IECO_BIN_DIR setzen statt install.sh zu editieren,' ;;
        fetch_tip_2)       en="  or stash changes with 'git -C %s stash' and retry the update."
                           de="  oder Aenderungen mit 'git -C %s stash' zuruecklegen und Update wiederholen." ;;
        fetch_updated)     en='Project files updated.'
                           de='Projekt-Dateien aktualisiert.' ;;
        fetch_pull_failed) en='git pull not possible (no network or non-fast-forward) - NOT updated, continuing with existing files.'
                           de='git pull nicht moeglich (kein Netz oder non-fast-forward) - NICHT aktualisiert, nutze vorhandene Dateien weiter.' ;;
        fetch_zip_update)  en='Updating existing installation (ZIP download)...'
                           de='Aktualisiere vorhandene Installation (ZIP-Download)...' ;;
        fetch_updated_zip) en='Project files updated (ZIP).'
                           de='Projekt-Dateien aktualisiert (ZIP).' ;;
        fetch_downloading) en='Downloading project files to %s ...'
                           de='Lade Projekt-Dateien nach %s ...' ;;
        err_no_git_curl)   en='Neither git nor curl available. Please download the repository manually.'
                           de='Weder git noch curl verfuegbar. Bitte Repository manuell herunterladen.' ;;
        fetch_provided)    en='Project files provided in %s.'
                           de='Projekt-Dateien bereitgestellt in %s.' ;;
        fetch_present)     en='Project files already present in %s.'
                           de='Projekt-Dateien bereits vorhanden in %s.' ;;

        # ---- venv / Abhaengigkeiten / Wrapper / PATH ----
        venv_exists)       en='venv already exists - skipping creation.'
                           de='venv existiert bereits - ueberspringe Erstellung.' ;;
        venv_creating)     en='Creating virtual environment...'
                           de='Erstelle virtuelle Umgebung...' ;;
        venv_created)      en='venv created.'
                           de='venv erstellt.' ;;
        req_missing)       en='requirements.txt not found - installing msmart-ng/midea-local unpinned.'
                           de='requirements.txt nicht gefunden - installiere msmart-ng/midea-local ungepinnt.' ;;
        deps_installed)    en='Dependencies installed (msmart-ng, midea-local).'
                           de='Abhaengigkeiten installiert (msmart-ng, midea-local).' ;;
        core_deps_retry)   en='Core dependencies not importable - installing missing packages...'
                           de='Kern-Abhaengigkeiten nicht importierbar - installiere fehlende Pakete nach...' ;;
        err_core_deps)     en='Core dependencies still not importable - automatic repair failed.\n  Please check manually (network?) and re-run:\n    "%s/venv/bin/pip" install -r "%s/requirements.txt"'
                           de='Kern-Abhaengigkeiten weiterhin nicht importierbar - automatische Reparatur fehlgeschlagen.\n  Bitte manuell pruefen (Netzwerk?) und erneut starten:\n    "%s/venv/bin/pip" install -r "%s/requirements.txt"' ;;
        core_deps_repaired) en='Missing core dependency/dependencies installed automatically.'
                            de='Fehlende Kern-Abhaengigkeit(en) automatisch nachinstalliert.' ;;
        core_deps_ok)      en='Core dependencies importable (midealocal, msmart).'
                           de='Kern-Abhaengigkeiten importierbar (midealocal, msmart).' ;;
        val_unknown)       en='unknown';  de='unbekannt' ;;
        wrapper_created)   en='Wrapper script created: %s'
                           de='Wrapper-Skript angelegt: %s' ;;
        path_not_in_manual) en='%s is not in PATH. Add manually: export PATH="%s:$PATH"'
                            de='%s ist nicht im PATH. Manuell ergaenzen: export PATH="%s:$PATH"' ;;
        path_already_in_rc) en='%s is already set in %s - active in a NEW shell (or now: source %s).'
                            de='%s ist in %s bereits eingetragen - in einer NEUEN Shell aktiv (oder jetzt: source %s).' ;;
        path_add_to_rc_hint) en='%s is not in PATH. Add it to %s with: export PATH="%s:$PATH"'
                             de='%s ist nicht im PATH. In %s ergaenzen mit: export PATH="%s:$PATH"' ;;
        path_prompt_add)   en='  %s is not in PATH. Add it to %s? [Y/n]: '
                           de='  %s ist nicht im PATH. Zu %s hinzufuegen? [J/n]: ' ;;
        path_input_aborted) en='Input aborted. Add manually (to %s): export PATH="%s:$PATH"'
                            de='Eingabe abgebrochen. Manuell ergaenzen (in %s): export PATH="%s:$PATH"' ;;
        path_skipped)      en='Skipped. Add manually (to %s): export PATH="%s:$PATH"'
                           de='Uebersprungen. Manuell ergaenzen (in %s): export PATH="%s:$PATH"' ;;
        path_added)        en='%s added to %s.'
                           de='%s zu %s hinzugefuegt.' ;;
        path_active_now)   en='Active immediately in the CURRENT shell with:  source %s'
                           de='In der AKTUELLEN Shell sofort aktiv mit:  source %s' ;;
        path_write_failed) en='Could not write %s. Add manually: export PATH="%s:$PATH"'
                           de='Konnte %s nicht schreiben. Manuell ergaenzen: export PATH="%s:$PATH"' ;;

        # ---- Update-Modus ----
        err_update_needs_install) en='Update requires an installed copy. install.sh not found in %s.'
                                  de='Update benoetigt eine installierte Kopie. install.sh nicht gefunden unter %s.' ;;
        banner_update_done) en='Update complete!';  de='Update abgeschlossen!' ;;
        update_version_uptodate) en='Version: %s (already up to date)'
                                 de='Version: %s (war bereits aktuell)' ;;
        update_version_changed) en='Version: %s -> %s';  de='Version: %s -> %s' ;;
        update_see_changelog) en='Changes: see CHANGELOG.md'
                              de='Aenderungen siehe CHANGELOG.md' ;;
        err_unknown_phase) en="Unknown internal update phase: '%s'."
                           de="Unbekannte interne Update-Phase: '%s'." ;;

        # ---- Onboarding ----
        already_configured) en='Already set up - onboarding skipped (devices.json untouched).'
                            de='Bereits eingerichtet - Onboarding uebersprungen (devices.json unangetastet).' ;;
        already_cfg_update) en='  Update:               midea-ieco-update'
                            de='  Aktualisieren:        midea-ieco-update' ;;
        already_cfg_reconfigure) en='  Reconfigure:          install.sh --reconfigure'
                                 de='  Neu einrichten:       install.sh --reconfigure' ;;
        devices_backed_up) en='Existing devices.json backed up to devices.json.bak.'
                           de='Vorhandene devices.json nach devices.json.bak gesichert.' ;;
        hint_obsolete_credentials) en='A credentials.json from an earlier version is no longer needed (0.2.0 fetches device tokens without any cloud credentials). You may delete it: rm %s'
                                   de='Eine credentials.json aus einer frueheren Version wird nicht mehr benoetigt (0.2.0 holt die Geraete-Token ohne jegliche Cloud-Zugangsdaten). Du kannst sie loeschen: rm %s' ;;
        discover_searching) en='Searching for Midea devices on the local network (may take a moment)...'
                            de='Suche Midea-Geraete im lokalen Netzwerk (kann etwas dauern)...' ;;
        discover_found)    en='Devices found - enter this IP and device ID below:'
                           de='Gefundene Geraete - diese IP und Geraete-ID gleich unten eintragen:' ;;
        col_ip)            en='IP ADDRESS';  de='IP-ADRESSE' ;;
        col_device_id)     en='DEVICE ID';   de='GERAETE-ID' ;;
        discover_none)     en='No devices detected automatically (network/client isolation? device off?).'
                           de='Keine Geraete automatisch erkannt (Netzwerk-/Client-Isolation? Geraet aus?).' ;;
        discover_manual_hint) en="IP and device ID can also be entered manually - see the README, 'Network troubleshooting'."
                              de="IP und Geraete-ID koennen auch manuell eingetragen werden - siehe README, 'Netzwerk-Fehlerbehebung'." ;;
        hdr_device_config) en='Device configuration';  de='Geraetekonfiguration' ;;
        prompt_use_discovered) en='  %s device(s) detected - adopt IP/ID automatically and only assign names? [Y/n]: '
                               de='  %s Geraet(e) erkannt - IP/ID automatisch uebernehmen und nur Namen vergeben? [J/n]: ' ;;
        dev_line_auto)     en='  Device %s of %s:  IP %s   ID %s'
                           de='  Geraet %s von %s:  IP %s   ID %s' ;;
        prompt_dev_name)   en='    Name (e.g. Living room): '
                           de='    Name (z.B. Wohnzimmer): ' ;;
        prompt_device_count) en='  Number of air conditioners: '
                             de='  Anzahl der Klimaanlagen: ' ;;
        err_invalid_count) en="Invalid number: '%s'."
                           de="Ungueltige Anzahl: '%s'." ;;
        dev_line_manual)   en='  Device %s of %s:'
                           de='  Geraet %s von %s:' ;;
        prompt_dev_ip)     en='    IP address              : '
                           de='    IP-Adresse              : ' ;;
        err_ip_format)     en='Invalid IP format. Example: 192.168.0.186'
                           de='Ungueltiges IP-Format. Beispiel: 192.168.0.186' ;;
        prompt_dev_id)     en='    Device ID (digits only) : '
                           de='    Geraete-ID (nur Ziffern): ' ;;
        err_dev_id_numeric) en="Device ID must be numeric (see 'id' from the discover output)."
                            de="Geraete-ID muss numerisch sein (siehe 'id' aus der Discover-Ausgabe)." ;;
        devices_written)   en='devices.json written (chmod 600).'
                           de='devices.json geschrieben (chmod 600).' ;;
        tokens_fetching)   en='Fetching token/key pairs for all devices...'
                           de='Rufe Token/Key-Paare fuer alle Geraete ab...' ;;
        tokens_fetched)    en='Token/key pairs fetched successfully.'
                           de='Token/Key-Paare erfolgreich abgerufen.' ;;
        tokens_failed)     en='Token fetch finished with errors. Retry later with:'
                           de='Token-Abruf mit Fehlern beendet. Spaeter wiederholen mit:' ;;
        tokens_retry_cmd)  en='  cd %s && venv/bin/python3 midea_refresh_tokens.py --all'
                           de='  cd %s && venv/bin/python3 midea_refresh_tokens.py --all' ;;
        prompt_test_run)   en='  Run a test for the first device? [y/N]: '
                           de='  Testlauf fuer das erste Geraet durchfuehren? [j/N]: ' ;;
        test_running)      en='Testing device: %s';  de='Teste Geraet: %s' ;;
        test_ok)           en='Test run successful!';  de='Testlauf erfolgreich!' ;;
        test_failed)       en="Test run failed. See the 'Network troubleshooting' section in README.md."
                           de="Testlauf fehlgeschlagen. Siehe Abschnitt 'Netzwerk-Fehlerbehebung' in README_german.md." ;;
        hdr_cron)          en='Optional cron job';  de='Optionaler Cron-Job' ;;
        prompt_cron_add)   en='  Add these cron jobs now automatically? [y/N]: '
                           de='  Diese Cron-Jobs jetzt automatisch eintragen? [j/N]: ' ;;
        cron_already)      en='midea-ieco cron jobs are already installed. Skipping to avoid duplicates.'
                           de='midea-ieco-Cron-Jobs sind bereits eingetragen. Ueberspringe, um Duplikate zu vermeiden.' ;;
        cron_added)        en='Cron jobs installed for user %s.'
                           de='Cron-Jobs eingetragen fuer Benutzer %s.' ;;
        cron_no_crontab)   en="No 'crontab' command found. Cron jobs must be set up manually."
                           de="Kein 'crontab'-Kommando gefunden. Cron-Jobs muessen manuell eingerichtet werden." ;;
        banner_install_done) en='Installation complete!';  de='Installation abgeschlossen!' ;;
        final_summary) en="  Directory:          %s
  Wrapper command:    midea-ieco <device-name>   (if %s is in PATH)
  Overview / devices: midea-ieco list
  Update:             midea-ieco-update
  Refresh tokens:     midea-ieco-refresh-tokens --all
  Direct call:        cd %s && venv/bin/python3 midea_ieco_ensure.py <device-name>
  All devices:        venv/bin/python3 midea_ieco_ensure.py all

  Full guide: README.md / README_german.md"
                       de="  Verzeichnis:        %s
  Wrapper-Befehl:     midea-ieco <Geraetename>   (falls %s im PATH ist)
  Uebersicht/Geraete: midea-ieco list
  Aktualisieren:      midea-ieco-update
  Token auffrischen:  midea-ieco-refresh-tokens --all
  Direkter Aufruf:    cd %s && venv/bin/python3 midea_ieco_ensure.py <Geraetename>
  Alle Geraete:       venv/bin/python3 midea_ieco_ensure.py all

  Detaillierte Anleitung: README_german.md / README.md" ;;
    esac
    if [[ "$LANG_CHOICE" == "de" ]]; then fmt="$de"; else fmt="$en"; fi
    # Formatstring stammt aus dem eigenen Katalog (vertrauenswuerdig), dynamische
    # Werte kommen ausschliesslich ueber "$@" - daher SC2059 hier bewusst aus.
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# Vorab-Scan NUR fuer die Sprachwahl, damit Banner/Usage/Fehlermeldungen unten
# bereits in der richtigen Sprache erscheinen. Arbeitet auf einer Kopie und
# aendert $@ NICHT - die eigentliche Optionsauswertung folgt weiter unten.
LANG_CHOICE_ARG=""
if [[ $# -gt 0 ]]; then
    _pre_args=("$@")
    for (( _pi=0; _pi<${#_pre_args[@]}; _pi++ )); do
        case "${_pre_args[_pi]}" in
            # Das Folge-Token nur dann als Sprachwert lesen, wenn es NICHT selbst
            # eine Option ist (Sprachwerte sind stets 'en'/'de', nie '-'-praefigiert).
            # Sonst verschluckte z.B. '--lang --help' die Hilfe als vermeintlichen
            # Wert. Explizites if statt '[[ ]] &&': unter 'set -e' darf die
            # case-Auswertung in der Schleife nicht mit Exit 1 enden.
            --lang)   if [[ "${_pre_args[_pi+1]:-}" != -* ]]; then
                          LANG_CHOICE_ARG="${_pre_args[_pi+1]:-}"
                      fi ;;
            --lang=*) LANG_CHOICE_ARG="${_pre_args[_pi]#--lang=}" ;;
        esac
    done
    unset _pre_args _pi
fi
LANG_CHOICE="$(resolve_lang)"

# Kurze Hilfe. Bewusst vor dem Argument-Parsing definiert, damit '--help'
# ausgewertet werden kann, ohne dass irgendein weiterer Schritt lief.
print_usage() {
    printf '%s\n' "$(t usage)"
}

# =============================================================================
# Argument-Parsing: bestimmt Betriebsmodus. Unbekannte Optionen werden klar
# abgelehnt (statt still ignoriert), damit ein Tippfehler nicht unbemerkt das
# Onboarding statt eines Updates startet.
# =============================================================================
MODE="install"
RECONFIGURE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)      MODE="update" ;;
        --reconfigure) RECONFIGURE=1 ;;
        # Wert im Vorab-Scan gelesen; hier nur konsumieren, wenn ein NICHT-Options-
        # Token folgt - so bleibt '--lang --help' (o.ae.) als eigene Option erhalten.
        --lang)        if [[ $# -ge 2 && "$2" != -* ]]; then shift; fi ;;
        --lang=*)      : ;;
        -h|--help)     print_usage; exit 0 ;;
        *)             error "$(t err_unknown_option "$1")" ;;
    esac
    shift
done

# Interne Phasen-Variable des Update-Modus (leer bei jedem NORMALEN Aufruf).
# Nur bei den self-re-exec'ten Update-Phasen gesetzt (relaunch -> fetch ->
# apply). Wird hier einmal gelesen, um Banner/Info-Zeilen bei den Re-Execs nicht
# dreifach zu wiederholen.
UPDATE_PHASE="${MIDEA_IECO_UPDATE_PHASE:-}"

# Banner nur bei der ERSTEN (nicht re-exec'ten) Ausfuehrung zeigen.
if [[ -z "$UPDATE_PHASE" ]]; then
    echo ""
    echo -e "${BLUE}=================================================${NC}"
    if [[ "$MODE" == "update" ]]; then
        echo -e "${BLUE}   $(t banner_update)${NC}"
    else
        echo -e "${BLUE}   $(t banner_install)${NC}"
    fi
    echo -e "${BLUE}=================================================${NC}"
    echo ""
fi

# =============================================================================
# 0. Installationsverzeichnisse bestimmen
# =============================================================================
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR=""
fi

# Reihenfolge der Aufloesung:
#   1. MIDEA_IECO_RESOLVED_DIR (nur intern gesetzt) - haelt das Zielverzeichnis
#      ueber die 'exec'-Grenzen der Update-Phasen hinweg stabil. Ohne das wuerde
#      die aus einer TEMP-Kopie laufende fetch-Phase (BASH_SOURCE zeigt auf /tmp)
#      das Installationsverzeichnis falsch bestimmen.
#   2. Skriptverzeichnis, falls es die Projektdateien enthaelt (lokaler Aufruf).
#   3. MIDEA_IECO_DIR bzw. der Default (curl|bash-Erstinstallation).
if [[ -n "${MIDEA_IECO_RESOLVED_DIR:-}" ]]; then
    INSTALL_DIR="$MIDEA_IECO_RESOLVED_DIR"
elif [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/midea_ieco_ensure.py" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    INSTALL_DIR="${MIDEA_IECO_DIR:-$DEFAULT_INSTALL_DIR}"
fi
BIN_DIR="${MIDEA_IECO_BIN_DIR:-$DEFAULT_BIN_DIR}"

if [[ -z "$UPDATE_PHASE" ]]; then
    info "$(t label_install_dir) $INSTALL_DIR"
    info "$(t label_bin_dir) $BIN_DIR"
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
        warn "$(t homebrew_missing)"
    fi
fi

[[ -z "$UPDATE_PHASE" ]] && info "$(t platform_detected "$PLATFORM" "$PKG_MGR")"

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
        *$'\n'*) error "$(t err_cron_newline)" ;;
    esac
    local q="'\\''"
    s=${s//\'/$q}          # jedes ' -> '\''
    s="'$s'"               # Ergebnis in Single-Quotes einschliessen
    printf '%s' "${s//%/\\%}"   # unescaptes % wuerde cron zu Newline machen
}

# Prueft einen Geraetenamen. Rueckgabe 0 = gueltig; sonst 1 mit einem
# Ablehnungsgrund auf stderr. Abgelehnt werden: leer, fuehrendes '-'
# (sonst als Option missdeutet), die reservierten Woerter 'all' (alle
# Geraete) und 'list' (Uebersicht) - in Sync mit RESERVED_TARGETS in
# midea_ieco_ensure.py - sowie Steuerzeichen (die u.a. eine spaetere
# Weiterverarbeitung zerlegen koennten).
is_valid_device_name() {
    local name="$1"
    if [[ -z "$name" ]]; then
        printf '%s\n' "$(t dev_name_empty)" >&2; return 1
    elif [[ "$name" == -* ]]; then
        printf '%s\n' "$(t dev_name_dash)" >&2; return 1
    elif [[ "$name" == "all" || "$name" == "list" ]]; then
        printf '%s\n' "$(t dev_name_reserved)" >&2; return 1
    elif [[ "$name" == *[[:cntrl:]]* ]]; then
        printf '%s\n' "$(t dev_name_ctrl)" >&2; return 1
    fi
    return 0
}

# Zerlegt die "<IP>\t<Geraete-ID>"-Zeilenliste des Discovery-Snippets (Abschnitt
# 7) in die globalen Arrays DISC_IPS und DISC_IDS. Leere oder unvollstaendige
# Zeilen werden uebersprungen. Ausgelagert, damit die Zuordnung testbar ist.
parse_discovered() {  # $1 = mehrzeilige "IP<TAB>ID"-Liste
    DISC_IPS=(); DISC_IDS=()
    local _ip _id
    while IFS=$'\t' read -r _ip _id; do
        [[ -n "$_ip" && -n "$_id" ]] && { DISC_IPS+=("$_ip"); DISC_IDS+=("$_id"); }
    done <<< "$1"
}

# =============================================================================
# Grundwerkzeuge: python3 (>=3.11), venv, git/curl. Als Funktion, damit sowohl
# der Installations- als auch der Update-Pfad exakt dieselbe Pruefung nutzen
# (DRY). Setzt PY_MAJOR/PY_MINOR bewusst global (kein 'local'), wie im urspr.
# Inline-Code. Bricht bei zu altem Python bzw. fehlendem venv klar ab.
# =============================================================================
ensure_base_tools() {
    if ! command -v python3 &>/dev/null; then
        warn "$(t py_not_found)"
        case "$PKG_MGR" in
            pacman) install_pkg python ;;
            *)      install_pkg python3 ;;
        esac || error "$(t py_install_failed)"
    fi

    PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
    if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
        error "$(t py_too_old "$PY_MAJOR" "$PY_MINOR")"
    fi
    ok "$(t py_found "$PY_MAJOR" "$PY_MINOR")"

    if ! python3 -m venv --help &>/dev/null; then
        warn "$(t venv_missing_try)"
        case "$PKG_MGR" in
            # '|| true', damit ein fehlgeschlagener Installationsversuch unter
            # 'set -e' NICHT hier abbricht - die verbindliche Pruefung (mit
            # handlungsleitender Meldung) folgt unmittelbar danach.
            apt) install_pkg "python3-venv" || install_pkg "python${PY_MAJOR}.${PY_MINOR}-venv" || true ;;
            *)   true ;;
        esac
    fi
    python3 -m venv --help &>/dev/null || error "$(t venv_still_missing)"
    ok "$(t venv_ok)"

    if ! command -v git &>/dev/null && ! command -v curl &>/dev/null; then
        warn "$(t git_curl_missing)"
        install_pkg curl || error "$(t curl_install_failed)"
    fi
}

# =============================================================================
# Installationsverzeichnis anlegen
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
        [[ -f "$dir/midea_ieco_ensure.py" ]] && hint=" $(t dir_hint_prev_install)"
        error "$(t err_dir_not_writable "$dir" "$hint" "$(id -un)" "$(printf '%q' "$dir")")"
    fi
    # Verzeichnis existiert noch nicht: zuerst die Elternkette anlegen (bei
    # Bedarf per sudo, aber OHNE chown), dann das Blatt SELBST anlegen.
    local parent; parent="$(dirname "$dir")"
    if [[ ! -d "$parent" ]]; then
        mkdir -p "$parent" 2>/dev/null || { info "$(t sudo_need_mkdir "$parent")"; sudo mkdir -p "$parent"; }
    fi
    # 'mkdir' OHNE -p: ein zwischenzeitlich von Dritten angelegtes Verzeichnis
    # (TOCTOU) wird NICHT als Erfolg gewertet - dann scheitert auch das folgende
    # 'sudo mkdir' (ebenfalls ohne -p) und wir uebernehmen NICHTS Fremdes.
    if mkdir "$dir" 2>/dev/null; then
        return 0
    fi
    info "$(t sudo_need_mkdir "$dir")"
    sudo mkdir "$dir"
    sudo chown "$(id -u):$(id -g)" "$dir"
}

# =============================================================================
# Projekt-Dateien besorgen / aktualisieren
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
        0) error "$(t err_zip_no_root)" ;;
        *) error "$(t err_zip_multi_root "${subdirs[*]}")" ;;
    esac
}

# Laedt das aktuelle GitHub-Archiv und legt seinen Inhalt ueber $INSTALL_DIR.
# Genutzt fuer die ZIP-Erstinstallation UND fuer das ZIP-Update (ohne Git).
# 'cp -R extracted/. INSTALL_DIR/' ueberlagert NUR die im Archiv enthaltenen
# (getrackten) Dateien; devices.json/venv/logs (und eine evtl. aus 0.1.x
# verbliebene credentials.json) sind git-ignoriert und daher nicht im Archiv -
# sie bleiben strukturell unangetastet.
# tmp_dir wird explizit am Ende entfernt (nicht nur ueber den EXIT-Trap): der
# Update-fetch-Pfad verlaesst das Skript anschliessend per 'exec', wodurch der
# Trap NICHT mehr feuert. CLEANUP_PATHS bleibt als Sicherung fuer den Fehlerpfad.
download_and_overlay_zip() {
    command -v curl &>/dev/null || error "$(t err_zip_needs_curl)"
    command -v unzip &>/dev/null || install_pkg unzip \
        || error "$(t err_zip_needs_unzip "$PKG_MGR")"
    local tmp_dir tmp_zip extracted_root
    tmp_dir="$(mktemp -d)"
    CLEANUP_PATHS+=("$tmp_dir")
    tmp_zip="$tmp_dir/midea-ieco.zip"
    curl -fsSL "$REPO_ZIP_URL" -o "$tmp_zip"
    unzip -q "$tmp_zip" -d "$tmp_dir/extract"
    extracted_root="$(resolve_extracted_root_dir "$tmp_dir/extract")"
    cp -R "$extracted_root"/. "$INSTALL_DIR"/
    rm -rf "$tmp_dir"
}

# Holt die Projektdateien nach $INSTALL_DIR. $1 = 'install' | 'update'.
#   - Git-Clone vorhanden        -> git pull --ff-only (beide Modi).
#   - kein Git-Clone + 'update'  -> ZIP-Overlay (schliesst die Update-Luecke fuer
#                                   ZIP-Installationen).
#   - kein Git-Clone + Erstlauf  -> git clone bzw. ZIP-Download.
#   - sonst                      -> bereits vorhanden, nichts zu tun.
# Ein fehlgeschlagener Pull (lokale Aenderungen an getrackten Dateien, kein Netz,
# non-fast-forward) bricht NICHT ab, meldet aber KLAR, dass NICHT aktualisiert
# wurde - kein stiller No-Op.
fetch_project_files() {
    local mode="$1"
    if command -v git &>/dev/null && [[ -d "$INSTALL_DIR/.git" ]]; then
        info "$(t fetch_git_pull)"
        # Lokale Aenderungen an getrackten Dateien (z.B. manuell editierte
        # install.sh) wuerden --ff-only scheitern lassen. Vorher pruefen und
        # verstaendlich melden, statt den Nutzer mit einer git-Fehlermeldung
        # allein zu lassen. 'git diff --quiet' -> rc 1 bei Aenderungen; unter
        # 'set -e' sicher im 'if !'-Kontext.
        if ! git -C "$INSTALL_DIR" diff --quiet 2>/dev/null; then
            warn "$(t fetch_local_changes)"
            warn "$(t fetch_tip_1)"
            warn "$(t fetch_tip_2 "$INSTALL_DIR")"
        elif git -C "$INSTALL_DIR" pull --ff-only --quiet; then
            ok "$(t fetch_updated)"
        else
            warn "$(t fetch_pull_failed)"
        fi
    elif [[ "$mode" == "update" ]]; then
        info "$(t fetch_zip_update)"
        download_and_overlay_zip
        ok "$(t fetch_updated_zip)"
    elif [[ ! -f "$INSTALL_DIR/midea_ieco_ensure.py" ]]; then
        info "$(t fetch_downloading "$INSTALL_DIR")"
        if command -v git &>/dev/null; then
            git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        elif command -v curl &>/dev/null; then
            download_and_overlay_zip
        else
            error "$(t err_no_git_curl)"
        fi
        ok "$(t fetch_provided "$INSTALL_DIR")"
    else
        ok "$(t fetch_present "$INSTALL_DIR")"
    fi
}

# =============================================================================
# Virtuelle Umgebung + Abhaengigkeiten. Als Funktion, damit Installations- und
# Update-Pfad denselben Ablauf teilen (DRY). Erwartet cwd == $INSTALL_DIR.
# Fehlt die venv (z.B. beschaedigte Installation), wird sie neu angelegt statt
# kryptisch zu scheitern.
# =============================================================================
setup_venv_and_deps() {
    if [[ -d "venv" ]]; then
        info "$(t venv_exists)"
    else
        info "$(t venv_creating)"
        python3 -m venv venv
        ok "$(t venv_created)"
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
        warn "$(t req_missing)"
        # typing_extensions explizit mitnehmen: midea-local 6.6.1 importiert es,
        # deklariert es aber NICHT als Dependency (s. requirements.txt-Kommentar).
        pip install --quiet msmart-ng midea-local typing_extensions
    fi
    ok "$(t deps_installed)"

    # Sofortige Funktionspruefung der Kern-Abhaengigkeiten. midea-local 6.6.1
    # importiert 'typing_extensions', deklariert es aber nicht - fehlt es, taucht
    # der Fehler sonst erst spaeter als roher Traceback auf. Schlaegt die Pruefung
    # fehl, wird die bekannte Luecke AUTOMATISCH geschlossen und erneut geprueft -
    # der Installer heilt sich also selbst, statt den Nutzer abzuweisen.
    if ! check_core_imports; then
        warn "$(t core_deps_retry)"
        pip install --quiet typing_extensions || true
        if ! check_core_imports; then
            error "$(t err_core_deps "$INSTALL_DIR" "$INSTALL_DIR")"
        fi
        ok "$(t core_deps_repaired)"
    else
        ok "$(t core_deps_ok)"
    fi

    # Version rein informativ anzeigen - diese Zeilen duerfen den Installer unter
    # KEINEN Umstaenden abbrechen. Zwei Fallstricke unter 'set -e -o pipefail':
    #  1) KEIN 'exit' im awk. Ein frueher Pipe-Schluss (awk beendet sich nach dem
    #     ersten Treffer) toetet den noch schreibenden 'pip show'-Prozess per
    #     SIGPIPE; die Pipeline endet dann != 0 (real beobachtet: pip 120/141) und
    #     'set -e' bricht die GESAMTE Installation lautlos ab. Ohne 'exit' liest awk
    #     die Ausgabe vollstaendig, der Producer laeuft sauber zu Ende. Es gibt je
    #     Paket genau eine 'Version:'-Zeile, das Ergebnis bleibt also eindeutig.
    #  2) '|| true' als Guard. Liefert 'pip show' selbst einen Fehler (z.B. Paket
    #     nicht gefunden), soll die Zuweisung NICHT unter 'set -e' abbrechen -
    #     stattdessen greift der ${VAR:-unbekannt}-Fallback unten.
    MSMART_VER=$(pip show msmart-ng 2>/dev/null | awk '/^Version:/{print $2}') || true
    MIDEALOCAL_VER=$(pip show midea-local 2>/dev/null | awk '/^Version:/{print $2}') || true
    info "  msmart-ng    : ${MSMART_VER:-$(t val_unknown)}"
    info "  midea-local  : ${MIDEALOCAL_VER:-$(t val_unknown)}"
}

# =============================================================================
# Wrapper-Erzeugung in BIN_DIR
# =============================================================================
# Legt EINEN ausfuehrbaren Wrapper $1 im BIN_DIR ab; $2 ist die Kommandozeile
# NACH dem Shebang (bereits fertig gequotet vom Aufrufer). Schreibt zuerst in
# eine Temp-Datei und installiert sie dann - so laesst sich bei nicht
# beschreibbarem (root-eigenem) BIN_DIR gezielt EINE Datei per sudo ablegen,
# ohne die Besitzverhaeltnisse des Verzeichnisses zu aendern. Ausgelagert, damit
# Steuerungs- UND Update-Wrapper exakt denselben, getesteten Weg nutzen (DRY).
install_bin_wrapper() {
    local name="$1" body="$2"
    local target="$BIN_DIR/$name"
    local tmp; tmp="$(mktemp)"
    CLEANUP_PATHS+=("$tmp")
    cat > "$tmp" <<EOF
#!/usr/bin/env bash
# Wrapper generated automatically by install.sh.
$body
EOF
    if [[ -w "$BIN_DIR" ]]; then
        install -m 0755 "$tmp" "$target" 2>/dev/null \
            || { cp "$tmp" "$target" && chmod 0755 "$target"; }
    else
        info "$(t sudo_need_wrapper "$BIN_DIR")"
        sudo install -m 0755 "$tmp" "$target" 2>/dev/null \
            || { sudo cp "$tmp" "$target" && sudo chmod 0755 "$target"; }
    fi
    rm -f "$tmp"
    ok "$(t wrapper_created "$target")"
}

# Marker, an dem ein bereits eingetragener PATH-Block in einer Shell-Startdatei
# wiedererkannt wird (Idempotenz) und den der Nutzer leicht wiederfindet/entfernt.
PATH_BLOCK_MARKER="# midea-ieco-managed (PATH)"

# Zieldatei fuer die PATH-Ergaenzung nach der Login-Shell des aktuellen Nutzers.
# Bewusst die INTERAKTIVE rc-Datei (bash: ~/.bashrc, zsh: ~/.zshrc), denn genau
# dort erwartet der Nutzer den Befehl im Terminal; sonst ~/.profile als
# POSIX-Fallback. Ausgelagert, damit die Auswahl ohne Seiteneffekt testbar ist.
_path_rc_file() {
    case "$(basename "${SHELL:-}")" in
        zsh)  printf '%s\n' "$HOME/.zshrc" ;;
        bash) printf '%s\n' "$HOME/.bashrc" ;;
        *)    printf '%s\n' "$HOME/.profile" ;;
    esac
}

# Haengt einen idempotenten, selbst-schuetzenden PATH-Block an die Startdatei $1.
# Der geschriebene 'case'-Guard verhindert PATH-Dubletten, falls die Datei
# mehrfach gesourct wird. $BIN_DIR wird als Literal eingesetzt - der Aufrufer
# (ensure_bin_on_path) stellt sicher, dass es nur unkritische Zeichen enthaelt,
# sodass hier keine heikle Quotierung noetig ist. Ausgelagert, damit der Effekt
# ohne TTY/Prompt testbar ist.
_write_path_block() {
    local rc="$1"
    {
        echo ""
        echo "$PATH_BLOCK_MARKER"
        echo "case \":\$PATH:\" in *\":$BIN_DIR:\"*) ;; *) export PATH=\"$BIN_DIR:\$PATH\" ;; esac"
    } >> "$rc"
}

# Sorgt moeglichst sauber und ohne Ueberraschungen dafuer, dass BIN_DIR im PATH
# landet. Reihenfolge der Faelle:
#   1. BIN_DIR schon im aktuellen PATH        -> nichts zu tun.
#   2. BIN_DIR mit heiklen Zeichen            -> keine Startdatei editieren, nur Hinweis.
#   3. Block bereits eingetragen (Marker)     -> nur erklaeren, wie er aktiv wird.
#   4. Kein TTY (nicht-interaktiv, z.B. Cron) -> nur Hinweis, keine ungefragte Aenderung.
#   5. Sonst                                  -> EINMAL nachfragen [J/n] und bei Ja anhaengen.
ensure_bin_on_path() {
    case ":$PATH:" in *":$BIN_DIR:"*) return 0 ;; esac

    if [[ ! "$BIN_DIR" =~ ^[A-Za-z0-9._/-]+$ ]]; then
        warn "$(t path_not_in_manual "$BIN_DIR" "$BIN_DIR")"
        return 0
    fi

    local rc; rc="$(_path_rc_file)"

    if [[ -f "$rc" ]] && grep -qF "$PATH_BLOCK_MARKER" "$rc"; then
        info "$(t path_already_in_rc "$BIN_DIR" "$rc" "$rc")"
        return 0
    fi

    if [ ! -t 0 ]; then
        warn "$(t path_add_to_rc_hint "$BIN_DIR" "$rc" "$BIN_DIR")"
        return 0
    fi

    local ans
    echo ""
    if ! read -r -p "$(t path_prompt_add "$BIN_DIR" "$rc")" ans; then
        echo ""
        info "$(t path_input_aborted "$rc" "$BIN_DIR")"
        return 0
    fi
    if [[ "$ans" =~ ^[nN]$ ]]; then
        info "$(t path_skipped "$rc" "$BIN_DIR")"
        return 0
    fi

    if _write_path_block "$rc"; then
        ok "$(t path_added "$BIN_DIR" "$rc")"
        info "$(t path_active_now "$rc")"
    else
        warn "$(t path_write_failed "$rc" "$BIN_DIR")"
    fi
}

# Erzeugt alle drei Wrapper: 'midea-ieco' (Steuerung), 'midea-ieco-update'
# (Aktualisierung) und 'midea-ieco-refresh-tokens' (Token-Refresh).
# $INSTALL_DIR wird EINMAL per printf %q shell-sicher
# vorgequotet (ein Pfad mit " oder $(...) wuerde sonst die Quotierung der
# erzeugten Wrapper aufbrechen bzw. beim Ausfuehren erneut als Shell-Syntax
# interpretiert). %q liefert bereits eine selbst-quotende Form.
install_all_wrappers() {
    # BIN_DIR bei Bedarf anlegen - OHNE chown. Root-eigene bin-Verzeichnisse
    # (z.B. /opt/local/bin) sind der Normalfall; wir legen Dateien hinein, statt
    # das Verzeichnis zu uebernehmen.
    if [[ ! -d "$BIN_DIR" ]]; then
        mkdir -p "$BIN_DIR" 2>/dev/null || { info "$(t sudo_need_mkdir "$BIN_DIR")"; sudo mkdir -p "$BIN_DIR"; }
    fi
    local q; q="$(printf '%q' "$INSTALL_DIR")"
    # Steuerungs-Wrapper: ruft direkt den venv-Python (kein Exec-Bit noetig).
    install_bin_wrapper "midea-ieco" \
        "exec ${q}/venv/bin/python3 ${q}/midea_ieco_ensure.py \"\$@\""
    # Update-Wrapper: ruft install.sh im Update-Modus. Bewusst 'bash <pfad>'
    # statt Direktaufruf - so unabhaengig vom Exec-Bit der install.sh.
    install_bin_wrapper "midea-ieco-update" \
        "exec bash ${q}/install.sh --update \"\$@\""
    # Token-Refresh-Wrapper: ruft direkt den venv-Python (analog midea-ieco),
    # damit `midea-ieco-refresh-tokens --all|--name X` ohne den langen
    # 'cd ... && venv/bin/python3 ...'-Pfad nutzbar ist.
    install_bin_wrapper "midea-ieco-refresh-tokens" \
        "exec ${q}/venv/bin/python3 ${q}/midea_refresh_tokens.py \"\$@\""

    ensure_bin_on_path
}

# "Bereits eingerichtet" = eine devices.json existiert (wird nur beim Onboarding
# oder von midea_refresh_tokens.py erzeugt). Grundlage fuer den konfig-sicheren
# Re-Run: ein blosser Installer-Neustart soll die vorhandene Geraetekonfiguration
# nicht ueberschreiben. Die Vorlage devices.example.json zaehlt NICHT.
is_already_configured() {
    [[ -f "$INSTALL_DIR/devices.json" ]]
}

# Weist einmalig darauf hin, falls aus einer 0.1.x-Installation noch eine
# credentials.json herumliegt: seit 0.2.0 wird sie nicht mehr benoetigt (der
# Token-Abruf laeuft ohne Cloud-Zugangsdaten). BEWUSST wird sie NICHT
# automatisch geloescht - es ist eine Nutzerdatei mit Klartext-Passwort, deren
# Entfernung eine bewusste Nutzerentscheidung bleibt; wir geben nur den Hinweis.
# Gibt immer 0 zurueck, damit ein Aufruf unter 'set -e' nie einen Ablauf stoppt.
hint_obsolete_credentials() {
    [[ -f "$INSTALL_DIR/credentials.json" ]] \
        && warn "$(t hint_obsolete_credentials "$INSTALL_DIR/credentials.json")"
    return 0
}

# Liefert eine kurze Versionsreferenz fuer die Update-Meldung: bevorzugt den
# git-Kurz-Hash, sonst die oberste "## [x.y.z]"-Version aus CHANGELOG.md
# (ZIP-Installation ohne Git), sonst "unbekannt". Rein informativ - gibt IMMER
# rc 0 und IMMER genau einen Wert aus, damit die Anzeige nie ein Update abbricht.
# Das awk nutzt bewusst KEINE gawk-only 3-arg-match-Funktion (BSD/macOS-portabel).
read_version_ref() {
    local ref=""
    if command -v git &>/dev/null && [[ -d "$INSTALL_DIR/.git" ]]; then
        ref="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null)" || ref=""
    fi
    if [[ -z "$ref" && -f "$INSTALL_DIR/CHANGELOG.md" ]]; then
        ref="$(awk '/^## \[[0-9]/{ gsub(/^## \[|\].*/, ""); print; exit }' "$INSTALL_DIR/CHANGELOG.md" 2>/dev/null)" || ref=""
    fi
    printf '%s' "${ref:-$(t val_unknown)}"
}

# =============================================================================
# Update-Modus: drei self-re-exec'te Phasen.
#
# WARUM drei Phasen? git pull bzw. der ZIP-'cp' ueberschreiben install.sh
# in-place, waehrend bash sie noch zeilenweise liest - das kann das LAUFENDE
# Skript beschaedigen. Deshalb wird der laufende Prozess vor dem Ueberschreiben
# vom Zieldateipfad entkoppelt:
#   relaunch (aus $INSTALL_DIR/install.sh): kopiert sich nach /tmp und startet
#            die naechste Phase aus dieser TEMP-Kopie.
#   fetch    (aus der Temp-Kopie): holt/aktualisiert den Code in $INSTALL_DIR
#            (darf install.sh dort jetzt gefahrlos ueberschreiben) und startet
#            die letzte Phase aus der FRISCHEN $INSTALL_DIR/install.sh.
#   apply    (aus der frischen install.sh): Abhaengigkeiten + Wrapper erneuern,
#            Version melden, fertig. Kein weiterer exec.
#
# Der Phasen-Zaehler MIDEA_IECO_UPDATE_PHASE ist zugleich der Schleifen-Guard:
# 'apply' re-exec't nie. MIDEA_IECO_RESOLVED_DIR haelt das Zielverzeichnis stabil
# (siehe Abschnitt 0). MIDEA_IECO_UPDATE_TMP reicht die /tmp-Kopie bis zur
# apply-Phase durch, die sie - als einzige nicht mehr exec'ende Phase - ueber den
# EXIT-Trap sicher entfernt (ein exec verwirft den Trap, siehe cleanup()).
# =============================================================================
run_update() {
    case "$UPDATE_PHASE" in
        "")
            # Update setzt eine echte, auf Platte liegende Installation voraus
            # (nicht curl|bash). Klarer Abbruch statt kryptischem Folgefehler.
            [[ -n "$SCRIPT_DIR" && -f "$INSTALL_DIR/install.sh" ]] \
                || error "$(t err_update_needs_install "$INSTALL_DIR")"
            local tmp; tmp="$(mktemp)"
            cp "$INSTALL_DIR/install.sh" "$tmp"
            # Aufgeloeste Sprache ueber die exec-Grenze mitgeben: ein per --lang
            # gewaehlter Wert steht sonst nur der relaunch-Phase zur Verfuegung,
            # und fetch/apply fielen auf die Locale zurueck (gemischtsprachige
            # Ausgabe). MIDEA_IECO_LANG hat in resolve_lang Vorrang vor der Locale.
            exec env MIDEA_IECO_UPDATE_PHASE=fetch \
                     MIDEA_IECO_RESOLVED_DIR="$INSTALL_DIR" \
                     MIDEA_IECO_UPDATE_TMP="$tmp" \
                     MIDEA_IECO_LANG="$LANG_CHOICE" \
                     bash "$tmp" --update
            ;;
        fetch)
            # Die relaunch-Temp-Kopie (via env uebergeben) SCHON HIER fuer das
            # Aufraeumen registrieren, nicht erst in der apply-Phase: bricht diese
            # fetch-Phase VOR dem 'exec' ab (z.B. ZIP-Update ohne Netz -> curl
            # scheitert unter 'set -e'), entfernt der EXIT-Trap die Kopie, statt
            # sie bei jedem Fehlversuch leaken zu lassen. Beim erfolgreichen 'exec'
            # wird der Trap ohnehin verworfen -> die Kopie bleibt fuer die apply-
            # Phase erhalten (die sie dann selbst raeumt).
            [[ -n "${MIDEA_IECO_UPDATE_TMP:-}" ]] && CLEANUP_PATHS+=("${MIDEA_IECO_UPDATE_TMP}")
            ensure_base_tools
            local prev_ref; prev_ref="$(read_version_ref)"
            fetch_project_files update
            exec env MIDEA_IECO_UPDATE_PHASE=apply \
                     MIDEA_IECO_RESOLVED_DIR="$INSTALL_DIR" \
                     MIDEA_IECO_UPDATE_TMP="${MIDEA_IECO_UPDATE_TMP:-}" \
                     MIDEA_IECO_PREV_REF="$prev_ref" \
                     MIDEA_IECO_LANG="$LANG_CHOICE" \
                     bash "$INSTALL_DIR/install.sh" --update
            ;;
        apply)
            # Temp-Kopie aus der relaunch-Phase am Ende sicher entfernen: diese
            # Phase exec't nicht mehr, ihr EXIT-Trap feuert also.
            [[ -n "${MIDEA_IECO_UPDATE_TMP:-}" ]] && CLEANUP_PATHS+=("${MIDEA_IECO_UPDATE_TMP}")
            cd "$INSTALL_DIR"
            setup_venv_and_deps
            install_all_wrappers
            deactivate 2>/dev/null || true
            local new_ref; new_ref="$(read_version_ref)"
            local prev_ref="${MIDEA_IECO_PREV_REF:-$(t val_unknown)}"
            echo ""
            echo -e "${GREEN}=================================================${NC}"
            echo -e "${GREEN}   $(t banner_update_done)${NC}"
            echo -e "${GREEN}=================================================${NC}"
            if [[ "$prev_ref" == "$new_ref" ]]; then
                info "$(t update_version_uptodate "$new_ref")"
            else
                info "$(t update_version_changed "$prev_ref" "$new_ref")"
            fi
            info "$(t update_see_changelog)"
            hint_obsolete_credentials
            echo ""
            exit 0
            ;;
        *)
            error "$(t err_unknown_phase "$UPDATE_PHASE")"
            ;;
    esac
}

# =============================================================================
# Betriebsmodus-Weiche: Der Update-Modus laeuft vollstaendig in run_update und
# verlaesst das Skript per exec bzw. exit - der darunterstehende
# Installations-/Onboarding-Fluss wird dann nie erreicht.
# =============================================================================
if [[ "$MODE" == "update" ]]; then
    run_update
fi

# =============================================================================
# ===  Ab hier: Installations-/Onboarding-Fluss (nur MODE=install)          ===
# =============================================================================

# 2. Grundwerkzeuge
ensure_base_tools

# 3. Installationsverzeichnis anlegen
ensure_install_dir "$INSTALL_DIR"
# BIN_DIR wird NICHT hier vorbereitet - es wird beim Wrapper-Schritt bei Bedarf
# angelegt und der Wrapper per 'install'/sudo hineingelegt, ohne dessen
# Besitzverhaeltnisse zu aendern.

# 4. Projekt-Dateien besorgen
fetch_project_files install

cd "$INSTALL_DIR"

# 5. Virtuelle Umgebung + Abhaengigkeiten
setup_venv_and_deps

# =============================================================================
# 5b. Konfig-sicherer Re-Run
# =============================================================================
# Bereits eingerichtet + kein --reconfigure -> NUR Wrapper erneuern und beenden,
# statt das interaktive Onboarding zu wiederholen und dabei die vorhandene
# devices.json zu ueberschreiben. So ist ein blosser Installer-Neustart
# (z.B. erneutes curl|bash) datensicher; zum reinen Aktualisieren dient ohnehin
# 'midea-ieco-update'.
if [[ "$RECONFIGURE" -eq 0 ]] && is_already_configured; then
    install_all_wrappers
    deactivate 2>/dev/null || true
    echo ""
    info "$(t already_configured)"
    info "$(t already_cfg_update)"
    info "$(t already_cfg_reconfigure)"
    hint_obsolete_credentials
    exit 0
fi

# --reconfigure auf bestehender Installation: vorhandene devices.json sichern,
# bevor das Onboarding sie neu schreibt (Undo-Moeglichkeit). Die .bak-Datei ist
# git-ignoriert, da sie echte Token/Key-Werte enthaelt.
if [[ "$RECONFIGURE" -eq 1 && -f devices.json ]]; then
    cp -p devices.json devices.json.bak
    ok "$(t devices_backed_up)"
fi

# =============================================================================
# 6. Hinweis: Feste IP-Adressen im Router
# =============================================================================
echo ""
echo -e "${YELLOW}=================================================${NC}"
echo -e "${YELLOW}   $(t ip_banner_title)${NC}"
echo -e "${YELLOW}=================================================${NC}"
echo ""
printf '%s\n' "$(t ip_hint)"
echo ""
read -r -p "$(t prompt_continue_setup)" CONTINUE_SETUP
if [[ "$CONTINUE_SETUP" =~ ^[nN]$ ]]; then
    info "$(t deps_done_abort)"
    exit 0
fi

# =============================================================================
# 7. Geraete im Netzwerk suchen
# =============================================================================
echo ""
info "$(t discover_searching)"
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
    ok "$(t discover_found)"
    printf "    %-15s  %s\n" "$(t col_ip)" "$(t col_device_id)"
    printf "    %-15s  %s\n" "---------------" "----------"
    while IFS=$'\t' read -r disc_ip disc_id; do
        printf "    %-15s  %s\n" "$disc_ip" "$disc_id"
    done <<< "$DISCOVERED"
    echo ""
else
    warn "$(t discover_none)"
    warn "$(t discover_manual_hint)"
fi

# =============================================================================
# 8. devices.json interaktiv anlegen (ueber python3/json fuer sichere Escapes)
# =============================================================================
echo ""
echo -e "${YELLOW}--- $(t hdr_device_config) ---${NC}"

IP_REGEX='^([0-9]{1,3}\.){3}[0-9]{1,3}$'
DEVICE_NAMES=(); DEVICE_IPS=(); DEVICE_IDS=()

# IP+ID der in Abschnitt 7 erkannten Geraete uebernehmen, damit der Nutzer die
# (langen, fehleranfaelligen) Werte nicht von Hand abtippen muss.
DISC_IPS=(); DISC_IDS=()
if [[ "$DISCOVER_RC" -eq 0 && -n "$DISCOVERED" ]]; then
    parse_discovered "$DISCOVERED"
fi

USE_DISCOVERED=""
if [[ "${#DISC_IPS[@]}" -gt 0 ]]; then
    read -r -p "$(t prompt_use_discovered "${#DISC_IPS[@]}")" ANS_AUTO
    [[ ! "$ANS_AUTO" =~ ^[nN]$ ]] && USE_DISCOVERED="yes"
fi

if [[ -n "$USE_DISCOVERED" ]]; then
    # Auto-Befuellung: IP/ID stammen aus der Discovery, nur noch Name je Geraet.
    for (( i=0; i<${#DISC_IPS[@]}; i++ )); do
        echo ""
        printf '%s\n' "$(t dev_line_auto "$((i + 1))" "${#DISC_IPS[@]}" "${DISC_IPS[i]}" "${DISC_IDS[i]}")"
        while true; do
            read -r -p "$(t prompt_dev_name)" DEV_NAME
            if reason="$(is_valid_device_name "$DEV_NAME" 2>&1)"; then
                break
            fi
            warn "$reason"
        done
        DEVICE_NAMES+=("$DEV_NAME"); DEVICE_IPS+=("${DISC_IPS[i]}"); DEVICE_IDS+=("${DISC_IDS[i]}")
    done
else
    # Manuelle Eingabe (Fallback: nichts erkannt oder bewusst abgelehnt).
    read -r -p "$(t prompt_device_count)" DEVICE_COUNT
    [[ "$DEVICE_COUNT" =~ ^[1-9][0-9]*$ ]] || error "$(t err_invalid_count "$DEVICE_COUNT")"
    for (( i=1; i<=DEVICE_COUNT; i++ )); do
        echo ""
        printf '%s\n' "$(t dev_line_manual "$i" "$DEVICE_COUNT")"
        while true; do
            read -r -p "$(t prompt_dev_name)" DEV_NAME
            if reason="$(is_valid_device_name "$DEV_NAME" 2>&1)"; then
                break
            fi
            warn "$reason"
        done

        while true; do
            read -r -p "$(t prompt_dev_ip)" DEV_IP
            [[ "$DEV_IP" =~ $IP_REGEX ]] && break
            warn "$(t err_ip_format)"
        done

        while true; do
            read -r -p "$(t prompt_dev_id)" DEV_ID
            [[ "$DEV_ID" =~ ^[0-9]+$ ]] && break
            warn "$(t err_dev_id_numeric)"
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

ok "$(t devices_written)"

# =============================================================================
# 9. Token/Key-Paare abrufen
# =============================================================================
echo ""
info "$(t tokens_fetching)"
if python3 midea_refresh_tokens.py --all; then
    ok "$(t tokens_fetched)"
else
    warn "$(t tokens_failed)"
    warn "$(t tokens_retry_cmd "$INSTALL_DIR")"
fi

# =============================================================================
# 10. Wrapper in BIN_DIR anlegen
# =============================================================================
chmod +x midea_ieco_ensure.py midea_refresh_tokens.py 2>/dev/null || true
install_all_wrappers

# =============================================================================
# 11. Schnelltest
# =============================================================================
echo ""
read -r -p "$(t prompt_test_run)" DO_TEST
if [[ "$DO_TEST" =~ ^[jJyY]$ ]]; then
    FIRST_DEVICE=$(python3 -c "import json; print(json.load(open('devices.json'))['devices'][0]['name'])")
    info "$(t test_running "$FIRST_DEVICE")"
    if python3 midea_ieco_ensure.py "$FIRST_DEVICE"; then
        ok "$(t test_ok)"
    else
        warn "$(t test_failed)"
    fi
fi

deactivate 2>/dev/null || true

# =============================================================================
# 12. Cron-Job-Vorschlag (idempotent - keine Duplikate bei erneutem Lauf)
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
echo -e "${YELLOW}--- $(t hdr_cron) ---${NC}"
echo ""
echo "$CRON_LINE_IECO"
echo "$CRON_LINE_REFRESH"
echo "$CRON_LINE_LOGROTATE"
echo ""

if command -v crontab &>/dev/null; then
    read -r -p "$(t prompt_cron_add)" DO_CRON
    if [[ "$DO_CRON" =~ ^[jJyY]$ ]]; then
        EXISTING_CRON=$(crontab -l 2>/dev/null || true)
        if echo "$EXISTING_CRON" | grep -qF "$CRON_MARKER"; then
            warn "$(t cron_already)"
        else
            { echo "$EXISTING_CRON"
              echo "$CRON_LINE_IECO"
              echo "$CRON_LINE_REFRESH"
              echo "$CRON_LINE_LOGROTATE"
            } | crontab -
            ok "$(t cron_added "$(whoami)")"
        fi
    fi
else
    warn "$(t cron_no_crontab)"
fi

# =============================================================================
# Fertig
# =============================================================================
echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   $(t banner_install_done)${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
printf '%s\n' "$(t final_summary "$INSTALL_DIR" "$BIN_DIR" "$INSTALL_DIR")"
hint_obsolete_credentials
echo ""
