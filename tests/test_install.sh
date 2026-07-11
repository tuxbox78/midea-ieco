#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# Isolierte Funktionstests fuer install.sh. Einzelne Funktionen werden aus
# install.sh extrahiert und mit gestubbten Hilfsfunktionen gesourct, damit die
# Logik ohne einen echten (interaktiven, sudo-behafteten) Installer-Lauf
# pruefbar ist. Der Installer selbst wird NIE ausgefuehrt.
#
# Dateiweite, bewusst begruendete shellcheck-Ausnahmen (VOR dem ersten Kommando,
# damit sie fuer die ganze Datei gelten):
#  SC2034: Variablen wie INSTALL_DIR/BIN_DIR/CLEANUP_PATHS werden von den per
#          'eval "$(extract_func ...)"' eingezogenen install.sh-Funktionen ueber
#          globalen Scope genutzt - fuer shellcheck (der nur die Testdatei sieht)
#          unsichtbar, daher scheinbar "unused".
#  SC2030/SC2031: PATH wird ABSICHTLICH nur innerhalb von Subshells veraendert,
#          damit Stub-Kommandos (git/pip/python3) nicht in spaetere Abschnitte
#          durchsickern - die "Modifikation geht verloren"-Info ist hier gewollt.
#  SC1090: Der PATH-Test sourct bewusst eine zur Laufzeit ERZEUGTE rc-Datei
#          (variabler Pfad), um die Wirkung des generierten Blocks zu pruefen -
#          ShellCheck kann so ein dynamisches Ziel prinzipiell nicht verfolgen.
# shellcheck disable=SC2034,SC2030,SC2031,SC1090
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$REPO/install.sh"
pass=0; fail=0

# assert RC LABEL : RC ist ein zuvor gesetzter Ergebnis-Code (0 = erfuellt).
# Aufrufmuster: 'rc=0; <bedingung> || rc=1; assert "$rc" "..."' - vermeidet
# sowohl SC2015 (A && B || C) als auch SC2319 ($? einer Bedingung).
assert() {
    if [ "$1" -eq 0 ]; then
        echo "  [PASS] $2"; pass=$((pass + 1))
    else
        echo "  [FAIL] $2"; fail=$((fail + 1))
    fi
}

# Portable Helfer (GNU/Linux vs. macOS BSD stat): GNU '-c' ZUERST versuchen.
# Reihenfolge ist entscheidend: GNU scheitert bei 'stat -f' NICHT sauber, sondern
# deutet '-f' als --file-system, gibt fuer den gueltigen Pfad einen Dateisystem-
# Block nach stdout aus UND liefert Exit != 0 - dann liefe der '||'-Zweig
# zusaetzlich und die Ausgabe waere verunreinigt (genau dieser Fall liess die
# 0600-Checks nur auf dem Linux-CI-Runner scheitern). BSD lehnt das unbekannte
# '-c' dagegen sauber mit leerer Ausgabe ab, sodass der Fallback auf '-f' greift.
mode_of() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
inode_of() { stat -c '%i' "$1" 2>/dev/null || stat -f '%i' "$1"; }

# Extrahiert eine Shell-Funktion NAME (bis zur schliessenden Klammer in Spalte 0).
extract_func() {  # $1=name $2=file
    awk -v n="$1" '$0 ~ "^"n"\\(\\) \\{" {f=1} f {print} f && /^}/ {exit}' "$2"
}

# Stubs fuer Hilfsfunktionen, die extrahierte Funktionen evtl. aufrufen.
info() { :; }
warn() { :; }
ok()   { :; }
# Wie in install.sh bricht error() ab (exit) - Funktionen, die error() rufen
# koennen, werden daher in einer Subshell getestet.
error() { echo "ERROR: $*" >&2; exit 1; }

# Mehrere extrahierte install.sh-Funktionen rufen inzwischen t() (i18n) auf.
# Katalog + Sprachwahl daher frueh bereitstellen; Standard hier Deutsch, damit
# die (deutschen) Erwartungswerte der Funktionstests unten unveraendert gelten.
# Die dedizierte i18n-Sektion am Ende setzt LANG_CHOICE fuer ihre Faelle selbst.
eval "$(extract_func resolve_lang "$INSTALL")"
eval "$(extract_func t "$INSTALL")"
LANG_CHOICE=de

# Manche extrahierten Funktionen (install_bin_wrapper, download_and_overlay_zip)
# haengen Temp-Pfade an CLEANUP_PATHS an - hier vordefinieren, damit das unter
# 'set -u' nicht scheitert.
CLEANUP_PATHS=()

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------------------
echo "== write_credentials_file (#8 atomar/0600, #5b env-Uebergabe) =="
# ---------------------------------------------------------------------------
eval "$(extract_func write_credentials_file "$INSTALL")"

MIDEA_USER='a@b.example'
MIDEA_PASS='p ä"x\y'   # Leerzeichen, Umlaut, Quote, Backslash

write_credentials_file "$WORK/credentials.json"
rc=0; [ "$(mode_of "$WORK/credentials.json")" = "600" ] || rc=1
assert "$rc" "credentials.json mit 0600 angelegt"

got_user=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["username"])' "$WORK/credentials.json")
got_pass=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["password"])' "$WORK/credentials.json")
rc=0; { [ "$got_user" = "$MIDEA_USER" ] && [ "$got_pass" = "$MIDEA_PASS" ]; } || rc=1
assert "$rc" "Sonderzeichen-Roundtrip (User+Passwort) korrekt"

# Vorbestehende 0644-Datei -> atomarer Ersatz (Inode wechselt), Ergebnis 0600.
printf 'alt' > "$WORK/c2.json"; chmod 644 "$WORK/c2.json"
ino1=$(inode_of "$WORK/c2.json")
write_credentials_file "$WORK/c2.json"
ino2=$(inode_of "$WORK/c2.json")
rc=0; { [ "$(mode_of "$WORK/c2.json")" = "600" ] && [ "$ino1" != "$ino2" ]; } || rc=1
assert "$rc" "vorbestehende 0644 -> 0600 via Inode-Wechsel (atomar)"

# Keine .tmp-Waise im Zielverzeichnis.
shopt -s nullglob dotglob
leftovers=("$WORK"/.credentials.json.*.tmp "$WORK"/.c2.json.*.tmp)
shopt -u nullglob dotglob
rc=0; [ "${#leftovers[@]}" -eq 0 ] || rc=1
assert "$rc" "keine .tmp-Waise"

# ---------------------------------------------------------------------------
echo "== shell_quote_for_cron (#4) =="
# ---------------------------------------------------------------------------
eval "$(extract_func shell_quote_for_cron "$INSTALL")"

# Einfacher Pfad: direkt via /bin/sh nutzbar.
simple="$WORK/plain"; mkdir -p "$simple"
qs="$(shell_quote_for_cron "$simple")"
got="$(sh -c "cd $qs && pwd")"
rc=0; [ "$got" = "$simple" ] || rc=1
assert "$rc" "einfacher Pfad: cd erreicht das Verzeichnis"

# Boesartiger Pfad: Leerzeichen, Single-Quote UND Prozentzeichen.
tricky="$WORK/My Pro'ject 50%"; mkdir -p "$tricky"
qt="$(shell_quote_for_cron "$tricky")"

