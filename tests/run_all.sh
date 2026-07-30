#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# Komplette Testsuite: Lint (shellcheck/bash -n), Python-Syntax, Python-Unit-
# Tests und die install.sh-Funktionstests. Keine externen Abhaengigkeiten, keine
# Hardware noetig. Aufruf: bash tests/run_all.sh
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
fail=0

# Bytecode-Caches VOR dem Lauf raeumen: Python entscheidet anhand von Groesse und
# mtime-Sekunde, ob ein .pyc noch gueltig ist - eine Aenderung, die die
# Dateigroesse nicht veraendert (z.B. sys.exit(0) -> sys.exit(1)) und innerhalb
# derselben Sekunde erfolgt, kann sonst aus einem veralteten Cache bedient
# werden. Genau das hat bei einer Mutationspruefung dieser Suite kurzzeitig echte
# Fehler verdeckt.
#
# PYTHONDONTWRITEBYTECODE deckt das NICHT vollstaendig ab: py_compile (unten)
# schreibt .pyc bewusst und unabhaengig von dieser Variablen - das ist genau
# seine Aufgabe. Deshalb wird am Ende des Laufs ein zweites Mal geraeumt, damit
# ein spaeterer DIREKTER Aufruf (python3 -m unittest ..., python3 skript.py)
# nicht doch auf einem veralteten Cache aufsetzt.
export PYTHONDONTWRITEBYTECODE=1
clear_pycache() {
    rm -rf "$REPO/__pycache__" "$REPO/tests/__pycache__" "$REPO/tools/__pycache__"
}
clear_pycache

echo "### shellcheck + bash -n ###"
for f in install.sh midea_ieco_ensure.sh tests/test_install.sh tests/test_wrapper.sh tests/run_all.sh; do
    bash -n "$f" || fail=1
    shellcheck "$f" || fail=1
done

echo "### python syntax (py_compile) ###"
# tools/ ist mit drin, damit auch die Diagnose-Werkzeuge (die sonst nur von Hand
# am echten Geraet laufen) nicht mit einem Syntaxfehler ins Repo gelangen. Ihre
# IMPORTE deckt das nicht ab - py_compile prueft nur die Syntax; dafuer startet
# tests/test_conn.py sie zusaetzlich als Unterprozess.
python3 -m py_compile midea_ieco_ensure.py midea_refresh_tokens.py midea_i18n.py \
    midea_conn.py midea_apply.py tests/*.py tools/*.py || fail=1

echo "### python unit tests ###"
python3 -m unittest discover -s tests -p 'test_*.py' || fail=1

echo "### midea_ieco_ensure.sh wrapper tests ###"
# Ausgabe bewusst FANGEN statt durchzureichen: laesst der Wrappertest einen
# Nachkommen zurueck, der das Schreibende der Ausgabe offen haelt, blockiert
# genau dieses Fangen - so wie es jede CI tut, die Schrittausgaben protokolliert.
# Ohne die Zeitschranke waere das eine stille Verzoegerung statt eines Fehlers;
# genau so ist der Fall einmal in dieses Repo gelangt.
#
# Diese Zahl ist die MITTLERE von dreien und gehoert deshalb hierher, an genau
# eine Stelle: das Startbudget des Wrappertests muss darunter liegen (ein bloss
# langsamer Start soll mit der Zusicherung scheitern, die ihn benennt), der Stau
# des bekannten Defekts deutlich darueber (sonst faengt der Waechter ihn nicht).
# test_wrapper.sh bekommt den Wert und sichert die Ordnung selbst zu - beide
# Nachbarzahlen stehen dort. Gemessen: normaler Lauf rund 1 s, Defekt rund 21 s.
WRAPPER_TIME_LIMIT=8
wrapper_start=$SECONDS
wrapper_out="$(MIDEA_IECO_TEST_TIME_LIMIT="$WRAPPER_TIME_LIMIT" bash tests/test_wrapper.sh 2>&1)" || fail=1
printf '%s\n' "$wrapper_out"
wrapper_elapsed=$((SECONDS - wrapper_start))
if [ "$wrapper_elapsed" -gt "$WRAPPER_TIME_LIMIT" ]; then
    echo "FEHLER: test_wrapper.sh brauchte ${wrapper_elapsed}s (Grenze ${WRAPPER_TIME_LIMIT}s) -" \
         "haelt ein zurueckgelassener Prozess die Ausgabe offen?"
    fail=1
fi

echo "### install.sh function tests ###"
bash tests/test_install.sh || fail=1

clear_pycache
echo ""
if [ "$fail" -eq 0 ]; then echo "ALL GREEN"; else echo "FAILURES ABOVE"; fi
exit "$fail"
