#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Frank Seidel <frank@f-seidel.de>
# SPDX-License-Identifier: MIT
# Funktionstests fuer midea_ieco_ensure.sh - den Wrapper, ueber den die
# Siri-Kurzbefehle und SSH-Aufrufe laufen (beide READMEs dokumentieren ihn).
#
# WARUM es diese Datei gibt: der Wrapper ist der ZWEITE Produktionspfad fuer
# das Sicherheits-Flag --only-if-on (der erste ist die Crontab-Zeile). Genau
# hier steckte der allererste Fehler dieser Arbeit - der Wrapper verschluckte
# das Flag -, und bis hierher pruefte ihn keine einzige Zusicherung: die Suite
# sah ihn nur per 'bash -n' und shellcheck. Beides bleibt gruen, wenn man "$@"
# durch "${1:-}" ersetzt und damit jede bewusst ausgeschaltete Anlage alle 20
# Minuten wieder einschaltet.
#
# Der Wrapper wird als KOPIE in einer Sandbox ausgefuehrt (eigenes Verzeichnis,
# Fake-venv-Python) - nie gegen die echte Installation.
set -uo pipefail

# Referenzzeitpunkt der Datei - so frueh wie moeglich, damit die Zusicherung am
# Dateiende WIRKLICH die ganze Datei misst und nicht erst ab hier. Bewusst eine
# DIFFERENZ statt "SECONDS=0": SECONDS ist eine Sondervariable, die bash aus der
# Umgebung uebernimmt (gemessen: "SECONDS=1000 bash datei.sh" startet den
# Zaehler bei 1000), und ein Reset mitten in der Datei laesst jede spaetere
# Messung nur noch die Zeit SEIT dem Reset sehen.
FILE_START=$SECONDS

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$REPO/midea_ieco_ensure.sh"
pass=0; fail=0

# ---------------------------------------------------------------------------
# Die drei Zeitgroessen dieser Datei - sie haengen zusammen und muessen es auch
# bleiben. Der Laufzeitwaechter in tests/run_all.sh faengt den Defekt, den der
# 'exec'-Abschnitt unten beschreibt (ein zurueckgelassener Nachkomme haelt das
# Schreibende der Testausgabe offen), NUR wenn gilt:
#
#   Startbudget  <  Zeitschranke in run_all.sh  <  Stau, den der Defekt erzeugt
#
# Waren die Zahlen einmal falsch geordnet, war der Waechter in BEIDE Richtungen
# unbrauchbar: der wiedereingebaute Defekt staute nur rund 6 s und blieb unter
# der 8-s-Schranke (gemessen, die Suite meldete ALL GREEN), waehrend ein bloss
# LANGSAMER Start das Budget von 10 s ausschoepfte und damit ueber die Schranke
# lief - der Lauf wurde rot mit der irrefuehrenden Meldung ueber einen
# zurueckgelassenen Prozess. Deshalb wird die Ordnung unten zugesichert.
#
# Zum Startbudget: die Schleife kostet je Runde etwas mehr als die 0,1 s Schlaf
# (gemessen rund 14 % Aufschlag), 40 Runden sind also knapp 4,6 s echte Zeit.
# Zusammen mit rund 1 s fuer den Rest der Datei bleibt genug Abstand zur
# 8-s-Schranke. Beobachtet startet der Fake-Python nach 3 Runden.
# Wandzeit statt Schleifenrunden: Runden kosten je nach Last 0,10-0,13 s, die
# Umrechnung war deshalb schon einmal falsch. SECONDS ist sekundengenau, das
# effektive Budget liegt also in (N-1, N] - bei beobachteten 0,32-0,59 s
# Startzeit ist der Abstand rund sechsfach.
START_BUDGET_SECONDS=4
# Wie lange der Fake-Python nach dem Hinterlegen seiner PID schlaeft. Bei
# korrektem Code kostet das nichts: der Schlafende IST der Wrapper-Prozess und
# wird unten sofort erschlagen. Erst der Defekt macht daraus einen Stau - und
# der muss deutlich ueber der Zeitschranke liegen, sonst faellt er nicht auf.
ORPHAN_SLEEP_SECONDS=20
# Die Zeitschranke gehoert run_all.sh; von dort kommt sie als Umgebungsvariable.
# Der Standardwert gilt nur fuer den Direktaufruf dieser Datei - im Suitenlauf
# (und damit in der CI) entscheidet immer der uebergebene Wert.
OUTER_TIME_LIMIT="${MIDEA_IECO_TEST_TIME_LIMIT:-8}"