# % muss als \% escaped sein (sonst macht cron daraus einen Zeilenumbruch).
rc=0; case "$qt" in *'\%'*) : ;; *) rc=1 ;; esac
assert "$rc" "% wird als \\% escaped"

# cron-Verarbeitung nachbilden: \% -> % (ein unescaptes % waere ein Newline,
# was unser Quoting gerade verhindert), dann das Kommandofeld an /bin/sh geben.
cron_seen="${qt//\\%/%}"
got="$(sh -c "cd $cron_seen && pwd")"
rc=0; [ "$got" = "$tricky" ] || rc=1
assert "$rc" "Space/Quote/%-Pfad: cron-Kommandofeld erreicht das Verzeichnis"

# ---------------------------------------------------------------------------
echo "== set -e error-path fix (#6) =="
# ---------------------------------------------------------------------------
# Kontrolle: ohne '|| true' bricht set -e beim zweiten fehlgeschlagenen
# install_pkg ab, BEVOR die nachfolgende Pruefung greift.
buggy_out="$(bash -c '
    set -e
    install_pkg() { return 1; }
    case apt in apt) install_pkg a || install_pkg b ;; esac
    echo REACHED' 2>/dev/null || true)"
rc=0; [ "$buggy_out" != "REACHED" ] || rc=1
assert "$rc" "Kontrolle: altes Muster bricht vor der Pruefung ab"
# Fix: mit '|| true' wird die nachfolgende Pruefung erreicht.
fixed_out="$(bash -c '
    set -e
    install_pkg() { return 1; }
    case apt in apt) install_pkg a || install_pkg b || true ;; esac
    echo REACHED' 2>/dev/null || true)"
rc=0; [ "$fixed_out" = "REACHED" ] || rc=1
assert "$rc" "Fix: '|| true' laesst die nachfolgende Pruefung greifen"

# ---------------------------------------------------------------------------
echo "== is_valid_device_name (#9) =="
# ---------------------------------------------------------------------------
eval "$(extract_func is_valid_device_name "$INSTALL")"
for good in "Wohnzimmer" "Wohn Zimmer" "Küche" "buero-2"; do
    rc=0; is_valid_device_name "$good" 2>/dev/null || rc=1
    assert "$rc" "gueltig: '$good'"
done
for bad in "" "-foo" "all"; do
    rc=0; is_valid_device_name "$bad" 2>/dev/null && rc=1
    assert "$rc" "abgelehnt: '$bad'"
done
rc=0; is_valid_device_name "$(printf 'a\036b')" 2>/dev/null && rc=1
assert "$rc" "abgelehnt: Name mit RS-Steuerzeichen (\\x1e)"
rc=0; is_valid_device_name "$(printf 'a\tb')" 2>/dev/null && rc=1
assert "$rc" "abgelehnt: Name mit Tabulator"

# ---------------------------------------------------------------------------
echo "== devices.json triplet-argv write (#9 / #8) =="
# ---------------------------------------------------------------------------
extract_py_block() {  # $1=start-regex $2=file
    awk -v re="$1" '$0 ~ re {f=1; next} f && /^PYEOF$/ {exit} f {print}' "$2"
}
PYSRC="$(extract_py_block 'DEVICE_ARGS.*PYEOF' "$INSTALL")"
DWORK="$WORK/dev"; mkdir -p "$DWORK"
( cd "$DWORK" && python3 -c "$PYSRC" \
    "Wohn Zimmer" "192.168.0.5" "12345" "Küche" "192.168.0.6" "67890" )
rc=0; [ "$(mode_of "$DWORK/devices.json")" = "600" ] || rc=1
assert "$rc" "devices.json 0600"
n0=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["devices"][0]["name"])' "$DWORK/devices.json")
id1=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["devices"][1]["id"])' "$DWORK/devices.json")
rc=0; { [ "$n0" = "Wohn Zimmer" ] && [ "$id1" = "67890" ]; } || rc=1
assert "$rc" "Tripel korrekt gepaart (Name mit Space; 2. Geraete-ID)"

# ---------------------------------------------------------------------------
echo "== ensure_install_dir: kein Besitz-Takeover (#1) =="
# ---------------------------------------------------------------------------
eval "$(extract_func ensure_install_dir "$INSTALL")"
# Fake-sudo protokolliert seine Argumente und fuehrt sie unprivilegiert aus.
SUDOBIN="$WORK/fakebin"; mkdir -p "$SUDOBIN"
SUDO_LOG="$WORK/sudo.log"
cat > "$SUDOBIN/sudo" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$SUDO_LOG"
exec "\$@"
EOF
chmod +x "$SUDOBIN/sudo"
# Fake-sudo einmalig in den PATH: die eigentlichen Aufrufe laufen dann in einer
# Subshell (fuer error()'s exit), ohne PATH dort erneut zu veraendern.
export PATH="$SUDOBIN:$PATH"

# S1: existiert + beschreibbar -> ok, kein sudo/chown.
: > "$SUDO_LOG"; d1="$WORK/exists_ok"; mkdir -p "$d1"
rc=0; ( ensure_install_dir "$d1" ) || rc=1
rc2=0; { [ "$rc" -eq 0 ] && ! grep -q . "$SUDO_LOG"; } || rc2=1
assert "$rc2" "S1 vorhandenes beschreibbares Verz.: ok, kein sudo/chown"

# S2: existiert nicht, Parent beschreibbar -> angelegt, kein sudo/chown.
: > "$SUDO_LOG"; mkdir -p "$WORK/np"; d2="$WORK/np/leaf"
rc=0; ( ensure_install_dir "$d2" ) || rc=1
rc2=0; { [ "$rc" -eq 0 ] && [ -d "$d2" ] && ! grep -q . "$SUDO_LOG"; } || rc2=1
assert "$rc2" "S2 neues Verz. bei beschreibbarem Parent: ohne sudo/chown angelegt"

# S3: existiert + NICHT beschreibbar -> Abbruch (error), KEIN chown.
: > "$SUDO_LOG"; d3="$WORK/exists_ro"; mkdir -p "$d3"; chmod 000 "$d3"
rc=0; ( ensure_install_dir "$d3" 2>/dev/null ) || rc=1
chmod 755 "$d3"
rc2=0; { [ "$rc" -ne 0 ] && ! grep -q chown "$SUDO_LOG"; } || rc2=1
assert "$rc2" "S3 vorhandenes nicht-beschreibbares Verz.: Abbruch ohne chown"

# ---------------------------------------------------------------------------
echo "== MSMART_VER/MIDEALOCAL_VER: pipefail- UND SIGPIPE-sicher (#17 + Regression) =="
# ---------------------------------------------------------------------------
extract_between() {  # $1=start-regex $2=end-regex(inclusive) $3=file
    awk -v s="$1" -v e="$2" '$0 ~ s{f=1} f{print} f && $0 ~ e{exit}' "$3"
}
# '^[[:space:]]*' toleriert die Einrueckung: die Versions-Zeilen stehen jetzt
# eingerueckt in der Funktion setup_venv_and_deps (frueher auf Spalte 0).
VERSRC="$(extract_between '^[[:space:]]*MSMART_VER=' '^[[:space:]]*MIDEALOCAL_VER=' "$INSTALL")"

PIPBIN="$WORK/pipbin"; mkdir -p "$PIPBIN"

