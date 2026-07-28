# Known test gaps

This file lists behaviour that **survives deliberate mutation** — i.e. you can break
it in the source and the whole suite still passes. It exists so the next person
(or the next session) starts from a written baseline instead of re-deriving it.

Everything here is *known and accepted for now*, not forgotten. Each entry says what
breaks, what it would cost a real user, and the cheapest way to close it.

## How this list was produced

Several independent reviews mutation-tested the suite: break one thing in the
source, run `bash tests/run_all.sh`, record whether it goes red. The most recent
independent run reported **213 mutations, 144 caught, 69 survived**. That count
comes from the review and was **not** re-derived here — do not treat it as a
measurement of the current tree.

The survivors that review ranked by user damage have since been worked through. In
the round before this one, **88 mutations were applied one at a time and each was
confirmed to turn the suite red**. No claim is made that those 88 map one-to-one
onto the 69 survivors above; they were derived from the findings, not from that
list. That round's header claimed the 88 covered "every finding except the ones
listed below" — they did not: two mutations derived from those same findings
(dropping `echo "$EXISTING_CRON"`, dropping the connection-retry pause) survived
and were not listed.

The round after that started by re-checking ten known survivors, then closed them
one phase at a time. What was **measured** at the end, rather than argued:

- the ten survivors that opened the round (foreign cron jobs, `~/.bashrc`
  truncation, the `devices.json` backup, the config-safe re-run guard, the
  connection-retry pause, two argument swaps, and `exec` in the wrapper) each turn
  the suite red now;
- six mutations of the rewritten cron language check, each caught by the fixture
  that names its case;
- all 60 adjacent-pair swaps derived from the AST (see gap 4 below);
- the suite stays green under the default locale, `de_DE.UTF-8`, `LC_ALL=C`,
  `tr_TR.UTF-8`, `MIDEA_IECO_LANG=de` and under Python 3.9, and `shellcheck` 0.9.0
  (the version CI runs) reports nothing on any of the five shell files.

The round after *that* one was a repair round: two independent reviews found that
three things added in the previous round did not hold up. What was measured this
time:

- the runtime guard for the wrapper test caught neither its own fault (6.0 s stall
  against an 8 s limit — suite green) nor a slow start cleanly (the internal budget
  of 10 s exceeded the limit, so the guard's message appeared instead of the
  assertion that names the case). Both repaired and re-measured, plus three
  mutations that move one of the three numbers, each red at the new ordering
  assertion;
- the inactive-job notice reported a crontab driving the **bin wrappers** as having
  no jobs and offered to add duplicates. Eight fixtures now pin it, and five
  mutations of the new predicate — reverting it, and dropping each of the marker
  cut, the `cd`-operand skip, the quote stripping and the basename step — each turn
  a fixture red that names the case;
- one new assertion was vacuous and now has a positive counterpart;
- five documentation claims were measured and corrected rather than argued (see the
  corrections section at the end and `CHANGELOG.md`).

None of that says the suite is complete — only that these specific things were
checked. What is known to be open is below.

To re-run the exercise:

```bash
cp -R . /tmp/mut && cd /tmp/mut && rm -rf .git venv
# edit one thing, then:
bash tests/run_all.sh
```

Use a copy. Mutating the real working tree risks committing a mutation by accident.

Five traps that have all produced wrong results here at least once:

> **Check the baseline first.** If the copy is already red, every mutation result is
> worthless. A newly reported `shellcheck` finding is enough to cause this.
>
> **Assert that the mutation was actually applied** (count the replacements). An
> edit that silently matched nothing looks exactly like a survivor.
>
> **Run the right part of the suite.** A mutation in `install.sh` cannot be caught
> by `python3 -m unittest`; a shell-only run says "survived" for reasons that have
> nothing to do with test coverage.
>
> **Stale bytecode:** an edit that keeps the file size identical
> (`sys.exit(0)` → `sys.exit(1)`) within the same mtime second can be served from a
> stale `.pyc` and look like a survivor when it is not. `tests/run_all.sh` clears the
> caches before *and* after each run for this reason; if you run `unittest` directly,
> export `PYTHONDONTWRITEBYTECODE=1` yourself.
>
> **Replace the whole thing.** A replacement that matches only the first fragment of
> a multi-line string leaves the rest standing and fakes a survivor. This happened
> with a catalogue text wrapped over four lines.

---

