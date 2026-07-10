#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# Isolierte Funktionstests fuer install.sh. Einzelne Funktionen werden aus
# install.sh extrahiert und mit gestubbten Hilfsfunktionen gesourct, damit die
# Logik ohne einen echten (interaktiven, sudo-behafteten) Installer-Lauf
# pruefbar ist. Der Installer selbst wird NIE ausgefuehrt.
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
# Wie in install.sh bricht error() ab (exit) - Funktionen, die error() rufen
# koennen, werden daher in einer Subshell getestet.
error() { echo "ERROR: $*" >&2; exit 1; }

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
VERSRC="$(extract_between '^MSMART_VER=' '^MIDEALOCAL_VER=' "$INSTALL")"

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
echo "== Wrapper-Heredoc shell-sicher gequotet (#15) =="
# ---------------------------------------------------------------------------
# Scharfer Testpfad: Anfuehrungszeichen, \$(...)-Command-Substitution-Syntax,
# Leerzeichen - genau die Zeichenklassen, die die alte manuelle "..."-
# Umschliessung gebrochen bzw. bei Ausfuehrung des generierten Wrappers erneut
# als Shell-Syntax interpretiert haette.
MARKER="$WORK/pwned_marker"
TRICKY_DIR="$WORK/harn\"ess\$(touch $MARKER)dir"
mkdir -p "$TRICKY_DIR"
mkdir -p "$TRICKY_DIR/venv/bin"
cat > "$TRICKY_DIR/venv/bin/python3" <<'STUB'
#!/usr/bin/env bash
echo "ARGV0=$0"
STUB
chmod +x "$TRICKY_DIR/venv/bin/python3"
: > "$TRICKY_DIR/midea_ieco_ensure.py"

WRAPPER_OUT="$WORK/generated_wrapper"
( INSTALL_DIR="$TRICKY_DIR"
  INSTALL_DIR_Q="$(printf '%q' "$INSTALL_DIR")"
  cat > "$WRAPPER_OUT" <<EOF
#!/usr/bin/env bash
# Automatisch von install.sh erzeugter Wrapper.
exec ${INSTALL_DIR_Q}/venv/bin/python3 ${INSTALL_DIR_Q}/midea_ieco_ensure.py "\$@"
EOF
)
chmod +x "$WRAPPER_OUT"

rc=0; bash -n "$WRAPPER_OUT" 2>/dev/null || rc=1
assert "$rc" "generierter Wrapper ist syntaktisch valide (bash -n)"

rc=0; [ -e "$MARKER" ] && rc=1
assert "$rc" "kein Command-Substitution-Ausbruch beim Generieren (kein Marker-File)"

got_argv0="$("$WRAPPER_OUT" 2>/dev/null | sed -n 's/^ARGV0=//p')"
rc=0; [ "$got_argv0" = "$TRICKY_DIR/venv/bin/python3" ] || rc=1
assert "$rc" "Wrapper ruft bei Ausfuehrung exakt den urspruenglichen Pfad auf"

rc=0; [ -e "$MARKER" ] && rc=1
assert "$rc" "kein Command-Substitution-Ausbruch beim Ausfuehren (kein Marker-File)"

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

echo ""
echo "RESULT(test_install.sh): $pass passed, $fail failed"
[ "$fail" -eq 0 ]