# Kontrolle: das ALTE Muster (grep|awk) bricht unter pipefail ab, wenn kein
# 'Version:'-Feld vorhanden ist - reproduziert das urspruengliche Problem.
cat > "$PIPBIN/pip" <<'EOF'
#!/usr/bin/env bash
echo "Name: msmart-ng"
echo "Summary: x"
EOF
chmod +x "$PIPBIN/pip"
# PATH wird absichtlich nur INNERHALB dieser Subshells erweitert (Stub-'pip'
# soll nicht in spaetere Testabschnitte durchsickern).
# shellcheck disable=SC2030,SC2031
old_rc=0
# shellcheck disable=SC2030,SC2031
( PATH="$PIPBIN:$PATH"; set -o pipefail
  pip show msmart-ng 2>/dev/null | grep '^Version' | awk '{print $2}' ) || old_rc=$?
rc=0; [ "$old_rc" -ne 0 ] || rc=1
assert "$rc" "Kontrolle: altes grep|awk-Muster bricht bei fehlendem Version-Feld ab (Exit $old_rc)"

# Fix: das tatsaechlich in install.sh stehende Snippet bricht NICHT ab, und
# der ${VAR:-unbekannt}-Fallback ist erreichbar (Wert bleibt leer).
# shellcheck disable=SC2030,SC2031
out=$( ( PATH="$PIPBIN:$PATH"; set -o pipefail; eval "$VERSRC"; echo "RC=$?"; echo "V=${MSMART_VER:-LEER}" ) )
rc=0; echo "$out" | grep -q '^RC=0$' || rc=1
assert "$rc" "Fix: kein Abbruch, wenn 'Version:' fehlt"
rc=0; echo "$out" | grep -q '^V=LEER$' || rc=1
assert "$rc" "Fix: Fallback-Anzeige ('unbekannt') tatsaechlich erreichbar (Wert leer)"

# Positivfall: bei vorhandenem Feld wird die Version weiterhin korrekt extrahiert.
cat > "$PIPBIN/pip" <<'EOF'
#!/usr/bin/env bash
echo "Name: msmart-ng"
echo "Version: 2.1.3"
echo "Summary: x"
EOF
chmod +x "$PIPBIN/pip"
# shellcheck disable=SC2030,SC2031
out=$( ( PATH="$PIPBIN:$PATH"; set -o pipefail; eval "$VERSRC"; echo "V=${MSMART_VER:-LEER}" ) )
rc=0; echo "$out" | grep -q '^V=2\.1\.3$' || rc=1
assert "$rc" "Positivfall: Version wird weiterhin korrekt extrahiert"

# --- Der eigentliche Installer-Bug (real auf dem Zielsystem beobachtet) -------
# Symptom: der Installer starb lautlos direkt nach dem Abhaengigkeiten-OK, noch
# vor der ersten Rueckfrage. Ursache: 'pip show ... | awk "/Version/{...; exit}"'
# schliesst die Pipe nach dem ersten Treffer frueh; der noch schreibende
# pip/python-Prozess bekommt SIGPIPE und endet != 0 (real: 120/141). Unter
# 'set -e -o pipefail' bricht das die GESAMTE Installation ab. Der alte #17-Stub
# (drei schnelle echo) konnte das nicht ausloesen, weshalb der Bug durchrutschte.
#
# Die SIGPIPE-Ausloesung selbst ist puffer-/timing- und bash-versionsabhaengig
# (bash 3.2 macOS vs. 5.x Linux/CI) und damit als Assertion nicht portabel-
# deterministisch. Stattdessen wird der Fix zweigleisig geprueft: (a) statisch,
# dass die schuetzende Struktur vorhanden ist, und (b) funktional gegen die
# deterministische Kernwirkung (Producer endet != 0 -> Zeile darf NICHT abbrechen).

# (a) Statisch (plattformunabhaengig): Guard '|| true' vorhanden UND kein 'exit'
# im awk (das 'exit' war die eigentliche SIGPIPE-Ursache).
rc=0; printf '%s\n' "$VERSRC" | grep -q '|| true' || rc=1
assert "$rc" "install.sh: Versions-Zeilen durch '|| true' gegen Abbruch abgesichert"
rc=0; printf '%s\n' "$VERSRC" | grep -q "awk[^|]*exit" && rc=1
assert "$rc" "install.sh: awk OHNE 'exit' (kein frueher Pipe-Schluss -> kein SIGPIPE)"

# (b) Funktional: Producer gibt die Version aus und endet dann mit != 0 (wie ein
# per SIGPIPE getoeteter pip). Das ECHTE install.sh-Snippet muss sauber
# durchlaufen (Exit 0) und die bereits ausgegebene Version behalten.
cat > "$PIPBIN/pip" <<'EOF'
#!/usr/bin/env bash
echo "Name: msmart-ng"
echo "Version: 2.1.3"
exit 1
EOF
chmod +x "$PIPBIN/pip"
fix_rc=0
# shellcheck disable=SC2030,SC2031
out=$( ( PATH="$PIPBIN:$PATH"; set -e -o pipefail; eval "$VERSRC"; echo "V=${MSMART_VER:-LEER}" ) ) \
    || fix_rc=$?
rc=0; { [ "$fix_rc" -eq 0 ] && printf '%s\n' "$out" | grep -q '^V=2\.1\.3$'; } || rc=1
assert "$rc" "Fix: Snippet ueberlebt Nicht-Null-pip und behaelt die Version (Exit $fix_rc)"

# ---------------------------------------------------------------------------
echo "== Cron-Logrotate erfasst beide Logs (#16) =="
# ---------------------------------------------------------------------------
LOGROTATE_LINE="$(grep '^CRON_LINE_LOGROTATE=' "$INSTALL")"
rc=0; case "$LOGROTATE_LINE" in *'ieco.log'*'refresh.log'*) : ;; *) rc=1 ;; esac
assert "$rc" "Logrotate-Zeile enthaelt sowohl ieco.log als auch refresh.log"

# Funktional: truncate mit zwei Operanden leert tatsaechlich beide Dateien
# (belegt die Cross-Plattform-Annahme GNU/BSD truncate fuer diesen Batch-Lauf).
printf 'AAAA' > "$WORK/ieco.log"; printf 'BBBB' > "$WORK/refresh.log"
truncate -s 0 "$WORK/ieco.log" "$WORK/refresh.log"
rc=0; { [ ! -s "$WORK/ieco.log" ] && [ ! -s "$WORK/refresh.log" ]; } || rc=1
assert "$rc" "truncate -s 0 mit zwei Operanden leert beide Dateien"

# ---------------------------------------------------------------------------
echo "== install_bin_wrapper: shell-sicher gequotet, beide Wrapper (#15 + Update) =="
# ---------------------------------------------------------------------------
# Testet die ECHTE Funktion install_bin_wrapper (statt das Muster nachzubauen).
# Scharfer Testpfad: Anfuehrungszeichen, $(...)-Command-Substitution-Syntax,
# Leerzeichen - genau die Zeichenklassen, die eine manuelle "..."-Umschliessung
# gebrochen bzw. bei Ausfuehrung des Wrappers erneut als Shell-Syntax
# interpretiert haette.
eval "$(extract_func install_bin_wrapper "$INSTALL")"