# Gleiches Muster wie in test_install.sh: 'rc' wird vorher gesetzt, damit weder
# SC2015 (A && B || C) noch SC2319 ($? einer Bedingung) noetig werden.
assert() {
    if [ "$1" -eq 0 ]; then
        echo "  [PASS] $2"; pass=$((pass + 1))
    else
        echo "  [FAIL] $2"; fail=$((fail + 1))
    fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ARGV_LOG="$WORK/argv.log"
: > "$ARGV_LOG"

# Legt eine Sandbox-"Installation" an: Kopie des echten Wrappers, ein leeres
# midea_ieco_ensure.py daneben und ein Fake-venv-Python, das jedes Argument in
# eine eigene Zeile protokolliert und einen steuerbaren Exit-Code liefert.
# Ein Argument je Zeile ist entscheidend: nur so faellt auf, wenn ein Wert mit
# Leerzeichen unterwegs zerlegt wird.
new_sandbox() {   # $1 = Name der Sandbox; setzt SB
    SB="$WORK/$1"
    mkdir -p "$SB/venv/bin"
    cp "$WRAPPER" "$SB/midea_ieco_ensure.sh"
    : > "$SB/midea_ieco_ensure.py"
    cat > "$SB/venv/bin/python3" <<EOF
#!/usr/bin/env bash
: > "$ARGV_LOG"
for a in "\$@"; do printf '%s\n' "\$a" >> "$ARGV_LOG"; done
exit "\${FAKE_PY_RC:-0}"
EOF
    chmod +x "$SB/venv/bin/python3"
}

argv_count() { wc -l < "$ARGV_LOG" | tr -d ' '; }
argv_line()  { sed -n "${1}p" "$ARGV_LOG"; }

# ---------------------------------------------------------------------------
echo "== Argumente werden VOLLSTAENDIG durchgereicht =="
# ---------------------------------------------------------------------------
new_sandbox argv

# Der Produktionsaufruf aus den Siri-Kurzbefehlen.
bash "$SB/midea_ieco_ensure.sh" all --only-if-on
rc=0; [ "$(argv_count)" -eq 3 ] || rc=1
assert "$rc" "'all --only-if-on': drei Argumente kommen an (Skript + 2), n=$(argv_count)"

rc=0; [ "$(argv_line 1)" = "$SB/midea_ieco_ensure.py" ] || rc=1
assert "$rc" "ruft das Python-Skript NEBEN dem Wrapper auf (nicht relativ zum cwd)"

rc=0; { [ "$(argv_line 2)" = "all" ] && [ "$(argv_line 3)" = "--only-if-on" ]; } || rc=1
assert "$rc" "--only-if-on erreicht Python - und zwar HINTER dem Ziel 'all'"

# Ein einzelnes Argument darf nicht zu einem leeren zweiten werden.
bash "$SB/midea_ieco_ensure.sh" Wohnzimmer
rc=0; { [ "$(argv_count)" -eq 2 ] && [ "$(argv_line 2)" = "Wohnzimmer" ]; } || rc=1
assert "$rc" "einzelner Geraetename kommt unveraendert an, n=$(argv_count)"

# Ohne Argumente darf KEIN leeres Argument entstehen: 'midea-ieco' ohne Ziel
# zeigt die Uebersicht, ein leerer String waere dagegen ein unbekannter
# Geraetename und endete mit Exit 1.
bash "$SB/midea_ieco_ensure.sh"
rc=0; [ "$(argv_count)" -eq 1 ] || rc=1
assert "$rc" "ohne Argumente wird auch keines erfunden (Uebersichts-Pfad), n=$(argv_count)"

# Namen mit Leerzeichen duerfen unterwegs nicht zerlegt werden.
bash "$SB/midea_ieco_ensure.sh" "Wohn Zimmer" --only-if-on
rc=0; { [ "$(argv_count)" -eq 3 ] && [ "$(argv_line 2)" = "Wohn Zimmer" ]; } || rc=1
assert "$rc" "Geraetename mit Leerzeichen bleibt EIN Argument"

# ---------------------------------------------------------------------------
echo "== Aufruf aus fremdem Arbeitsverzeichnis (Cron/SSH) =="
# ---------------------------------------------------------------------------
# Der Wrapper bestimmt sein Verzeichnis ueber BASH_SOURCE, nicht ueber den cwd -
# genau das braucht ein Cron- oder Kurzbefehl-Aufruf mit absolutem Pfad.
OTHER="$WORK/anderswo"; mkdir -p "$OTHER"
( cd "$OTHER" && bash "$SB/midea_ieco_ensure.sh" all --only-if-on )
rc=0; { [ "$(argv_line 1)" = "$SB/midea_ieco_ensure.py" ] && [ "$(argv_count)" -eq 3 ]; } || rc=1
assert "$rc" "aus fremdem cwd: gleicher Skriptpfad, gleiche Argumente"

# ---------------------------------------------------------------------------
echo "== Exit-Code wird durchgereicht (Cron/Monitoring) =="
# ---------------------------------------------------------------------------
# 'exec' macht den Wrapper ZUM Python-Prozess; sein Exit-Code ist damit
# strukturell der von Python. Exit 2 = mindestens ein Geraet fehlgeschlagen -
# ginge er verloren, meldete der Cron-Lauf stillen Erfolg.
new_sandbox exitcode
for want in 0 1 2; do
    ecrc=0
    FAKE_PY_RC="$want" bash "$SB/midea_ieco_ensure.sh" all || ecrc=$?
    rc=0; [ "$ecrc" -eq "$want" ] || rc=1
    assert "$rc" "Python-Exit $want kommt als Wrapper-Exit $want an (war $ecrc)"
done

# ---------------------------------------------------------------------------
echo "== venv-Guard: klare Meldung statt kryptischem exec-Fehler =="
# ---------------------------------------------------------------------------
new_sandbox novenv
rm -f "$SB/venv/bin/python3"
: > "$ARGV_LOG"
guard_rc=0
guard_err="$(bash "$SB/midea_ieco_ensure.sh" all 2>&1 >/dev/null)" || guard_rc=$?

rc=0; [ "$guard_rc" -eq 1 ] || rc=1
assert "$rc" "fehlende venv: Exit 1 (nicht 126/127 aus exec), war $guard_rc"

# Der Wortlaut wird BEWUSST nicht gepinnt (der Wrapper ist einsprachig, das darf
# sich aendern) - wohl aber, dass die Meldung handlungsleitend ist: sie muss den
# gesuchten Pfad nennen und auf install.sh verweisen.
rc=0; case "$guard_err" in *"$SB/venv/bin/python3"*) : ;; *) rc=1 ;; esac
assert "$rc" "Meldung nennt den erwarteten venv-Python-Pfad"
rc=0; case "$guard_err" in *install.sh*) : ;; *) rc=1 ;; esac
assert "$rc" "Meldung verweist auf install.sh"

