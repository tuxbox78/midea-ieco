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

# Bytecode-Caches ZUERST raeumen, nicht erst am Ende. Python entscheidet anhand
# von Groesse und mtime-Sekunde, ob ein .pyc noch gueltig ist - eine Aenderung,
# die die Dateigroesse nicht veraendert (z.B. sys.exit(0) -> sys.exit(1)) und
# innerhalb derselben Sekunde erfolgt, kann sonst aus einem veralteten Cache
# bedient werden. Genau das hat bei der Mutationspruefung dieser Suite kurzzeitig
# echte Fehler verdeckt. Zusaetzlich schreibt der Lauf selbst keinen Cache mehr.
export PYTHONDONTWRITEBYTECODE=1
rm -rf "$REPO/__pycache__" "$REPO/tests/__pycache__" "$REPO/tools/__pycache__"

echo "### shellcheck + bash -n ###"
for f in install.sh midea_ieco_ensure.sh tests/test_install.sh tests/run_all.sh; do
    bash -n "$f" || fail=1
    shellcheck "$f" || fail=1
done

echo "### python syntax (py_compile) ###"
# tools/ ist mit drin, damit auch die Diagnose-Werkzeuge (die nur von Hand am
# echten Geraet laufen und deshalb keine Unit-Tests haben) nicht mit einem
# Syntaxfehler ins Repo gelangen koennen.
python3 -m py_compile midea_ieco_ensure.py midea_refresh_tokens.py midea_i18n.py tests/*.py tools/*.py || fail=1

echo "### python unit tests ###"
python3 -m unittest discover -s tests -p 'test_*.py' || fail=1

echo "### install.sh function tests ###"
bash tests/test_install.sh || fail=1

echo ""
if [ "$fail" -eq 0 ]; then echo "ALL GREEN"; else echo "FAILURES ABOVE"; fi
exit "$fail"
