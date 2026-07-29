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

The round after that added a tool the earlier ones did not have: **an execution
oracle**. Generated crontab lines were run through `/bin/sh` in a stubbed sandbox
to observe which tool really ran, and the scanner's verdict was compared against
that rather than against an expectation someone wrote down. It found what review
had missed — `/bin/sh` separates words at `(` and `)` and the tokenizer did not, so
a running job written as `(midea-ieco all)` counted as missing — and it is also
where the "262 of 2200" and "60 of 2200" figures in gaps 8 and 9 come from. That
round closed three unpinned rules in the suite, fixed the parenthesis defect, kept
device tokens out of the log files, and corrected five more documentation claims
(again in the corrections section at the end).

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

**One inference this entry did not draw, and it mattered.** "`refresh()` never
propagates them" is not only a reason these markers stay unreachable — it also
meant `verify_credentials` saw *no exception at all* when a unit went quiet after
the handshake, and therefore reported the candidate as **verified** and saved its
token. The observation above was correct and written down; the consequence was not.
A later change even added a marker for `Read cancelled.` in the belief that it could
reach a user, which this very entry already ruled out. Both are fixed: the marker is
gone again, and `verify_credentials` now requires `device.online` after `refresh()`
(`msmart-ng` sets it to `len(responses) > 0`), which is the only observable trace a
swallowed error leaves. Recorded here because the cost was not the missing knowledge
— it was having the knowledge and not following it through.

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

### 6. The cron language notice: four misjudged shapes, and the wrappers it never sees

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

**The notice does not see the wrappers at all**, and that is the widest shape of
this gap rather than a fifth row in the table above. It matches the two script
names literally (`*midea_ieco_ensure.py*`, `*midea_refresh_tokens.py*`), so a
crontab driving the `midea-ieco` / `midea-ieco-refresh-tokens` commands — or the
documented `midea_ieco_ensure.sh` — is passed over even when nothing sets the
language, and those jobs then log in English without anyone saying so. Measured
for both tool sides: the bin wrapper, the `.sh` wrapper, and the refresh wrapper
each stay silent, while the installer's own line is reported correctly.

This is the asymmetry worth naming: the *inactive-job* notice next door has been
wrapper-aware since it started comparing basenames, so the same crontab is
visible to one check and invisible to the other. It stays that way on purpose.
The two checks answer different questions, and their error directions are not
comparable — a missing language hint costs nothing, whereas the *other* check
reporting a running job as missing costs two jobs against units that tolerate one
local connection. Note that the safe direction differs between the two: over
there, counting more shapes as present is the cautious move, while here it only
decides whether one more paragraph of advice gets printed.
Two assertions in `tests/test_install.sh` pin the current state — one for the bin
wrapper, one for the `.sh` wrapper, each with a mutation that turns only it red —
so whoever widens the detection here trips over them and updates this entry with
it.

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
- **The tokenizer's closing parenthesis** has no kill of its own, and this time
  the reason was measured rather than assumed: after `)` the sh grammar requires a
  separator anyway — `(cd /tmp)echo x` is a syntax error — so the case that would
  distinguish the rule from the sub-split cannot occur in a line that runs. Over
  2262 cases (the 2200-line fuzz corpus and the 62 calibration fixtures) the
  variant without `)` returns identical verdicts. The rule stays because `)` *is*
  a shell metacharacter and the split then holds independently of what the
  sub-split does later. Its sibling, the **opening** parenthesis, does have a kill:
  glued to `cd` it turns the token into `(cd`, the cd-operand skip stops working,
  and the install directory behind it counts as a call. Same shape as the `<`
  branch two entries above — with the difference that `<` has a reachable
  divergence and `)` does not.
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
next character, `;`, `&`, `|`, `<`, `>`, `(` and `)` separate, and an unquoted `#`
at the start of a word ends the line. It then compares each token's **basename** —
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
- an install directory that carries a **separator right after** the path component
  `midea-ieco` makes the logrotate line count as the iECO job: the sub-split turns
  the separator into a space, so `…/midea-ieco 2/ieco.log` yields a fragment whose
  basename is `midea-ieco`. Measured: `/opt/midea-ieco 2`, `/opt/midea-ieco alt`
  and `/opt/midea-ieco;x` all do this, and they did so **before** the parenthesis
  support as well — a plain space is enough. What the parentheses added is the
  enclosing form `/opt/(midea-ieco)/inst`, and that one is the price for fixing the
  expensive direction (`sh -c '(midea-ieco)'`, a running job, used to count as
  missing). What decides it is whether the split leaves a fragment that *ends* in
  `midea-ieco`, so a separator earlier in the path does not do it —
  `/opt/a(b)/midea-ieco`, `/opt/a b/midea-ieco`, `/opt/x;midea-ieco` and
  `/opt/mein midea-ieco/inst` were all measured as unaffected;