MARKER="$WORK/pwned_marker"
TRICKY_DIR="$WORK/harn\"ess\$(touch $MARKER)dir"
mkdir -p "$TRICKY_DIR/venv/bin"
cat > "$TRICKY_DIR/venv/bin/python3" <<'STUB'
#!/usr/bin/env bash
echo "ARGV0=$0"
STUB
chmod +x "$TRICKY_DIR/venv/bin/python3"
: > "$TRICKY_DIR/midea_ieco_ensure.py"
: > "$TRICKY_DIR/install.sh"

WBIN="$WORK/wbin"; mkdir -p "$WBIN"
BIN_DIR="$WBIN"
Q="$(printf '%q' "$TRICKY_DIR")"

# Steuerungs-Wrapper (wie install_all_wrappers ihn baut).
install_bin_wrapper "midea-ieco" "exec ${Q}/venv/bin/python3 ${Q}/midea_ieco_ensure.py \"\$@\"" >/dev/null 2>&1

rc=0; bash -n "$WBIN/midea-ieco" 2>/dev/null || rc=1
assert "$rc" "install_bin_wrapper: erzeugter Wrapper syntaktisch valide (bash -n)"

rc=0; [ -e "$MARKER" ] && rc=1
assert "$rc" "install_bin_wrapper: kein Command-Substitution-Ausbruch beim Erzeugen"

got_argv0="$("$WBIN/midea-ieco" 2>/dev/null | sed -n 's/^ARGV0=//p')"
rc=0; [ "$got_argv0" = "$TRICKY_DIR/venv/bin/python3" ] || rc=1
assert "$rc" "install_bin_wrapper: ruft bei Ausfuehrung exakt den urspruenglichen Pfad auf"

rc=0; [ -e "$MARKER" ] && rc=1
assert "$rc" "install_bin_wrapper: kein Ausbruch beim Ausfuehren (kein Marker-File)"

# Update-Wrapper: 'bash <pfad>/install.sh --update "$@"' - muss den Modus und
# alle Argumente pfad-sicher weiterreichen.
install_bin_wrapper "midea-ieco-update" "exec bash ${Q}/install.sh --update \"\$@\"" >/dev/null 2>&1
rc=0; bash -n "$WBIN/midea-ieco-update" 2>/dev/null || rc=1
assert "$rc" "install_bin_wrapper: Update-Wrapper syntaktisch valide (bash -n)"
rc=0; grep -q -- '--update' "$WBIN/midea-ieco-update" || rc=1
assert "$rc" "install_bin_wrapper: Update-Wrapper reicht --update weiter"
rc=0; grep -qF '"$@"' "$WBIN/midea-ieco-update" || rc=1
assert "$rc" "install_bin_wrapper: Update-Wrapper reicht alle Argumente weiter (\$@)"

# ---------------------------------------------------------------------------
echo "== resolve_extracted_root_dir: ZIP-Fallback-Extraktion (L3) =="
# ---------------------------------------------------------------------------
eval "$(extract_func resolve_extracted_root_dir "$INSTALL")"

# Happy Path: genau ein Wurzelverzeichnis (GitHub-Archiv-Layout) -> dessen Pfad.
r1="$WORK/one_root"; mkdir -p "$r1/midea-ieco-main/subdir"
: > "$r1/midea-ieco-main/README.md"
got=""; rc=0; got="$(resolve_extracted_root_dir "$r1" 2>/dev/null)" || rc=1
rc2=0; { [ "$rc" -eq 0 ] && [ "$got" = "$r1/midea-ieco-main" ]; } || rc2=1
assert "$rc2" "genau ein Wurzelverzeichnis: Pfad korrekt geliefert"

# Realistisch: ein Wurzelverzeichnis PLUS lose Dateien direkt im Entpack-Ziel
# (z.B. eine Begleitdatei im ZIP) -> lose Dateien werden ignoriert.
r2="$WORK/one_root_plus_files"; mkdir -p "$r2/midea-ieco-main"
: > "$r2/loose_file.txt"
got=""; rc=0; got="$(resolve_extracted_root_dir "$r2" 2>/dev/null)" || rc=1
rc2=0; { [ "$rc" -eq 0 ] && [ "$got" = "$r2/midea-ieco-main" ]; } || rc2=1
assert "$rc2" "ein Wurzelverzeichnis + lose Dateien: lose Dateien ignoriert"

# Leeres Entpack-Ziel (0 Unterverzeichnisse) -> Abbruch via error(), kein Treffer.
# error() ruft exit auf, daher in einer Subshell isolieren (wie bei
# ensure_install_dir/S3 oben) - sonst wuerde der gesamte Testlauf abbrechen.
r3="$WORK/zero_roots"; mkdir -p "$r3"
rc=0; ( resolve_extracted_root_dir "$r3" >/dev/null 2>&1 ) || rc=1
rc2=0; [ "$rc" -ne 0 ] || rc2=1
assert "$rc2" "kein Wurzelverzeichnis: Abbruch statt stillem Leerlauf"

# Mehrere Wurzelverzeichnisse (unerwartete/veraenderte Archivstruktur) -> Abbruch.
r4="$WORK/two_roots"; mkdir -p "$r4/first" "$r4/second"
rc=0; ( resolve_extracted_root_dir "$r4" >/dev/null 2>&1 ) || rc=1
rc2=0; [ "$rc" -ne 0 ] || rc2=1
assert "$rc2" "mehrere Wurzelverzeichnisse: Abbruch statt unklarer Kopie"

# Fehlermeldung nennt bei mehreren Treffern beide Verzeichnisnamen (Diagnose).
err_msg="$(resolve_extracted_root_dir "$r4" 2>&1 >/dev/null || true)"
rc=0; { case "$err_msg" in *first*second*|*second*first*) : ;; *) rc=1 ;; esac; }
assert "$rc" "Fehlermeldung bei mehreren Treffern nennt beide Verzeichnisnamen"

# ---------------------------------------------------------------------------
echo "== typing_extensions-Dependency + check_core_imports =="
# ---------------------------------------------------------------------------
# midea-local importiert typing_extensions, deklariert es aber NICHT als
# Dependency - ohne expliziten Eintrag crasht 'python -m midealocal.cli' mit
# ModuleNotFoundError (real auf dem Zielsystem beobachtet).

# (a) Statisch: requirements.txt pinnt typing_extensions.
rc=0; grep -qE '^typing_extensions==' "$REPO/requirements.txt" || rc=1
assert "$rc" "requirements.txt pinnt typing_extensions"

# (b) Statisch: der ungepinnte install.sh-Fallback zieht typing_extensions mit.
FALLBACK_LINE="$(grep -E 'pip install --quiet msmart-ng midea-local' "$INSTALL")"
rc=0; case "$FALLBACK_LINE" in *typing_extensions*) : ;; *) rc=1 ;; esac
assert "$rc" "install.sh-Fallback (ohne requirements.txt) installiert typing_extensions"

# (c) Funktional: check_core_imports spiegelt den Exit-Code des python-Imports.
eval "$(extract_func check_core_imports "$INSTALL")"
PYOK="$WORK/pyok"; mkdir -p "$PYOK"
printf '#!/usr/bin/env bash\nexit 0\n' > "$PYOK/python3"; chmod +x "$PYOK/python3"
rc=0
# shellcheck disable=SC2030,SC2031
( PATH="$PYOK:$PATH"; check_core_imports ) || rc=1
assert "$rc" "check_core_imports: ok (0), wenn die Importe gelingen"