## Open gaps

### 1. `msmart-ng` wordings that still fall through to `VERIFY_OTHER`

The classification covers the ERROR frame, a wrong key (SHA256 digest mismatch),
silence, reset (both `Transport is closing` and a real `Connection reset by peer`)
and the connect timeout. The following wordings from `msmart-ng` 2026.7.0 remain
unclassified and therefore produce no summary hint (all verified against the pinned
version's `lan.py`):

| message | source |
|---|---|
| `Invalid data length for key handshake.` | `lan.py` |
| `Token and key must be supplied.` | `lan.py` |
| `Unexpected type: N` | `lan.py` |
| `Unknown type: N` | `lan.py` (distinct from `Unexpected type`) |
| `Protocol has not been authenticated.` | `lan.py` |
| `Invalid start of packet:` / `Invalid magic byte:` | `lan.py` |
| `Packet is too short:` | `lan.py` |
| `Packet is truncated.` | `lan.py` |
| `Unsupported packet:` | `lan.py` |
| a bare `IOError()` with no message at all | `lan.py` |

*Cost:* low — these indicate a protocol-level anomaly rather than a user-fixable
condition, and the raw message is still shown verbatim. Worth revisiting if any of
them shows up in a real bug report.

### 2. Three wordings are classified, but the classification can be wrong

These are *latent*: they cannot currently reach a user, because the code paths that
would produce them are swallowed inside `msmart-ng` (`base_device.py` catches
`ProtocolError`/`TimeoutError` in `_send_command`, so `refresh()` never propagates
them). Written down because the marker text alone does not distinguish them:

- **`digest do not match` matches three different states** in `lan.py`: the intended
  SHA256 handshake mismatch (key really is wrong), an **MD5** check against a
  hard-wired constant key (nothing to do with the device key at all), and a SHA256
  check *after* a successful handshake. All three currently report "token accepted,
  key wrong — re-run token retrieval"; for the latter two the key is demonstrably
  correct.
- **`Read cancelled.`** is raised by `msmart-ng` as a `TimeoutError` when its own
  read is cancelled. Our `isinstance(exc, TimeoutError)` branch therefore reports it
  as *our* verification cap, which it is not.

*Cheapest fix:* match the marker together with the exception type, or ask msmart-ng
upstream for distinguishable messages. Not worth it while the paths are unreachable.

### 3. `env -i` (no `HOME`) breaks two install-update tests

Pre-existing and confirmed against `a159ec7`. `git` needs `HOME` for its config, so
the `install.sh --update` end-to-end tests fail without it.

*Not a product defect* — cron and every realistic invocation set `HOME`. Listed so
nobody re-diagnoses it from scratch.

### 4. Installer messages with several placeholders are only spot-checked

For the two Python tools this was measured rather than asserted. The call sites
were derived from the AST — every `t()` call with two or more values, each
adjacent pair swapped in turn — which yields **60 mutations**. 25 of them survived
the suite; all 60 turn it red now, and the survey was re-run to confirm that. The
expectations are rendered from the catalogue via `t()`, so a reworded message
moves the assertion with it; what is pinned is the argument order.

Two limits of that measurement, so it is not read as more than it is: it covers
*adjacent* pairs (a three-way rotation is not in the 60), and it covers the two
Python modules only.

For `install.sh` only the four-value case (`dev_line_auto`, checked end-to-end
through the discovery path) and the cron lines are covered that way; the remaining
two-value installer messages are covered by the catalog completeness test only.

*Cost:* cosmetic. These strings are progress output during a supervised, interactive
run, not diagnostics someone has to act on hours later.

### 5. The cron language notice ignores standalone `LANG=`/`LC_ALL=` lines

The notice walks the crontab line by line and honours the nearest preceding
`MIDEA_IECO_LANG` assignment. A standalone `LANG=de_DE.UTF-8` line would also make
the jobs log in German — `resolve_lang` falls back to the locale — but it does not
suppress the notice.

*Cost:* a superfluous notice in an exotic setup. It suggests a line to copy and
never rewrites anything, so the worst case is one paragraph of unnecessary advice.
Deliberate: the notice recommends `MIDEA_IECO_LANG`, and matching every way a
locale can arrive would widen the check for no practical gain.

---

### 6. The cron language notice misjudges four shapes of hand-edited line

The notice asks one question: does this managed cron line set `MIDEA_IECO_LANG`?
Answering it exactly means parsing the command field the way `/bin/sh` does —
quotes, backslash escapes, command separators, and the schedule fields in front.
Two attempts at that parser each introduced defects worse than the gap (one of
them would have made the installer warn about every line it writes itself), so
the shapes below are measured and written down instead:

| line (no environment assignment above it) | notice | truth |
|---|---|---|
| `… MIDEA_IECO_LANG='   ' venv/bin/python3 …` | silent | job logs English |
| `… # midea-ieco-managed # MIDEA_IECO_LANG=de` | silent | job logs English |
| `… midea_ieco_ensure.py --note MIDEA_IECO_LANG=de all` | silent | job logs English |
| `… >> /opt/MIDEA_IECO_LANG=de.log 2>&1` | silent | job logs English |

All four need a hand-edited crontab, and the cost is a missing hint — never a
wrong action.

The reverse direction exists too, and it is narrower than this file used to say.
"A token after the command word with an *empty* value produces a superfluous
notice" holds **only when an effective assignment stands above the job**. Without
one the notice is correct, because nothing sets the language and the job really
does log in English. Measured with `… midea_ieco_ensure.py --note MIDEA_IECO_LANG=
all …`: no assignment above → notice, and the job logs English; `MIDEA_IECO_LANG=de`
above → notice, and the job logs German (that one is the superfluous case).

One shape *changed* with the "a whitespace value is not a value, and the line
wins" fix, in the direction of a new false positive — measured against that
commit's parent:

| line, with `MIDEA_IECO_LANG=de` above it | before | now | truth |
|---|---|---|---|
| `… # midea-ieco-managed # MIDEA_IECO_LANG=de` | silent | silent | job logs German |
| `… # midea-ieco-managed # MIDEA_IECO_LANG=`  | silent | **notice** | job logs German |

`/bin/sh` treats everything from the `#` as a comment, so neither trailing token
assigns anything and the outer assignment applies. The second row therefore gets
a notice it does not need. It is the price of letting a line-local assignment
override the environment, which is what shell precedence actually does; the cost
is one paragraph of unnecessary advice on a hand-edited line, and nothing is ever
rewritten.

A related, deliberate boundary: a value that is present but not one this project
understands (`MIDEA_IECO_LANG=de # x`, which cron stores verbatim, or `fr`)
counts as set. The notice reports "you never set it", not "you set it wrongly" —
second-guessing a value the user typed would nag whoever chose
`MIDEA_IECO_LANG=en` on purpose.

### 7. Where a test guards a conjunction rather than each half

- **The wrapper test's `exec sleep` and its `>/dev/null`** each remove the stall
  on their own, so neither can be killed individually. What is guarded is the
  conjunction: `tests/run_all.sh` captures the wrapper test's output the way CI
  does and fails above a time limit. That guard only works if three numbers stay
  in order — start budget 4 s < limit 8 s < the 20 s stall the fault produces — and
  they did not: the fault stalled 6.0 s, under the limit, and the suite reported
  ALL GREEN, while the start budget of 10 s sat *above* the limit so a slow start
  produced the guard's message instead of its own. The ordering is now asserted
  inside `tests/test_wrapper.sh`. That assertion has since been rewritten: it
  compared `START_BUDGET_TICKS / 10` — an integer division — so a budget of 79
  ticks stayed green ("7 s < 8 s") while 79 ticks really cost 8.6 s. The loop now
  bounds itself by wall time (`SECONDS` differences, never a reset, so a poisoned
  `SECONDS=1000` from the environment cannot skew it), the ordering assertion has
  a lower bound as well (a budget of 0 or 1 made the test flaky), and the file
  measures its own total runtime against the same limit. Measured: normal run
  1–2 s (up to 3.5 s under fourfold load), reintroduced fault 21 s caught by the
  guard, a slow start red by its own assertion. Four mutations — budget 0, 1, 8,
  and a 7 s stall — each turn the suite red.
- **The `git`/`curl`/`unzip` stubs in the end-to-end sandboxes** cannot be killed:
  every call a green run makes is already served by a stub, so removing one only
  changes what the stub does, not what escapes. To be exact about "no such calls",
  which this file used to claim: a green run makes **nine** of them (8 × `git`,
  1 × `curl`, 0 × `unzip`), all intercepted. The `unset` itself *is* killable —
  remove it, point `MIDEA_IECO_RESOLVED_DIR` at a victim directory, and the run
  creates a `venv/` and a `devices.json` there. (The file count once quoted here,
  898, is dropped: a bare offline `python3 -m venv` already produces 871 files, so
  the number never showed what it was cited for.) Whether the suite still writes
  outside its sandbox is a checkpoint someone has to run, not a permanent
  assertion; a permanent one would need a recursion guard and roughly half again
  the runtime.

### 8. What the inactive-job notice cannot see

`print_cron_missing_hint` looks at marked, active lines and splits each into tokens
the way `/bin/sh` would: single and double quotes group, a backslash escapes the
next character, `;`, `&`, `|`, `<` and `>` separate, and an unquoted `#` at the
start of a word ends the line. It then compares each token's **basename** —
`midea_ieco_ensure*` / `midea-ieco` and `midea_refresh_tokens*` /
`midea-ieco-refresh-tokens` — skipping the operand of `cd` and the target of an
output redirection. Whole-line substring matching is not an option: the marker
itself contains `midea-ieco` and so does the default install directory
`/opt/local/midea-ieco`, so it would count every managed line of every default
installation as the iECO job.

A plain whitespace split, which is what this did before, was wrong in *both*
directions: it tore quoted paths into fragments whose basename happened to match
(silence although the job was gone) and swallowed `cd /path;./midea-ieco` whole as
a supposed `cd` operand (advice to add a second line).

What that leaves, all print-only:

- an invocation under a name the check cannot know — a personal wrapper script, a
  `$VARIABLE`, an alias, `` `which midea-ieco` `` — makes the notice fire although
  the job exists. This is the remaining case in the *advice* direction, and it
  cannot be closed without executing the line;
- a token whose basename merely *starts with* `midea_ieco_ensure` — a log file
  called `midea_ieco_ensure.log` passed as a plain **argument**, say — counts as
  the iECO job and silences that half. As a redirection *target*
  (`>> …/midea_ieco_ensure.log`) it no longer does, and an install *directory* of
  that name never did (the basename of `/opt/midea_ieco_ensure/ieco.log` is
  `ieco.log`);
- the wrapper name appearing as a plain argument (`echo midea-ieco`) counts as the
  job, likewise silencing that half;
- a redirection glued inside a quoted word with no separator of its own
  (`sh -c 'foo >/opt/x/midea-ieco'`) counts the target as a job;
- a foreign line carrying our marker makes the notice fire for both tools;
- a line longer than 65536 characters is skipped untokenized (see gap 10);
- the notice is not suppressible: a user who removed the jobs but left a
  marker-bearing comment sees it on every run.

Where the check is unsure it counts a job as present — with two deliberate
exceptions, because there nothing measurably runs: what stands behind a comment
character, and the target of an output redirection. Both used to be counted as
jobs and therefore silenced the notice; both now let it speak. That is a change of
direction and it is the expensive one, so it is named rather than buried: a user
whose crontab happens to redirect an unrelated job into a file called
`midea_ieco_ensure.log` used to be silenced by accident and is now advised to add
a line. Following that advice costs two jobs against units that tolerate one local
connection — the notice therefore quotes nothing and only prints.

### 9. The installer still creates a duplicate job for an unmarked existing line

`fc51679`'s commit message says the notice makes "a duplicate cron job structurally
impossible". That is true of the notice, which only prints, and not of the
installer. The write branch decides on the marker alone (`grep -qF "$CRON_MARKER"`),
so a crontab that already runs `midea_ieco_ensure.py` from a **hand-written,
unmarked** line looks untouched to it: a fresh onboarding run appends its own
marked line on top. Measured end to end — one job in, two out, with an
"[OK] cron jobs installed" message.

*Cost:* real, not cosmetic — two jobs every 20 minutes against units that tolerate
a single local connection. *Cheapest fix:* have the write branch run the same
token check as the notice over the whole crontab, marked or not, and skip the line
whose tool is already scheduled. Not done here because it moves a decision from
"did we write this?" to "does something like this exist?", which needs its own
round of fixtures; written down rather than left to be rediscovered.

### 10. A line beyond the length guard is treated as missing

`cron_scan_tools` skips any crontab line longer than 65536 characters without
tokenizing it, because the split costs quadratic time in the line length (measured
under a UTF-8 locale: 6.2 s at 16000 characters, and the guard's own threshold
would cost roughly 100 s). Skipping is **not** the harmless direction: the job on
that line then counts as missing, which is advice to add a second one.

The threshold sits far above anything this installer can produce.
`shell_quote_for_cron` expands each `'` to `'\''` and each `%` to `\%`, and the
quoted path appears twice per line, so a line measures `136 + 2*L + 6*q + 2*p`
characters (L = path length, q = apostrophes, p = percent signs). At the Linux
`PATH_MAX` of 4096 with nothing but apostrophes that is 32904 — measured 32880 for
4093 of them. A hand-written line long enough to be skipped is possible; one this
installer wrote is not.

## Equivalent mutants (survive by design, not by omission)

Do not "fix" these by adding a test — there is nothing to observe.

- **Removing `asyncio.TimeoutError` from the `isinstance` check** in
  `classify_verify_failure`. On Python 3.11+ (the project's floor)
  `asyncio.TimeoutError is TimeoutError`, so the edit changes nothing. It is spelled
  out anyway because on older interpreters they are distinct classes and the branch
  would then silently never fire; this was found when the module became importable
  under Python 3.9 and the timeout test went red there.
- **Removing `os.fchmod(fd, 0o600)` from `_atomic_write_json`.** `tempfile.mkstemp`
  already creates the file with mode 0600, so the call cannot change the outcome.
  The 0600 assertion in `SaveConfigTests` passes either way — correctly. The call
  stays as an explicit statement of intent that does not depend on a `mkstemp`
  implementation detail.

**No longer an equivalent mutant:** adding `VERIFY_SILENT` to `_ANSWERED_CODES` used
to be one, because the old `summarize_failure_hint` only ever intersected that set.
The current version also tests `unique <= _ANSWERED_CODES`, so the edit now changes
observable behaviour and is caught. Mentioned here because an earlier analysis
listed it as equivalent, and that no longer holds.

## Deliberately not covered

- **`hint_all_cap`** was removed rather than tested: our verification cap sits above
  msmart-ng's own worst case (`authenticate` ≈ 12 s and it does not propagate
  network errors at all; the `refresh` ≈ 6 s figure is an estimate that has not been
  re-derived from the pinned source), so an all-timeout run is not expected to occur.
  `VERIFY_CAP` still classifies correctly if it ever does; there is simply no invented
  advice attached to it.
- **The generated crontab of existing installations** is never rewritten. The
  installer points out managed lines that lack `MIDEA_IECO_LANG` and prints the
  corrected ones, but the user's crontab is theirs. This is a deliberate boundary,
  not a gap.
- **`hint_mixed` also fires when the first candidate was unclassified**, e.g.
  `[other, rejected, silent]`. The head is deliberately more permissive than the
  tail: both claims of the text hold — the device did answer, and afterwards it did
  not — and what happened *before* the first answer cannot change either, whereas
  the same code in the tail would undercut the asserted ending. 64 of the 2801
  sequences up to length four take this path. Written down and pinned by a test so
  the asymmetry is not later mistaken for an oversight and tightened away, which
  would drop a correct hint.
- **The `exec` test compares process identity, not signals.** `tests/test_wrapper.sh`
  asserts that the wrapper *becomes* the Python process (same PID), which is the
  property `exec` provides and from which signal delivery follows; it does not send a
  `SIGTERM` and observe the process tree. Should a shell optimise the last command of
  a script into an `exec` by itself, the test stays green for correct code and only
  loses its grip on the mutation. Measured against this project's bash (3.2), the
  call without `exec` forks.
- **The wording of `midea_ieco_ensure.sh`'s venv error message.** The wrapper is glue
  code and, unlike the two Python tools, single-language. `tests/test_wrapper.sh`
  pins the exit code and that the message names the missing path and points at
  `install.sh` — deliberately not the phrasing, so translating it later needs no
  test change.

## Corrections to earlier versions of this file

Recorded so the same wrong statements do not get re-derived from the history:

- **Gap 8 described a whitespace split.** It did, accurately, until the tokenizer
  replaced it. The rewrite above is not a correction of a wrong statement but of a
  statement that the code outgrew — noted here because the two are easy to confuse
  when reading the history.
- **Gap 7 quoted `40×0.1 s` and a start budget in ticks.** Both are gone: the loop
  now bounds itself by wall time. The measurement that produced the old numbers was
  correct for its commit.
- **"The installer itself is NEVER executed"** (head of `tests/test_install.sh`).
  It is — as a copy in a sandbox, in the end-to-end sections of that very file, and
  once directly for `--help`. The sentence predated those sections.
- **"The scripts log in German"** (both READMEs). They are bilingual and default to
  English; the installer writes the resolved language into the cron line. The
  monitoring commands the README recommended (`grep FEHLER`, `grep Gesamtergebnis`)
  therefore never matched on an English installation — a silent monitoring failure,
  now covered by patterns for both languages.
- **"The installer never rewrites an existing crontab"** (both READMEs). True of
  the three notices, which only print; false of the installer, whose write branch
  appends three lines when the marker is absent. See gap 9.
- **"51 pip calls"** (`tests/README.md`, `CHANGELOG.md`). Measured today: 55. The
  `onbwackel` sandbox added in `33aef36` contributes four.

- The header used to read "165 mutations (111 caught, 51 survived)". Those numbers do
  not add up (111 + 51 = 162) and the three excluded equivalent mutants were missing
  from the arithmetic. Replaced with the counts above.
- **`refused` was listed as a covered classification.** The very commit that added
  that line had *removed* the word from the code, because `Connect failed.` does not
  mean a refusal. The document asserted what the fix had just eliminated.
- The table of unclassified `msmart-ng` wordings was incomplete; four entries and the
  bare `IOError()` have been added above.
- The entry about `MsmartMissingProbeTests` self-skipping named only that one test;
  `OverviewWithoutMsmartTests` in `test_ensure.py` has the same property. Both now
  have companions that run everywhere (`RefreshWithoutMsmartTests`,
  `OverviewWithoutMsmartAnywhereTests`), so the gap is closed rather than documented.
- The claim that Python 3.9 "reduces how much of the suite runs at all" was true for
  the predecessor commit only. Since the `__future__` import all modules are
  importable under Apple's system Python 3.9 and the full suite runs there.
- The commit message of `f97a025` says "228 Python (was 180)". The correct previous
  number is **166** — verified by running the suite at `f97a025^`.
- **"Both Python tools now assert the rendered message of every multi-placeholder
  call site."** They did not: 25 of 60 adjacent-pair swaps survived when that
  sentence was written. It has been replaced by what was measured, together with
  what the measurement does *not* cover (gap 4 above).
- **The header claimed the round's 88 mutations covered "every finding except the
  ones listed below".** Two mutations derived from those findings survived without
  being listed. Corrected at the top of this file.
- **`tests/README.md` listed `git` among the onboarding sandbox's PATH stubs.** That
  correction has itself gone stale and is withdrawn: `bc75850` added `git`, `curl`
  and `unzip` stubs to the onboarding sandbox, so `git` *is* one there now. What
  remains true from it is that `git` is never *called* in the onboarding runs —
  without a `.git` directory `fetch_project_files` takes another branch — and the
  measured call counts in gap 7 bear that out. `tests/README.md` now carries a
  per-sandbox table instead of a sentence.
- **"Zero `git`/`curl`/`unzip` calls" (gap 7 and `bc75850`).** A green run makes
  nine, all intercepted by stubs. Corrected in gap 7 above; the claim worth making
  is that none of them escapes the sandbox.
- **The "898 files" figure** attached to the sandbox-escape hazard does not support
  what it was cited for. A bare, offline `python3 -m venv` produces 871 files
  (Python 3.14.6; 569 under Apple's 3.9.6), so the number is not evidence that
  msmart-ng and midea-local were installed from PyPI. Dropped; the hazard itself is
  unaffected and still reproducible.
- **The runtime guard for the wrapper test did not catch its own fault.** It was
  added together with a change that shortened the stall it was meant to see, and
  the wrapper test's start budget sat above the guard's limit. Both directions were
  measured and repaired; see gap 7.
- **"A token after the command word with an empty value produces a superfluous
  notice"** (gap 6) was stated without its condition. It only holds when an
  effective assignment stands above the job; without one the notice is correct.
  Corrected in gap 6, together with the one shape that newly warns.
- The `--reconfigure` re-run had no `.bak` assertion, and the "commented-out
  environment line does not count" fixture passed for a different reason than its
  name suggested (the variable name check rejects `# MIDEA_IECO_LANG` before the
  comment guard ever matters). The case the comment guard actually protects — a
  commented-out managed *job* line — now has its own fixture.