- a foreign line carrying our marker makes the notice fire for both tools;
- a line longer than 65536 characters is skipped untokenized (see gap 10);
- the notice is not suppressible: a user who removed the jobs but left a
  marker-bearing comment sees it on every run.

Three more of these were found by fuzzing against an execution oracle, and they
deserve their own paragraph because — unlike the exotica above — they fire on the
**default install directory**, where the basename is `midea-ieco`. All three
silence a half of the notice that should have spoken, so a genuinely deleted iECO
job goes unmentioned — which is the very situation the notice exists for (measured
against the current code, each alongside the logrotate-only counter-check that
still reports both jobs missing):

- **A `cd` inside a quoted sub-command.** The sub-split re-splits `sh -c '…'` but
  carries none of the scanner's context, so it has no `cd` rule:
  `sh -c 'cd /opt/local/midea-ieco && venv/bin/python3 midea_refresh_tokens.py --all'`
  counts the directory as the iECO job.
- **`cd` with an option.** Only the *first* token after `cd` is skipped, so
  `cd -P /opt/local/midea-ieco` skips `-P` and then scans the directory.
- **An assignment line that carries the marker.** `FOO=/opt/local/midea-ieco` is
  not a job at all — cron treats it as an environment assignment — but it is a
  marked, non-comment line, so its value is tokenized like a command field. The
  same holds for a value pointing into the default *bin* directory
  (`/opt/local/bin/midea-ieco`), which is where the wrapper of that name actually
  lives.

None of the three is fixed here. The first two would each push the check further
into re-implementing shell semantics, which this file's history shows to be where
the defects come from; the third would mean teaching the scanner cron's own
grammar for assignment lines. All three point the same, cheap way — silence about
a job that is not running — which is why they are recorded instead.

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

Two things that were easy to assume otherwise, both checked in the code:

- **Nothing else catches it either.** Both notices are gated on the marker
  internally — the language check skips any line that lacks it, and the
  inactive-job check returns early when no line in the crontab carries one — so an
  unmarked pre-existing line is invisible at *both* call sites, the onboarding one
  and the re-run one. The re-run does not compensate for the write branch with a
  warning; it simply stays quiet too.
- **The same blind spot has a mirror image on the notice side, and it points the
  expensive way.** When a tool runs only from an unmarked line while some *other*
  line carries the marker, the gate opens, the unmarked line is skipped, and the
  notice reports the job as missing — advice to add a line for a job that is
  already running, which is the one direction this project cannot afford. Fuzzing
  against an execution oracle put 262 of 2200 generated cases in this class. It is
  the same root cause as this gap, seen from the other end, and closing the write
  branch as sketched above would not close this one.

### 10. A line beyond the length guard is treated as missing

`cron_scan_tools` skips any crontab line longer than 65536 characters without
tokenizing it, because the split costs quadratic time in the line length (measured
under a UTF-8 locale: 6.2 s at 16000 characters, and the guard's own threshold
would cost roughly 100 s). Skipping is **not** the harmless direction: the job on
that line then counts as missing, which is advice to add a second one.