PYBAD="$WORK/pybad"; mkdir -p "$PYBAD"
printf '#!/usr/bin/env bash\nexit 1\n' > "$PYBAD/python3"; chmod +x "$PYBAD/python3"
rc=0
# shellcheck disable=SC2030,SC2031
( PATH="$PYBAD:$PATH"; check_core_imports ) && rc=1
assert "$rc" "check_core_imports: schlaegt fehl (!=0), wenn ein Import fehlt"

# ---------------------------------------------------------------------------
echo "== Geraete-Discovery-Snippet (IP+ID statt IP-Regex-Fehlalarm) =="
# ---------------------------------------------------------------------------
# Der alte Weg parste das INFO-Log von 'midealocal.cli discover' (nur Geraete-
# ZUSTAND, keine IP/ID) und meldete faelschlich "keine Geraete". Neu: das inline
# Python-Snippet ruft midealocal.discover.discover() und gibt "IP\tID" je Geraet
# aus (Exit 0/1/2). Hier gegen ein gestubbtes midealocal geprueft (deterministisch,
# ohne echtes Netzwerk).
DISCSRC="$(extract_py_block 'DISCOVERED=.*PYEOF' "$INSTALL")"

# Legt ein Fake-'midealocal'-Paket an, dessen discover() den Body $2 ausfuehrt.
_mk_fake_ml() {  # $1=zielverzeichnis  $2=funktionskoerper (mit Einrueckung)
    mkdir -p "$1/midealocal"
    : > "$1/midealocal/__init__.py"
    { echo "def discover(*a, **k):"; printf '%s\n' "$2"; } > "$1/midealocal/discover.py"
}

# (a) zwei Geraete -> beide IPs+IDs erscheinen, Exit 0.
FML="$WORK/ml_two"
_mk_fake_ml "$FML" '    return {1: {"ip_address": "192.168.0.186", "device_id": 153931629346858}, 2: {"ip_address": "192.168.0.185", "device_id": 152832117825892}}'
out=$(PYTHONPATH="$FML" python3 -c "$DISCSRC"); drc=$?
rc=0; { [ "$drc" -eq 0 ] && echo "$out" | grep -q '153931629346858' && echo "$out" | grep -q '152832117825892' && echo "$out" | grep -q '192.168.0.186'; } || rc=1
assert "$rc" "Discovery: zwei Geraete -> IP+ID je Geraet, Exit 0"

# (b) kein Geraet -> Exit 1, leere Ausgabe (kein Fehlalarm-Trigger).
FML0="$WORK/ml_zero"; _mk_fake_ml "$FML0" '    return {}'
out=$(PYTHONPATH="$FML0" python3 -c "$DISCSRC"); drc=$?
rc=0; { [ "$drc" -eq 1 ] && [ -z "$out" ]; } || rc=1
assert "$rc" "Discovery: kein Geraet -> Exit 1, leere Ausgabe"

# (c) discover() wirft -> Exit 2 (Snippet bricht sauber ab, kein Traceback).
FMLE="$WORK/ml_err"; _mk_fake_ml "$FMLE" '    raise RuntimeError("boom")'
out=$(PYTHONPATH="$FMLE" python3 -c "$DISCSRC" 2>/dev/null); drc=$?
rc=0; [ "$drc" -eq 2 ] || rc=1
assert "$rc" "Discovery: Fehler in discover() -> Exit 2 (kein Traceback-Abbruch)"

# ---------------------------------------------------------------------------
echo "== parse_discovered: Discovery-Zeilen -> IP/ID-Arrays (Auto-Befuellung) =="
# ---------------------------------------------------------------------------
eval "$(extract_func parse_discovered "$INSTALL")"

parse_discovered "$(printf '192.168.0.186\t153931629346858\n192.168.0.185\t152832117825892')"
rc=0; { [ "${#DISC_IPS[@]}" -eq 2 ] \
    && [ "${DISC_IPS[0]}" = "192.168.0.186" ] && [ "${DISC_IDS[0]}" = "153931629346858" ] \
    && [ "${DISC_IPS[1]}" = "192.168.0.185" ] && [ "${DISC_IDS[1]}" = "152832117825892" ]; } || rc=1
assert "$rc" "parse_discovered: zwei Zeilen -> zwei korrekte IP/ID-Paare"

parse_discovered "$(printf '192.168.0.7\t789\n\t')"
rc=0; { [ "${#DISC_IPS[@]}" -eq 1 ] && [ "${DISC_IPS[0]}" = "192.168.0.7" ] && [ "${DISC_IDS[0]}" = "789" ]; } || rc=1
assert "$rc" "parse_discovered: unvollstaendige Zeile wird uebersprungen"

# ---------------------------------------------------------------------------
echo "== read_version_ref: git-Hash / CHANGELOG-Fallback / unbekannt =="
# ---------------------------------------------------------------------------
eval "$(extract_func read_version_ref "$INSTALL")"

# (a) Git-Clone vorhanden -> Kurz-Hash bevorzugt (Stub-git in PATH + .git-Dir).
RVR="$WORK/rvr"; mkdir -p "$RVR/.git"
GBIN="$WORK/gbin"; mkdir -p "$GBIN"
cat > "$GBIN/git" <<'EOF'
#!/usr/bin/env bash
case "$*" in *"rev-parse"*) echo "deadbee"; exit 0 ;; *) exit 0 ;; esac
EOF
chmod +x "$GBIN/git"
INSTALL_DIR="$RVR"
rc=0; [ "$(PATH="$GBIN:$PATH" read_version_ref)" = "deadbee" ] || rc=1
assert "$rc" "read_version_ref: git-Kurz-Hash bevorzugt"

# (b) Kein Git-Clone -> oberste CHANGELOG-RELEASE-Version (nicht 'Unreleased').
RVR2="$WORK/rvr2"; mkdir -p "$RVR2"
printf '# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-01-01\n' > "$RVR2/CHANGELOG.md"
INSTALL_DIR="$RVR2"
rc=0; [ "$(read_version_ref)" = "1.2.3" ] || rc=1
assert "$rc" "read_version_ref: ohne git -> CHANGELOG-Release-Version (ueberspringt 'Unreleased')"

# (c) Weder git noch CHANGELOG -> 'unbekannt' (nie leer, nie Abbruch).
RVR3="$WORK/rvr3"; mkdir -p "$RVR3"
INSTALL_DIR="$RVR3"
rc=0; [ "$(read_version_ref)" = "unbekannt" ] || rc=1
assert "$rc" "read_version_ref: ohne git und ohne CHANGELOG -> 'unbekannt'"

# ---------------------------------------------------------------------------
echo "== is_already_configured: devices.json-Praesenz (konfig-sicherer Re-Run) =="
# ---------------------------------------------------------------------------
eval "$(extract_func is_already_configured "$INSTALL")"
IAC="$WORK/iac"; mkdir -p "$IAC"
INSTALL_DIR="$IAC"
rc=0; is_already_configured && rc=1
assert "$rc" "is_already_configured: ohne devices.json -> falsch (Onboarding laeuft)"
: > "$IAC/devices.json"
rc=0; is_already_configured || rc=1
assert "$rc" "is_already_configured: mit devices.json -> wahr (Onboarding wird uebersprungen)"

