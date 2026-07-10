#!/usr/bin/env bash
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

# Portable Helfer (macOS BSD stat vs. GNU stat).
mode_of() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"; }
inode_of() { stat -f '%i' "$1" 2>/dev/null || stat -c '%i' "$1"; }

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

echo ""
echo "RESULT(test_install.sh): $pass passed, $fail failed"
[ "$fail" -eq 0 ]