rc=0; [ ! -s "$ARGV_LOG" ] || rc=1
assert "$rc" "ohne venv wird nichts ausgefuehrt"

# ---------------------------------------------------------------------------
echo "== exec: der Wrapper WIRD der Python-Prozess =="
# ---------------------------------------------------------------------------
# Der Wrapper begruendet sein 'exec' mit der Signalzustellung: ein SIGTERM aus
# Cron oder systemd (oder ein SSH-Disconnect) soll den Python-Prozess treffen,
# der die AC-Verbindung haelt - nicht eine Shell, die danebensteht und Python
# verwaist zuruecklaesst. Nachweisen laesst sich das ueber die PROZESSIDENTITAET:
# mit 'exec' traegt Python die PID des Wrappers, ohne 'exec' ist es ein
# Kindprozess mit eigener PID. Genau daraus folgen Signalzustellung UND der
# strukturell durchgereichte Exit-Code.
#
# Bis hierher liess sich das 'exec' streichen, ohne dass etwas rot wurde:
# Argumentdurchreichung und Exit-Code ueberstehen den Wegfall unveraendert.
#
# Anmerkung fuer andere Umgebungen: optimiert eine Shell den letzten Befehl
# eines Skripts von sich aus zu einem exec, bleibt dieser Test fuer korrekten
# Code gruen und faengt lediglich die Mutation nicht mehr. Gemessen mit der bash
# dieses Projekts (3.2) forkt der Aufruf ohne 'exec' nachweislich.
new_sandbox execpid
PIDFILE="$WORK/child.pid"
# Fake-Python, der seine eigene PID hinterlegt und dann wartet.
#
# Das 'exec' vor dem Schlafen ist wesentlich: der Schlafende IST danach dieser
# Prozess und traegt dessen PID, nur so beendet ihn das 'kill' weiter unten
# wirklich. Ohne exec ueberlebt der Schlaf als Kind, haelt das Schreibende der
# Testausgabe offen und laesst jeden Aufrufer haengen, der die Ausgabe faengt -
# Pipe, Kommandosubstitution, CI-Schrittprotokoll. So ist der Fall einmal in
# dieses Repo gelangt (damals gemessen: 1,0 s in eine Datei gegen 10,9 s
# gefangen, bei 10 s Schlafdauer). Der Stau folgt der Schlafdauer, deshalb legt
# ORPHAN_SLEEP_SECONDS oben fest, wie deutlich er die Zeitschranke ueberschreitet.
#
# Der Erklaertext steht bewusst HIER und nicht im Heredoc: dieses Here-Dokument
# ist unquotiert (es interpoliert PIDFILE und die Schlafdauer), eine
# Kommandosubstitution im Kommentar wuerde also beim Erzeugen ausgefuehrt.
#
# Zuerst die Ordnung der drei Zahlen zusichern - ohne sie sagt alles Weitere in
# diesem Abschnitt nichts mehr aus. Zugesichert wird die konservative Form
# (Schranke < reine Schlafdauer); der echte Stau ist um die Laufzeit der uebrigen
# Faelle laenger.
rc=0
{ [ "$START_BUDGET_SECONDS" -ge 2 ] \
  && [ "$START_BUDGET_SECONDS" -lt "$OUTER_TIME_LIMIT" ] \
  && [ "$OUTER_TIME_LIMIT" -lt "$ORPHAN_SLEEP_SECONDS" ]; } || rc=1