# ---------------------------------------------------------------------------
echo "== fetch_project_files: git-pull / dirty-skip / ZIP-Update (Luecke A) =="
# ---------------------------------------------------------------------------
eval "$(extract_func fetch_project_files "$INSTALL")"
# Netz vermeiden: download_and_overlay_zip stubben (protokolliert nur).
FPF_LOG="$WORK/fpf.log"
download_and_overlay_zip() { echo "OVERLAY" >> "$FPF_LOG"; }
GIT2LOG="$WORK/git2.log"
GBIN2="$WORK/gbin2"; mkdir -p "$GBIN2"
cat > "$GBIN2/git" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$GIT2LOG"
case "\$*" in
  *"diff --quiet"*)   exit \${FAKE_GIT_DIFF_RC:-0} ;;
  *"pull --ff-only"*) exit \${FAKE_GIT_PULL_RC:-0} ;;
  *) exit 0 ;;
esac
EOF
chmod +x "$GBIN2/git"

# (a) Git-Clone, sauber -> git pull --ff-only wird aufgerufen.
FPF="$WORK/fpf_git"; mkdir -p "$FPF/.git"
INSTALL_DIR="$FPF"; : > "$GIT2LOG"; : > "$FPF_LOG"
rc=0; ( PATH="$GBIN2:$PATH"; fetch_project_files update ) || rc=1
rc2=0; { [ "$rc" -eq 0 ] && grep -q 'pull --ff-only' "$GIT2LOG"; } || rc2=1
assert "$rc2" "fetch_project_files: Git-Clone sauber -> git pull --ff-only"

# (b) Git-Clone mit lokalen Aenderungen (diff rc 1) -> KEIN pull (kein Datenverlust).
: > "$GIT2LOG"
rc=0; ( PATH="$GBIN2:$PATH"; export FAKE_GIT_DIFF_RC=1; fetch_project_files update ) || rc=1
rc2=0; { [ "$rc" -eq 0 ] && ! grep -q 'pull --ff-only' "$GIT2LOG"; } || rc2=1
assert "$rc2" "fetch_project_files: lokale Aenderungen -> pull uebersprungen, klar gemeldet"

# (c) Kein Git-Clone + Modus 'update' -> ZIP-Overlay (schliesst die ZIP-Update-Luecke).
FPFZ="$WORK/fpf_zip"; mkdir -p "$FPFZ"
INSTALL_DIR="$FPFZ"; : > "$FPF_LOG"
rc=0; ( PATH="$GBIN2:$PATH"; fetch_project_files update ) || rc=1
rc2=0; { [ "$rc" -eq 0 ] && grep -q 'OVERLAY' "$FPF_LOG"; } || rc2=1
assert "$rc2" "fetch_project_files: kein .git + update -> ZIP-Overlay statt No-Op"

# ---------------------------------------------------------------------------
echo "== install.sh --update: End-to-End (Phasen, kein Onboarding, beide Wrapper) =="
# ---------------------------------------------------------------------------
# Vollstaendig gestubbte Umgebung: kein Netz, keine echte venv, keine Hardware.
# Beweist, dass der Update-Modus die drei Re-Exec-Phasen durchlaeuft, das
# Onboarding NIE erreicht (also devices.json nicht ueberschreibt) und beide
# Wrapper erneuert.
UPD="$WORK/upd_install"; mkdir -p "$UPD/.git" "$UPD/venv/bin"
cp "$INSTALL" "$UPD/install.sh"
: > "$UPD/midea_ieco_ensure.py"
: > "$UPD/midea_refresh_tokens.py"
printf 'msmart-ng==1\n' > "$UPD/requirements.txt"
printf '# Changelog\n\n## [9.9.9] - 2026-01-01\n' > "$UPD/CHANGELOG.md"
printf 'deactivate() { :; }\n' > "$UPD/venv/bin/activate"   # minimales Fake-venv
# Echte (secret-haltige) devices.json vorlegen: der Update-Lauf MUSS sie
# unveraendert lassen (Ziel b). Wird unten per Pruefsumme direkt verifiziert.
printf '{"devices":[{"name":"Wohnzimmer","token":"geheim","key":"geheim"}]}\n' > "$UPD/devices.json"
DJ_SUM_BEFORE="$(cksum < "$UPD/devices.json")"
UPDBIN="$WORK/upd_bin"; mkdir -p "$UPDBIN"

SBIN="$WORK/upd_stub"; mkdir -p "$SBIN"
cat > "$SBIN/git" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"diff --quiet"*)   exit 0 ;;
  *"pull --ff-only"*) exit 0 ;;
  *"rev-parse"*)      echo "newhash"; exit 0 ;;
  *) exit 0 ;;
esac
EOF
cat > "$SBIN/python3" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"version_info.major"*) echo 3 ;;
  *"version_info.minor"*) echo 12 ;;
  *"-m venv --help"*)     exit 0 ;;
  *"import midealocal.cli"*) exit 0 ;;
  *) exit 0 ;;
esac
EOF
cat > "$SBIN/pip" <<'EOF'
#!/usr/bin/env bash
case "$*" in *"show"*) echo "Version: 9.9.9" ;; *) exit 0 ;; esac
EOF
chmod +x "$SBIN/git" "$SBIN/python3" "$SBIN/pip"

UPD_OUT="$WORK/upd_out.txt"; UPD_RC=0
# stdin von /dev/null: install_all_wrappers ruft ensure_bin_on_path; ohne TTY
# nimmt das den Hinweis-Zweig (kein Prompt), sonst wuerde ein interaktiver
# Testlauf hier auf die PATH-Rueckfrage warten.
( PATH="$SBIN:$PATH" MIDEA_IECO_BIN_DIR="$UPDBIN" MIDEA_IECO_LANG=de \
  bash "$UPD/install.sh" --update < /dev/null ) > "$UPD_OUT" 2>&1 || UPD_RC=$?

rc=0; [ "$UPD_RC" -eq 0 ] || rc=1
assert "$rc" "install.sh --update: Exit 0 (RC $UPD_RC)"
rc=0; grep -q "Update abgeschlossen" "$UPD_OUT" || rc=1
assert "$rc" "install.sh --update: meldet 'Update abgeschlossen'"
rc=0; grep -qE "Anzahl der Klimaanlagen|Weiter mit der Einrichtung" "$UPD_OUT" && rc=1
assert "$rc" "install.sh --update: KEIN Onboarding erreicht (devices.json unangetastet)"
rc=0; { [ -f "$UPDBIN/midea-ieco" ] && [ -f "$UPDBIN/midea-ieco-update" ]; } || rc=1
assert "$rc" "install.sh --update: beide Wrapper (midea-ieco + midea-ieco-update) erzeugt"
rc=0; grep -q "9.9.9" "$UPD_OUT" || rc=1
assert "$rc" "install.sh --update: Versionsanzeige nutzt git-Ref/CHANGELOG"
# Ziel b, direkt statt indirekt belegt: devices.json ist byte-identisch geblieben.
rc=0; [ "$(cksum < "$UPD/devices.json")" = "$DJ_SUM_BEFORE" ] || rc=1
assert "$rc" "install.sh --update: devices.json byte-identisch erhalten (kein Overwrite)"