The threshold sits far above anything this installer can produce.
`shell_quote_for_cron` expands each `'` to `'\''` and each `%` to `\%`, and the
quoted path appears twice per line, so a line measures `153 + 2*L + 6*q + 2*p`
characters (L = path length, q = apostrophes, p = percent signs). At the Linux
`PATH_MAX` of 4096 with nothing but apostrophes that is 32921 — measured 32897 for
4093 of them. A hand-written line long enough to be skipped is possible; one this
installer wrote is not.

That formula describes the **`@reboot` catch-up** line, which is the longest of the
four since it was added; the iECO line starts from 136 (measured 32880 at the same
path, which is the figure earlier versions of this file quoted), the weekly refresh
line from 128 and the logrotate line from 71. Using the longest is what makes it an
upper bound, but the constant is not a property of "a managed line" in general —
and it moves whenever a managed line is added or reworded, as it just did.

Two further things are true of the guard and are easier to write down than to
rediscover:

- **What counts as "longer than 65536" depends on the locale.** `${#line}` counts
  characters under a UTF-8 locale and bytes under `LC_ALL=C`, so a line of 32868
  umlauts — 65736 bytes — is tokenized under `de_DE.UTF-8` and skipped under
  `LC_ALL=C` (measured, both). The skip is the expensive direction, so of the two
  it is the C locale that misjudges such a line. Reaching it takes a hand-written
  line of more than 32768 multi-byte characters, which is why it is recorded rather
  than fixed: pinning the guard to bytes would change what the threshold means for
  every ordinary line, in exchange for a shape this installer cannot produce.
- **The assertions cover the guard only up to 8146 characters.** A fixture in the
  installer's own line shape (path twice) pins that a line of that length is still
  tokenized, and a second one pins that a line past the threshold counts as
  missing. Between 8146 and 65535 the number can be moved without any assertion
  noticing — measured by setting it to 20000, which leaves the suite green. That
  gap is deliberate: a fixture at the worst case above (32921) would cost a
  multiple of the whole suite's runtime, because the split is quadratic. The
  mutation that removes the guard entirely already takes 98 s on the
  65627-character fixture under a UTF-8 locale.

### 11. What the hex redaction does and does not cover

Every argument that reaches a message catalogue runs through `redact_hex`
(`midea_i18n.make_translator`), so any run of 32 or more hex characters is
replaced by `[hex:<length>]` before it can reach `ieco.log` or `refresh.log`.
The guard sits in the translator rather than at each individual call site
precisely so that a newly added catalogue message is covered without anyone
having to remember it.

The catalogue is one of three channels that carry a guard — the other two are
Python's `logging` and the traceback path, both added after an independent
review found them open. That review, and a second one after it, also turned up
channels that have **no** guard; they are in the table too, because a list that
only shows what is covered reads as a promise. What follows is the state as
measured. The two rows that carry the wiring — does a real run actually arm the
log handler and the excepthook — are asserted by running each tool as its own
process with `2>&1` into a file, the way cron does it; every other row was
reproduced by hand the same way, and the redaction itself is covered by
in-process tests:

| channel | still reaches the log unfiltered | guard |
|---|---|---|
| `logging` — records a library writes (`msmart-ng` dumps raw receive buffers at `WARNING`) | no | `RedactingStreamHandler.format()` |
| `logging` — `lastResort`, because `midea_refresh_tokens.py` configured nothing at all | no | it now installs the same handler |
| `logging` — a library logger that sets `propagate = False` without a handler of its own | **yes** | its records reach neither the root's handlers nor ours, so `lastResort` prints them raw; `msmart-ng` sets no `propagate` |
| a library (or `argparse`) writing straight to `sys.stdout`/`sys.stderr`, past `logging` entirely | **yes** | no guard sees those; this project's own such line is the overview row below |
| `logging.Handler.handleError` on a malformed library log call | no | `handleError` override; the run also survives it |
| asyncio's default handler (`Task exception was never retrieved`) | no | same handler — `format()` renders `exc_info`, so the traceback goes through it |
| a chained traceback: a secondary exception inside an `except` block | no | `install_excepthook_redaction()` |
| a traceback from an exception nobody catches | no | same excepthook |
| `print()` past the catalogue (the overview line) | **yes** | but it prints name/ip/port only, never a secret |
| the same value in another **encoding** | **yes** | nothing matches it — see below |
| a credential that is not hexadecimal at all (a JWT, a dashed session id) | **yes** | this is a format filter, not a secret filter |
| `warnings.warn()` | **yes** | goes to stderr past every guard; no tool or library here uses it today |
| code that calls `logging.basicConfig(force=True)` or clears the root handlers | **yes** | removes the guard again; `msmart-ng` does neither |
| `sys.unraisablehook` (an exception inside `__del__`) | **yes** | not routed; neither tool nor `msmart-ng` defines `__del__`, but asyncio's own transports do |
| records written by a handler someone else installed | **yes** | foreign handlers are deliberately left alone |

Two design points are worth keeping, because the obvious implementations are
wrong and both were measured on the real classes. A `logging.Filter` on the
**root logger** never sees a child logger's records: those reach the root's
*handlers* without passing the root's *filters* (it does fire for records logged
on the root itself, which is not where a library writes). And a filter that
calls `record.getMessage()` raises on a malformed library log call —
`Handler.filter()` sits outside `emit()`'s `try`, so that version turns a
harmless library bug into a cron run that exits 1. Overriding `format()` avoids
both: it runs inside `emit()`, leaves `msg` and `args` untouched — which is what
other handlers read — and renders `exc_info` along the way, which is what makes
the asyncio row above work. (It does set `record.message` and `record.exc_text`,
but every formatter does that; the point is that nothing another handler
consumes is rewritten.)

**How much this costs today, measured against the pinned library rather than
assumed.** Every `warning`/`error`/`critical`/`exception` call in `msmart-ng`
2026.7.0 was extracted with an AST walk — a `grep` is not enough here, because
the interesting ones span several lines and show only `_LOGGER.warning(` on the
first. There are 65: 35 `warning`, 30 `error`, and none of the other two. No
record names a token or key. But four of them, all at `WARNING` and
therefore **active at the level these tools configure**, dump a raw receive
buffer from the device connection: `lan.py`'s "No start of packet found",
"Ignoring data before packet", "Buffer too short" and "Partial packet received",
each ending in `buf.hex()`. Whether key material is recoverable from such a
buffer is not settled here — it is device-connection payload in a world-readable
file, which is reason enough. Those four lines are exactly what the handler was
added for, and they now come out as `Buffer: [hex:128]`. At `INFO` the same
module logs the device's local key outright, and at `DEBUG` full frame dumps and
the cloud's reply; raising the level to troubleshoot therefore produces far more
material, all of it through the same handler.

Two things that are *not* problems, checked rather than assumed: `msmart-ng`
calls `basicConfig` only in its own CLI, and importing the path these tools use
(`msmart.device.AC.device`) provably does not load it — after the import
`sys.modules` has no `msmart.cli` and the root logger has no handler. And
`midea-local` does log tokens at `DEBUG`, but it is only ever run as a
subprocess with `capture_output=True`; its output cannot reach the log on its
own.