assert "$rc" "Zeitordnung: 2 <= Startbudget ${START_BUDGET_SECONDS}s < Schranke ${OUTER_TIME_LIMIT}s < Stau ${ORPHAN_SLEEP_SECONDS}s"

cat > "$SB/venv/bin/python3" <<EOF
#!/usr/bin/env bash
printf '%s' "\$\$" > "$PIDFILE"
exec sleep $ORPHAN_SLEEP_SECONDS
EOF
chmod +x "$SB/venv/bin/python3"
rm -f "$PIDFILE"

# Ausgabe umleiten: selbst wenn hier je wieder ein Nachkomme ueberlebt, kann er
# das Schreibende der Testausgabe nicht halten. Zusammen mit dem 'exec' oben ist
# der Stau strukturell ausgeschlossen; einzeln ist keine der beiden Haelften
# durch einen Test falsifizierbar (siehe KNOWN_GAPS).
bash "$SB/midea_ieco_ensure.sh" all --only-if-on >/dev/null 2>&1 &
WRAPPER_PID=$!

# Begrenztes Warten statt festem Schlafen: auf einer langsamen Maschine soll der
# Test nicht falsch-rot werden, und im Fehlerfall nicht ewig haengen. Das Budget
# liegt UNTER der Zeitschranke von run_all.sh (siehe Kopf): ein wirklich langsamer
# Start soll mit der Zusicherung direkt darunter scheitern, die den Fall benennt,
# nicht mit der Sammelmeldung des Laufzeitwaechters ueber einen
# zurueckgelassenen Prozess.
waited=0
wait_start=$SECONDS
while [ ! -s "$PIDFILE" ] && [ "$((SECONDS - wait_start))" -lt "$START_BUDGET_SECONDS" ]; do
    sleep 0.1
    waited=$((waited + 1))
done
waited_s=$((SECONDS - wait_start))
CHILD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"

rc=0; [ -n "$CHILD_PID" ] || rc=1
assert "$rc" "der Fake-Python startet ueberhaupt (PID-Datei nach ${waited_s}s / ${waited} Runden)"

rc=0; [ "$CHILD_PID" = "$WRAPPER_PID" ] || rc=1
assert "$rc" "Wrapper-PID ($WRAPPER_PID) IST die Python-PID ($CHILD_PID) - exec, kein Fork"

kill "$WRAPPER_PID" 2>/dev/null || true
if [ -n "$CHILD_PID" ]; then kill "$CHILD_PID" 2>/dev/null || true; fi
wait "$WRAPPER_PID" 2>/dev/null || true

# Beide Schranken sind dieselbe Zahl, und das ist Absicht: greift sie, soll
# DIESE Zusicherung zuerst sprechen (sie benennt den Fall), nicht die
# Sammelmeldung des Waechters. Der Waechter bleibt fuer den Fall zustaendig,
# in dem die Datei gar nicht zu Ende laeuft - dann wird diese Zeile nie
# erreicht. Gemessen: normaler Lauf 1-2 s, unter vierfacher Last bis 3,5 s.
#
# Die Laufzeit dieser Datei wird GEMESSEN statt geschaetzt: der Waechter in
# run_all.sh faengt einen zurueckgelassenen Prozess an derselben Schranke, und
# eine hier notierte Konstante war genau deshalb schon einmal falsch. Im
# Defektfall ist diese Zusicherung gruen (die Datei ist fertig, der Stau
# entsteht beim Aufrufer) - die Arbeitsteilung ist also gewollt.
file_elapsed=$((SECONDS - FILE_START))
rc=0; [ "$file_elapsed" -lt "$OUTER_TIME_LIMIT" ] || rc=1
assert "$rc" "Laufzeit dieser Datei (${file_elapsed}s) bleibt unter der Schranke ${OUTER_TIME_LIMIT}s"

echo ""
echo "RESULT(test_wrapper.sh): $pass passed, $fail failed"
[ "$fail" -eq 0 ]