# ---------------------------------------------------------------------------
echo "== install.sh --update: kein Temp-Leak, wenn die fetch-Phase abbricht =="
# ---------------------------------------------------------------------------
# Regressionstest fuer den Fix: die relaunch-Temp-Kopie wird bereits in der
# fetch-Phase fuer den EXIT-Trap registriert. Szenario: ZIP-Installation (kein
# .git) + curl schlaegt fehl -> die fetch-Phase bricht VOR dem 'exec' zur
# apply-Phase ab. Ein dediziertes, leeres TMPDIR macht den Leak (die ~48-KB-
# install.sh-Kopie) sichtbar: nach dem Fehllauf muss es leer sein.
LUPD="$WORK/leak_install"; mkdir -p "$LUPD/venv/bin"   # bewusst KEIN .git
cp "$INSTALL" "$LUPD/install.sh"
: > "$LUPD/midea_ieco_ensure.py"; : > "$LUPD/midea_refresh_tokens.py"
printf 'msmart-ng==1\n' > "$LUPD/requirements.txt"
printf '# Changelog\n\n## [9.9.9] - 2026\n' > "$LUPD/CHANGELOG.md"
printf 'deactivate() { :; }\n' > "$LUPD/venv/bin/activate"

LSBIN="$WORK/leak_stub"; mkdir -p "$LSBIN"
cat > "$LSBIN/git" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$LSBIN/python3" <<'EOF'
#!/usr/bin/env bash
case "$*" in *major*) echo 3;; *minor*) echo 12;; *"venv --help"*) exit 0;; *) exit 0;; esac
EOF
# curl scheitert -> download_and_overlay_zip bricht unter 'set -e' ab.
cat > "$LSBIN/curl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$LSBIN/git" "$LSBIN/python3" "$LSBIN/curl"

LEAKTMP="$WORK/leak_tmp"; mkdir -p "$LEAKTMP"
LEAK_RC=0
( PATH="$LSBIN:$PATH" TMPDIR="$LEAKTMP" MIDEA_IECO_BIN_DIR="$WORK/leak_bin" \
  bash "$LUPD/install.sh" --update ) >/dev/null 2>&1 || LEAK_RC=$?

rc=0; [ "$LEAK_RC" -ne 0 ] || rc=1
assert "$rc" "fetch-Abbruch (curl-Fehler) beendet den Update-Lauf mit != 0 (RC $LEAK_RC)"

