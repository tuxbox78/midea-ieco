#!/usr/bin/env bash
# =============================================================================
# midea_ieco_ensure.sh - Wrapper fuer midea_ieco_ensure.py
#
# Aktiviert bewusst KEIN venv, sondern ruft den venv-Python direkt auf - das
# ist in nicht-interaktiven SSH-/Cron-Kontexten deutlich robuster als
# 'source venv/bin/activate' (offiziell unterstuetzt, siehe Python-venv-Doku).
#
# Reicht ALLE Argumente unveraendert an das Python-Skript weiter ("$@"),
# insbesondere das Sicherheits-Flag --only-if-on. Ein Aufruf wie
#     midea_ieco_ensure.sh all --only-if-on
# darf NIEMALS zu '... all' verkuerzt werden - das wuerde bewusst
# ausgeschaltete Geraete ungewollt einschalten.
# =============================================================================
set -euo pipefail

# Eigenes Verzeichnis robust bestimmen (BASH_SOURCE statt $0), damit der
# Wrapper auch per absolutem Pfad aus beliebigem Arbeitsverzeichnis
# (z.B. aus Cron) funktioniert. Kein 'cd' noetig: das Python-Skript loest
# devices.json ueber Path(__file__).parent auf, also unabhaengig vom cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"

# Guard: klare, handlungsleitende Fehlermeldung statt kryptischem
# 'exec: ... No such file or directory', falls die venv fehlt.
if [[ ! -x "$PYTHON" ]]; then
    echo "FEHLER: venv-Python nicht gefunden unter $PYTHON" >&2
    echo "        Bitte zuerst install.sh ausfuehren - oder die venv manuell anlegen:" >&2
    echo "        python3 -m venv \"$SCRIPT_DIR/venv\" && \"$SCRIPT_DIR/venv/bin/pip\" install msmart-ng midea-local" >&2
    exit 1
fi

# exec: der Wrapper WIRD zum Python-Prozess. Signale (SIGTERM aus Cron/systemd,
# SSH-Disconnect) treffen so direkt Python, das die AC-Sockets haelt, und der
# Exit-Code ist strukturell der von Python (z.B. sys.exit(2) bei Geraetefehler).
exec "$PYTHON" "$SCRIPT_DIR/midea_ieco_ensure.py" "$@"