The redaction is also **encoding-specific**: it matches contiguous hex. The same
64 bytes rendered as `repr(bytes)`, as a spaced hexdump, or in groups is not
recognised, and from any of those forms the value is trivially recoverable. This
matters because a protocol library renders frames that way — the one form that
*is* covered, `bytes.hex()`, is what `msmart-ng` uses in the two `ProtocolError`
texts that quote a packet (`lan.py`, "Packet is too short" / "Unsupported
packet"), which is the case this filter was built for.

*What is left, and what it costs.* The encoding gap above is the real one: it
cannot be closed by a wider pattern, because "the same bytes with spaces in
between" has no lexical signature that a device id or a checksum does not also
have. Closing it properly means matching the **values** the tools actually hold
— which would give `midea_i18n` state it deliberately does not have, and would
still miss a candidate that has just arrived from the cloud. It is therefore
written down rather than half-fixed. The logs are created with the user's normal
umask (usually world-readable), which is what makes any leak worth avoiding;
`devices.json` is `chmod 600` by comparison.

Also still open, and deliberately: a handler installed by someone else keeps its
own output — `install_log_redaction` adds a handler and never removes foreign
ones. `threading.excepthook` and `sys.unraisablehook` are not set; neither tool
defines `__del__` or starts a thread of its own, and an exception inside
asyncio's executor lands in a future and is reported through the asyncio logger,
which the handler does cover.

Both guards are armed in `main()`, not at import. That was a correction: armed
at import, `install_log_redaction` clamped an embedding application's root level
from `DEBUG` to `WARNING` and doubled every line of its output — measured, and
worse than the `logging.basicConfig()` it replaced, which does nothing at all
when handlers already exist. Two consequences are worth stating precisely.
Importing either module gives you the **catalogue** redaction (that one lives in
`t()` and needs no setup) but neither the logging nor the traceback guard, since
it gives you no logging configuration either — only a real run arms those. And
calling `main()` from your own code still costs you the root level and gives you
a second handler: the guards are installed for the run, not negotiated with a
host application.

One consequence for the test suite, measured rather than assumed. Several tests
call `main()` in-process, so a full run leaves a `RedactingStreamHandler` on the
root logger and a marked `sys.excepthook` behind. The two tests that install the
handler *directly* now undo it; the ones that go through `main()` do not, and
that is left alone deliberately — they are pre-existing tests and the residue
harms nothing today.

What makes it worth writing down is *why* it currently holds. A `StreamHandler`
binds `sys.stderr` when it is **created**, and six of those tests — five in
`ExitCodeTests`, one in `ConfigMessageOrderTests` — run `main()` inside a
`redirect_stderr` block. Measured on the current suite, the handler that ends up
on the root logger is created exactly once, by a test that redirects only
*stdout*, so it holds the real stderr. That is a property of `unittest`'s
alphabetical class order, not a guarantee: rename a class and the handler could
end up holding a dead buffer, which would silently swallow library records in
later tests. Nothing in production is affected — every cron run is a fresh
process where `main()` binds the real stderr.

Two deliberate boundaries. A hex run **shorter** than 32 characters stays
visible: a token cut by the 800-character tail keeps its remainder if fewer than
32 characters are left, which drops more than 96 of a token's 128 and makes the
rest useless. And the threshold cuts both ways — a *device name* or an
appliance id of 32 hex characters is redacted too, which removes it from every
line that renders it through the catalogue (the overview line prints the name
with an f-string and keeps showing it). Real ids are decimal and about 15
digits, so this is a theoretical loss, but it is the price of a filter that does
not need to know which argument is a secret. A `udpId`, at 64 hex characters, is
the one realistic case: several tokenlist entries become indistinguishable in an
error message. The extraction itself is unaffected — it runs on the raw text
before any message is built.

## Equivalent mutants (survive by design, not by omission)

Do not "fix" these by adding a test — on the interpreters this project supports
(Python 3.11+) there is nothing to observe.

That qualifier is not decoration, and it applies to the first entry below: on
Python 3.9 the edit *is* observable, and the tests that catch it are already
written. Measured on 3.9.6, dropping `asyncio.TimeoutError` from the check turns
`test_own_time_limit_actually_caps_a_hanging_device` and
`test_a_hanging_refresh_is_capped_as_well` red — `FAILED (failures=2)` — while the
same edit leaves 3.14 green. So what the entry records is an equivalence that
holds from the project's floor upwards, not one that holds on every interpreter
the module happens to import under.

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

- **The `@reboot` catch-up line is not an expected job, and its absence is never
  reported.** `cron_scan_tools` skips any managed line carrying `--only-if-due` as a
  whole token, so it counts towards neither of the two jobs the inactive-job notice
  demands. That is a decision, not an oversight: every installation made before this
  line existed lacks it, and treating it as expected would greet the entire installed
  base with "at least one job is not active" on the next re-run — for a job they never
  had. *Cost:* someone who deletes the catch-up line is not told. *Why that is the
  right way round:* losing it costs a missed weekly refresh being caught up late; the
  weekly line itself still runs, and its absence **is** reported.
- **Two refresh runs that start in the same instant can still both reach the
  devices.** The catch-up guard (`--only-if-due`) writes `last_attempt` *before* the
  device loop precisely so that a second run started afterwards backs off — but two
  processes that get past the check before either has written it are not excluded.
  Realistically this needs the `@reboot` line and the weekly line to overlap: with
  the `sleep 120` grace period the catch-up decides around 03:02, so the window is a
  machine whose cron daemon starts roughly two minutes before the slot (≈02:58 on a
  Sunday), the weekly run then still inside its own device loop. *Cost:* two local
  connections to a unit that tolerates one, i.e. a temporarily quiet unit — the
  same class the delays elsewhere in the tool guard against, not data loss. *Why not
  closed:* the obvious fix is an `O_EXCL` lock file, and that trades this rare case
  for a worse one — a run killed mid-flight (power cut, OOM, `kill -9`) leaves the
  lock behind, and from then on the refresh never runs again, silently. A guard
  whose failure mode is permanent silent non-execution is the wrong trade for a tool
  whose entire purpose is preventing silent non-execution. Written down instead of
  left to be rediscovered.
- **A backward clock still costs one skipped catch-up per boot until it corrects.**
  The future-attempt block is bounded (a stamp more than one retry interval ahead no
  longer silences the catch-up forever — that was fixed), but a stamp *up to* a
  retry interval ahead still blocks. On a machine whose clock reads a recent-past
  date at boot and only corrects via NTP a few seconds later, a boot where the check
  runs before NTP lands can skip that boot's catch-up. *Cost:* the catch-up is
  missed on that boot, caught on the next one where the clock is right in time — not
  permanent, and the weekly line remains the backstop on any host that is up for it.
  *Why not closed:* distinguishing "clock briefly behind" from "genuine recent
  attempt" needs a trusted time source the tool does not have at `@reboot`; the
  bounded window is the honest compromise.
- **`--only-if-due` with neither `--all` nor `--name` exits 2, not 1.** The explicit
  check gives exit 1 for `--only-if-due --name X`; but dropping `--all` entirely
  leaves argparse's own required-group error, which exits 2 — the code this tool uses
  for "a device failed". A crontab typo that omits `--all` therefore reaches a
  monitor as a device fault rather than a usage error. *Why not closed:* catching it
  would mean giving up argparse's `required=True` group and validating the two flags
  by hand, a larger change to the argument parsing than a mis-mapped exit code on a
  hand-edit warrants. Recorded rather than fixed.

- **A capability answer that arrives but cannot be decoded is still blamed on the
  unit.** `_send_commands_get_responses` sets `_online = len(responses) > 0` on the
  **raw** byte list, *before* `Response.construct` validates and discards anything
  malformed. A reply that fails the payload CRC, or that is a valid frame of the
  wrong response type, therefore leaves `online == True` while no capability was
  learned — and `ensure_ieco`'s `caps_answered` guard, which reads exactly that
  `online`, lets it through to `Device reports no iECO capability`. Measured against
  the real msmart-ng 2026.7.0; **identical before and after** the carry-over change,
  so it is a residual of the same class, not a regression. Closing it would mean
  reaching past `online` into msmart's discarded response objects, for which there is
  no public API. The packet-loss case it neighbours — no answer at all — *is* covered
  and is by far the more common one; a garbled reply implies a unit that answered on
  a healthy connection with an unparseable payload.
- **A verification round that answers the state query but loses only the property
  read** still produces `iECO is still disabled`, which is a statement the unit did
  not make. It survives both guards by construction: `msmart-ng` sets `_online` once
  per *command batch* (`_send_commands_get_responses`), and `refresh()` sends
  `GetStateCommand` and `GetPropertiesCommand` in one batch — so one answer out of
  two leaves `online == True` while `device.ieco` keeps the fresh object's default
  `False`. The library discards the response object, so there is no public way to
  ask "was the IECO property reported?". Measured against the real msmart-ng
  2026.7.0 with a scripted transport; the neighbouring cases are covered (a fully
  silent verification hits `dev_verify_no_answer`, a lost capability exchange hits
  the carry-over). Closing it would need either another reach into msmart internals
  or an extra roundtrip on a unit that tolerates exactly one connection — both were
  judged disproportionate to a failure that needs a *partial* loss inside a single
  batch. The suite cannot express it either: the device fakes answer per call, not
  per batch.
- **The mirror of that gap corrupts a *successful* report.** If the verification's
  state query is lost while the property read is answered, `online` is still `True`,
  `device.ieco` is read correctly — so the verdict `OK: iECO is active` is right —
  but `_update_state` never ran for a `StateResponse`, so the line above it prints
  the fresh object's defaults: `power=False, mode=AUTO (1), eco=False`. Three
  fabricated values inside a success message. Same cause and same measurement as the
  entry above, identical before and after the change. Recorded rather than fixed for
  the same reason; noted here because the neighbouring wording says the capability
  failure mode "disappears structurally", which must not be read as covering the
  status line too.

- **`hint_all_cap`** was removed rather than tested: our verification cap sits above
  msmart-ng's own worst case, so an all-timeout run is not expected to occur.
  Both figures have since been re-derived from the pinned source: `authenticate`
  ≈ 12 s (5 s connect + 3 × 2 s read timeout, plus the 1 s trailing sleep that is
  only reached when the handshake succeeds), and `refresh` → `LAN.send` ≈ 6 s
  (`RETRIES = 3`, 2 s per read). `VERIFY_CAP` still classifies correctly if it ever
  does fire; there is simply no invented advice attached to it. Note the cap can
  only *surface* while `authenticate()` runs — during `refresh()` msmart-ng
  swallows the cause, and what surfaces there is the `online` check instead.
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

- **The `msmart-ng` teardown contract does not run without the real dependency.**
  The connection teardown in `midea_conn.py` uses the private chain
  `device._lan._disconnect()`, and `tests/test_conn.py::MsmartContractTests` pins
  that path against the real library. It runs its probe in a **subprocess** — this
  suite registers an `msmart` stub, so an in-process check would have verified the
  stub — and skips only when `msmart-ng` is genuinely **not installed**, which is
  the normal state on a developer machine. (An earlier version of this entry said
  it skips *because msmart is stubbed*; that is wrong — with `msmart-ng` present
  the test runs, stub or no stub, because the subprocess never imports the stub.)
  **Consequence:** on a machine without `msmart-ng`, a release that moved the
  private path goes unnoticed until CI. Cheapest close: install the pinned
  dependencies locally, or run `python3 -m unittest tests.test_conn.MsmartContractTests`
  inside the venv. In CI it cannot degrade quietly: the `install-smoke` step sets
  `MIDEA_IECO_REQUIRE_MSMART`, under which any skip is a failure, and a crashed
  probe fails everywhere regardless of that variable.
- **The fake-fidelity check tests three names, not the whole surface.**
  `FakeFidelityTests` asserts that no device fake offers `close`, `disconnect` or
  `stop` — the names the real `AirConditioner` lacks and that the previous, dead
  teardown probed for. It would **not** catch a fake inventing some *other* method
  the real object does not have. The general form (compare each fake against the
  real class) needs the real library, which the stubbed suite does not have; the
  inverse direction — that the real object still lacks these three — is what
  `MsmartContractTests` pins in CI. Accepted: the narrow check covers the concrete
  defect class that actually occurred, and the general one would inherit the skip
  problem above.

## Corrections to earlier versions of this file

Recorded so the same wrong statements do not get re-derived from the history:

- **Gap 10's length formula named the iECO line as the longest managed line.** True
  while there were three of them. The `@reboot` catch-up line is 17 characters longer
  at the same path — `@reboot sleep 120 &&` for `*/20 * * * *` (+8), the longer script
  name and flag (+6), and `refresh.log` for `ieco.log` (+3); grace period and flag
  alone are the +25 gap to the *weekly* line, not to the iECO line — so the constant
  moved from 136 to 153 and the worst case from a measured 32880 to 32897. The same
  claim sat in `install.sh`'s guard comment and was corrected there too. The guard's
  threshold and its conclusion are untouched: 65536, with 32639 characters of
  headroom, measured rather than computed.

- **Gap 8 described a whitespace split.** It did, accurately, until the tokenizer
  replaced it. The rewrite above is not a correction of a wrong statement but of a
  statement that the code outgrew — noted here because the two are easy to confuse
  when reading the history.
- **Gap 8 claimed the line was split "the way `/bin/sh` would" and then listed
  `;`, `&`, `|`, `<` and `>`.** That list was incomplete: `/bin/sh` separates at
  `(` and `)` as well, and the tokenizer did not. A running job written as
  `(midea-ieco all) # marker` — the bin wrapper both READMEs recommend, wrapped in
  parentheses to redirect the group — therefore counted as **missing**, and the
  installer offered its line to add. That is the expensive direction, measured
  against an execution oracle over 2200 generated lines: 60 such cases before the
  fix, none after, and the fix introduced no case in the other direction. Fixed,
  and the list above now names the two characters. The claim itself is the lesson: a sentence that says "the way sh
  does" is only as true as the enumeration next to it.
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
  appends four lines when the marker is absent. See gap 9.
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
- **"Do not fix these by adding a test — there is nothing to observe"** (the
  heading of the equivalent-mutants section) was true of the interpreters this
  project supports and too broad for the first entry underneath it. On Python 3.9.6
  that mutation turns two existing tests red. Corrected at the heading, with the
  measurement; the entry itself is unchanged and still belongs there.
- **Both READMEs framed the three crontab notices as reachable from a plain
  re-run.** Two of them are; the "read the crontab twice and got different
  results" notice sits behind the cron question, which the re-run branch never
  reaches — it prints its two notices and exits. The second bullet in both READMEs
  now says so.
- **`tests/README.md` claimed "Nothing leaves the sandbox" as a standing
  property.** The file's own next sentence, and gap 7, describe it as a checkpoint
  someone has to re-run — the suite makes no permanent assertion about it. The
  sentence now carries that qualifier.
- **"55 calls in the same run"** (`tests/README.md` *and* `CHANGELOG.md`) was
  attributed to the two end-to-end sandboxes. Measured with instrumented stubs: 55
  is the whole-run figure, of which 48 fall inside those sandboxes (44 onboarding,
  4 `--update`) and 7 come from the `pip`-snippet tests. Both numbers now appear in
  both places, with what each covers — the earlier "51 pip calls" correction listed
  both files for the same reason, and this one initially did not.
- **The language notice's blindness to the wrappers was described as a bin-wrapper
  matter.** It is structural: the check matches the two script names literally, so
  `midea_ieco_ensure.sh` and the refresh wrapper are equally invisible, on both tool
  sides. Measured and written up in gap 6, where it belongs with the rest of that
  gap rather than as a footnote.