# Der eigentliche Leak-Check ist nur dort scharf, wo wir das mktemp-Ziel via
# TMPDIR steuern koennen: GNU mktemp (Linux/CI) beachtet $TMPDIR, BSD mktemp
# (macOS) NICHT (nur mit -t). Wo mktemp $TMPDIR ignoriert, landen die Temp-Files
# im System-Temp und ein leeres $LEAKTMP wuerde "kein Leak" nur VORTAEUSCHEN -
# dann ehrlich ueberspringen statt vakuos gruen. (Der Fix ist per Mutations-Check
# auf beiden mktemp-Varianten belegt; CI faehrt diesen Check scharf.)
_probe="$(TMPDIR="$LEAKTMP" mktemp 2>/dev/null || true)"
if [ -n "$_probe" ] && [ "${_probe#"$LEAKTMP"/}" != "$_probe" ]; then
    rm -f "$_probe"
    shopt -s nullglob dotglob
    leak_left=("$LEAKTMP"/*)
    shopt -u nullglob dotglob
    rc=0; [ "${#leak_left[@]}" -eq 0 ] || rc=1
    assert "$rc" "kein Temp-Leak nach fetch-Abbruch (TMPDIR leer, ${#leak_left[@]} Rest)"
else
    rm -f "$_probe" 2>/dev/null || true
    echo "  [SKIP] Leak-Check: mktemp beachtet \$TMPDIR hier nicht (BSD/macOS); im Linux-CI scharf."
fi

# ---------------------------------------------------------------------------
echo "== PATH-Aufnahme: _path_rc_file / _write_path_block / ensure_bin_on_path =="
# ---------------------------------------------------------------------------
eval "$(extract_func _path_rc_file "$INSTALL")"
eval "$(extract_func _write_path_block "$INSTALL")"
eval "$(extract_func ensure_bin_on_path "$INSTALL")"
eval "$(grep '^PATH_BLOCK_MARKER=' "$INSTALL")"

# (a) Zieldatei nach Login-Shell.
HOME="/h"; SHELL="/usr/bin/zsh"
rc=0; [ "$(_path_rc_file)" = "/h/.zshrc" ] || rc=1;   assert "$rc" "_path_rc_file: zsh -> ~/.zshrc"
SHELL="/bin/bash"
rc=0; [ "$(_path_rc_file)" = "/h/.bashrc" ] || rc=1;  assert "$rc" "_path_rc_file: bash -> ~/.bashrc"
SHELL="/usr/bin/dash"
rc=0; [ "$(_path_rc_file)" = "/h/.profile" ] || rc=1; assert "$rc" "_path_rc_file: sonst -> ~/.profile"

# (b) _write_path_block: nimmt BIN_DIR auf, dupliziert bei doppeltem Sourcen nicht.
BIN_DIR="/opt/local/bin"
RCF="$WORK/rc_write"; : > "$RCF"
_write_path_block "$RCF"
rc=0; grep -qF "$PATH_BLOCK_MARKER" "$RCF" || rc=1
assert "$rc" "_write_path_block: Marker geschrieben"
got="$(PATH="/usr/bin:/bin"; . "$RCF"; . "$RCF"; printf '%s' "$PATH")"
rc=0; case ":$got:" in *":/opt/local/bin:"*) : ;; *) rc=1 ;; esac
assert "$rc" "_write_path_block: BIN_DIR nach Sourcen im PATH"
occ="$(printf '%s' ":$got:" | grep -o ':/opt/local/bin:' | wc -l | tr -d ' ')"
rc=0; [ "$occ" -eq 1 ] || rc=1
assert "$rc" "_write_path_block: kein PATH-Duplikat bei doppeltem Sourcen (n=$occ)"

# (c) ensure_bin_on_path: BIN_DIR bereits im PATH -> keine rc-Datei angefasst.
HOME="$WORK/h_inpath"; mkdir -p "$HOME"; SHELL="/bin/bash"
( PATH="/opt/local/bin:/usr/bin:/bin"; ensure_bin_on_path < /dev/null ) >/dev/null 2>&1
rc=0; [ ! -e "$HOME/.bashrc" ] || rc=1
assert "$rc" "ensure_bin_on_path: BIN_DIR schon im PATH -> keine rc-Aenderung"

# (d) ensure_bin_on_path: nicht im PATH, KEIN TTY -> keine ungefragte rc-Aenderung.
HOME="$WORK/h_notty"; mkdir -p "$HOME"; SHELL="/bin/bash"
( PATH="/usr/bin:/bin"; BIN_DIR="/opt/local/bin"; ensure_bin_on_path < /dev/null ) >/dev/null 2>&1
rc=0; [ ! -e "$HOME/.bashrc" ] || rc=1
assert "$rc" "ensure_bin_on_path: ohne TTY keine ungefragte rc-Aenderung"

# (e) ensure_bin_on_path: Marker bereits vorhanden -> kein zweiter Eintrag.
HOME="$WORK/h_marked"; mkdir -p "$HOME"; SHELL="/bin/bash"
printf '%s\n' "$PATH_BLOCK_MARKER" > "$HOME/.bashrc"
( PATH="/usr/bin:/bin"; BIN_DIR="/opt/local/bin"; ensure_bin_on_path < /dev/null ) >/dev/null 2>&1
n="$(grep -cF "$PATH_BLOCK_MARKER" "$HOME/.bashrc")"
rc=0; [ "$n" -eq 1 ] || rc=1
assert "$rc" "ensure_bin_on_path: vorhandener Marker -> kein Duplikat (n=$n)"

# ---------------------------------------------------------------------------
echo "== i18n: resolve_lang Praezedenz (Flag > Env > Locale > en) =="
# ---------------------------------------------------------------------------
eval "$(extract_func resolve_lang "$INSTALL")"

rc=0; [ "$( ( unset LANG_CHOICE_ARG MIDEA_IECO_LANG LC_ALL LC_MESSAGES LANG; resolve_lang ) )" = "en" ] || rc=1
assert "$rc" "resolve_lang: ohne alles -> en (Default)"
rc=0; [ "$( ( unset LANG_CHOICE_ARG MIDEA_IECO_LANG LC_ALL LC_MESSAGES; LANG=de_DE.UTF-8; resolve_lang ) )" = "de" ] || rc=1
assert "$rc" "resolve_lang: LANG=de_DE.UTF-8 -> de"
rc=0; [ "$( ( unset LANG_CHOICE_ARG MIDEA_IECO_LANG LC_ALL LC_MESSAGES; LANG=en_GB.UTF-8; resolve_lang ) )" = "en" ] || rc=1
assert "$rc" "resolve_lang: LANG=en_GB.UTF-8 -> en"
rc=0; [ "$( ( unset LANG_CHOICE_ARG LC_ALL LC_MESSAGES LANG; MIDEA_IECO_LANG=de; resolve_lang ) )" = "de" ] || rc=1
assert "$rc" "resolve_lang: MIDEA_IECO_LANG=de -> de"
rc=0; [ "$( ( unset LC_ALL LC_MESSAGES LANG; LANG_CHOICE_ARG=de; MIDEA_IECO_LANG=en; resolve_lang ) )" = "de" ] || rc=1
assert "$rc" "resolve_lang: --lang (Flag) schlaegt Env"
rc=0; [ "$( ( unset LANG_CHOICE_ARG LC_ALL LC_MESSAGES; MIDEA_IECO_LANG=en; LANG=de_DE.UTF-8; resolve_lang ) )" = "en" ] || rc=1
assert "$rc" "resolve_lang: Env schlaegt Locale"

# ---------------------------------------------------------------------------
echo "== i18n: t()-Katalog vollstaendig + Interpolation =="
# ---------------------------------------------------------------------------
eval "$(extract_func t "$INSTALL")"

# Jeder im Skript per $(t <key>) referenzierte Schluessel MUSS in EN und DE eine
# nicht-leere Uebersetzung liefern - Schutz vor Drift und Tippfehlern.
# grep-/sed-Muster sind bewusst literal ('$(t ' als Text, keine Expansion).
# shellcheck disable=SC2016
used_keys="$(grep -oE '\$\(t [a-z][a-z0-9_]*' "$INSTALL" | sed 's/^\$(t //' | sort -u)"
rc=0
while IFS= read -r key; do
    [ -n "$key" ] || continue
    for lc in en de; do
        LANG_CHOICE="$lc"
        [ -n "$(t "$key" _a _b _c 2>/dev/null)" ] || { echo "    fehlt: '$key' ($lc)"; rc=1; }
    done
done <<< "$used_keys"
assert "$rc" "t(): alle referenzierten Schluessel in EN und DE vorhanden"

# printf-Interpolation + korrekte Sprache (dynamischer Wert nur als Argument).
LANG_CHOICE=de
rc=0; [ "$(t err_unknown_option '--foo')" = "Unbekannte Option: '--foo'. '--help' zeigt die Optionen." ] || rc=1
assert "$rc" "t(): DE-Interpolation (err_unknown_option)"
LANG_CHOICE=en
rc=0; [ "$(t err_unknown_option '--foo')" = "Unknown option: '--foo'. '--help' shows the options." ] || rc=1
assert "$rc" "t(): EN-Interpolation (err_unknown_option)"

# Sprachumschaltung wirkt (mind. ein Schluessel unterscheidet sich EN vs DE).
LANG_CHOICE=de; d_banner="$(t banner_install)"
LANG_CHOICE=en; e_banner="$(t banner_install)"
rc=0; [ "$d_banner" != "$e_banner" ] || rc=1
assert "$rc" "t(): DE- und EN-Ausgabe unterscheiden sich (banner_install)"

# Mehrzeiliger usage-Block traegt in beiden Sprachen die Kernoptionen.
LANG_CHOICE=en
rc=0; { t usage | grep -q -- '--update' && t usage | grep -q 'MIDEA_IECO_DIR'; } || rc=1
assert "$rc" "t(): EN-usage enthaelt --update und MIDEA_IECO_DIR"
LANG_CHOICE=de
rc=0; t usage | grep -q -- '--reconfigure' || rc=1
assert "$rc" "t(): DE-usage enthaelt --reconfigure"

# Regressionsschutz (Fremd-Audit Finding 1): die aufgeloeste Sprache muss ueber
# ALLE Update-Phasen getragen werden. Beide 'exec env ... --update'-Bloecke in
# run_update muessen MIDEA_IECO_LANG mitgeben - sonst fallen fetch/apply auf die
# Locale zurueck und ein per --lang gewaehlter Wert erzeugte gemischtsprachige
# Ausgabe. resolve_lang gibt MIDEA_IECO_LANG Vorrang vor der Locale.
RUN_UPDATE_SRC="$(extract_func run_update "$INSTALL")"
n_lang=$(printf '%s\n' "$RUN_UPDATE_SRC" | grep -c 'MIDEA_IECO_LANG=')
rc=0; [ "$n_lang" -eq 2 ] || rc=1
assert "$rc" "run_update: MIDEA_IECO_LANG an beide Update-exec-Phasen weitergereicht (n=$n_lang)"

# '--lang' darf ein folgendes Options-Token NICHT als Sprachwert verschlucken
# (Fremd-Audit-Nit, in beiden Runden gemeldet). '--help' beendet vor jeder
# Nebenwirkung mit Exit 0, daher als Subprozess sicher pruefbar.
help_out="$(LANG=C bash "$INSTALL" --lang --help 2>&1)"; hrc=$?
rc=0; { [ "$hrc" -eq 0 ] && printf '%s' "$help_out" | grep -q -- '--reconfigure'; } || rc=1
assert "$rc" "--lang --help zeigt die Hilfe (verschluckt --help nicht)"
help_out2="$(LANG=C bash "$INSTALL" --lang de --help 2>&1)"; hrc2=$?
rc=0; { [ "$hrc2" -eq 0 ] && printf '%s' "$help_out2" | grep -q -- '--update'; } || rc=1
assert "$rc" "--lang de --help zeigt die Hilfe (gueltiger Wert konsumiert, Option erkannt)"

echo ""
echo "RESULT(test_install.sh): $pass passed, $fail failed"
[ "$fail" -eq 0 ]
