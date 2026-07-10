#!/usr/bin/env bash
# Komplette Testsuite: Lint (shellcheck/bash -n), Python-Syntax, Python-Unit-
# Tests und die install.sh-Funktionstests. Keine externen Abhaengigkeiten, keine
# Hardware noetig. Aufruf: bash tests/run_all.sh
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
fail=0

echo "### shellcheck + bash -n ###"
for f in install.sh midea_ieco_ensure.sh tests/test_install.sh tests/run_all.sh; do
    bash -n "$f" || fail=1
    shellcheck "$f" || fail=1
done

echo "### python syntax (py_compile) ###"
python3 -m py_compile midea_ieco_ensure.py midea_refresh_tokens.py tests/*.py || fail=1

echo "### python unit tests ###"
python3 -m unittest discover -s tests -p 'test_*.py' || fail=1

echo "### install.sh function tests ###"
bash tests/test_install.sh || fail=1

rm -rf "$REPO/__pycache__" "$REPO/tests/__pycache__"
echo ""
if [ "$fail" -eq 0 ]; then echo "ALL GREEN"; else echo "FAILURES ABOVE"; fi
exit "$fail"
