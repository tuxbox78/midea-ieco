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
error() { echo "ERROR: $*" >&2; return 1; }

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

echo ""
echo "RESULT(test_install.sh): $pass passed, $fail failed"
[ "$fail" -eq 0 ]
