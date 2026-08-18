# Dreaming evidence completeness

## Objective

Make the Mac mini finish and maintain trustworthy Copilot skill-usage coverage
for the MacBook, attribute exact historical skill renames, expose the read-only
dashboard to the trusted tailnet, and record which other dashboard claims have
or lack live producers.

## Lane

**Critical.**

Most of the work is systemic because it changes persisted collection state and
the MacBook-to-mini evidence flow. The tailnet route also changes the
dashboard's authentication boundary, so the critical lane applies to the whole
work order.

The critical boundary remains small: the server stays bound to loopback, stays
read-only, accepts unauthenticated API reads only for one explicitly configured
Tailscale Serve host, and continues requiring the existing bearer token for
localhost requests.

## Non-goals

- Do not add SQLite, a database service, a daemon, a queue, a background worker,
  or a second transcript collector.
- Do not retain byte offsets or resumable parser state inside a transcript.
  A changed session is streamed again from the beginning.
- Do not make usage collection generic across Copilot, Claude, and Codex.
  This change covers the existing Copilot estate collector only.
- Do not read Git during normal collection or infer renames dynamically.
  Exact historical aliases are recorded in the existing collector from
  reviewed Git evidence.
- Do not force every historical name to resolve. Ambiguous names and plugin
  histories without exact evidence remain unattributed.
- Do not add dashboard accounts, passwords, cookies, login screens, TLS
  termination, a reverse proxy, or a general Tailscale integration framework.
- Do not bind the dashboard to a LAN, tailnet, wildcard, or non-loopback
  address.
- Do not add dashboard mutation endpoints or allow usage evidence to authorize
  skill retirement, restoration, publication, or plugin disablement.
- Do not automatically repair every gap found by the live-evidence audit.
  The audit records bounded follow-up work; only defects that prevent this
  objective from being proved are fixed here.
- Do not redesign Dreaming's review queue or raise its review budget without
  evidence that the current arrival rate cannot be served.

## Observed failures

The MacBook retains 2,229 direct-child `events.jsonl` files totaling 18.68 GiB.
The current collector starts from the same sorted session every run, reads
whole files into memory, and stops before a file that would exceed its one-GiB
budget. The latest run therefore scanned the same 828,330,049-byte prefix, and
a 1,758,979,878-byte session can never be admitted under the current rule.
Longer or more frequent runs cannot advance coverage.

Nine observed skill names are currently unattributed because the collector
joins only against names in the current census. The personal skills Git
history contains exact rename and retirement evidence for at least some of
those names, but collection does not use that evidence.

The dashboard remains loopback-only and bearer-protected. Tailscale Serve
forwards the exact MagicDNS Host header, which the server currently rejects, so
the intended phone URL is unusable.

Finally, installed checks prove many component and fixture contracts but do not
enumerate every user-visible dashboard claim and demonstrate that each one has
a live producer. The missing usage producer passed earlier checks for this
reason. The latest run also reported 1,388 deferred reviews without a recorded
service-level interpretation.

## Reuse contract

This work extends existing owners rather than adding new services:

- `dreaming-estate.py` remains the only skill census and usage collector. Its
  existing event validation, successful-call matching, name normalization, and
  privacy rules remain authoritative.
- `ssh-estate-census.py` remains the pinned MacBook receiver. Only aggregate
  usage output crosses SSH.
- `dreaming-core.py` continues writing the current content-addressed census and
  usage receipts. The local index is a rebuildable collector cache, not a new
  evidence authority.
- `dreaming-dashboard.py` remains the only dashboard server. Its loopback bind,
  exact Host and Origin checks, read-only methods, response hardening, and
  bearer-token path are retained.
- The existing installer and LaunchAgent continue owning the dashboard process.
  Tailscale Serve remains an external loopback proxy and gets one explicit
  port entry rather than a new proxy process.
- The existing run records, dashboard APIs, and installed receipts supply the
  live-evidence audit. The audit is a document, not a new runtime subsystem.

One new persisted file is required on the MacBook because the current
collector otherwise cannot distinguish already processed sessions from pending
ones. It is a cache whose complete contents can be reconstructed from retained
transcripts.

## Design

### 1. Minimal per-session usage index

The collector stores one atomic JSON file under the MacBook's existing
Dreaming state root. Schema version 1 contains:

- the collector schema version;
- one entry keyed by the existing opaque session identifier;
- the source file fingerprint: device, inode, size, and nanosecond
  modification time;
- the earliest valid event timestamp;
- successful skill loads aggregated by normalized skill name and UTC day;
- the last successful load timestamp for each observed name;
- bounded parse issues for that session;
- the last completed checkpoint time.

It does not contain transcript text, prompts, arbitrary tool arguments, tool
results, filesystem paths, or raw session names.

At collection time:

1. Enumerate the same direct-child session directories used today.
2. Reuse an indexed summary when its complete fingerprint still matches.
3. Order unindexed and changed files by oldest modification time, with opaque
   session identity as the deterministic tie-breaker.
4. Leave a file pending without opening it when it was modified within the last
   300 seconds. This is a collector constant pinned by the existing collector
   hash, not a new remote configuration surface.
5. Stream an eligible unindexed or changed `events.jsonl` line by line using
   the current event and completion checks.
6. Compare the file identity and size before and after the read. A file that
   changes during the read remains pending and does not replace its prior
   summary.
7. Atomically checkpoint the index after each successfully parsed session.
8. Remove cached entries only after a successful full directory enumeration
   proves their source sessions no longer exist.
9. Aggregate cached session summaries against the current census and collection
   time to produce the existing 7-, 30-, and 90-day counts, retained total, and
   last-use fields.

The byte and session limits become per-run **work budgets**, counting only files
actually parsed during that run. Cached unchanged files cost no work budget.
The collector stops before starting another pending file once either budget is
exhausted. When the first eligible file in a run alone exceeds the byte budget,
the run still streams that one file and then stops, whether the read succeeds
or the file changes. This is the only budget exception.

Oldest-first ordering and the fixed quiet period prevent one active oversized
file from permanently blocking stable work. If it changes during streaming,
its newer modification time moves it behind older stable pending files on the
next run. The collector does not add retries, recovery allowances, or another
cursor.

A changed session is read again from the beginning. This is intentionally less
efficient than retaining byte offsets, but it avoids partial-line state,
unfinished tool-call state, offset invalidation, and index migrations. The
retained corpus is only 2,229 sessions, most completed session files are
immutable, and the design can be revisited only if measured steady-state
re-reading is material.

An absent index starts an initial catch-up. An invalid index is renamed as a
rejected cache, collection starts from an empty index, and the usage snapshot
reports that a rebuild occurred. Raw transcripts remain the authority, so
rebuilding the cache cannot lose source evidence.

Coverage reports both corpus state and current-run work:

- discovered sessions and bytes;
- indexed sessions and bytes;
- pending sessions and bytes;
- failed or changing sessions;
- files and bytes parsed in this run;
- whether the work budget stopped the run;
- earliest retained event;
- whether corpus indexing is complete;
- whether name attribution is complete.

Collection completeness and attribution completeness remain separate. Unknown
historical names do not make the transcript corpus unindexed.

### 2. Small Git-backed alias table

`dreaming-estate.py` gains a small, reviewed alias table. Each entry contains:

- historical normalized name;
- current normalized runtime name;
- evidence repository;
- exact Git commit or explicit retirement record;
- evidence kind.

The initial table contains only aliases proved by exact Git rename records or
explicit retirement history in the personal skills repository. Entries are
flattened to the current runtime name; the collector does not follow alias
chains.

Before landing, a finite check verifies every shipped alias against its cited
repository and immutable Git commit or retirement record. Normal collection
does not perform this Git check.

An alias applies only when:

1. the observed historical name has no direct current mapping;
2. the alias target has exactly one enabled canonical mapping in the same
   census; and
3. the alias entry passes static validation.

Direct current names always win. Conflicting targets, missing targets,
ambiguous delete/add histories, and unverified plugin histories remain
unattributed. Alias evidence is diagnostic only and cannot affect mutation
authority.

Keeping the table in the collector means the existing pinned collector hash
also pins the aliases. This avoids another remotely configured file, hash, or
protocol field.

### 3. Tailnet dashboard route

The dashboard continues listening only on `127.0.0.1:47673`.

The installer accepts one optional exact value:

`DREAMING_DASHBOARD_TAILNET_HOST=mac-mini.hornbill-dragon.ts.net:47673`

When unset, behavior is unchanged. When set:

- the exact Host is allowed;
- an Origin, when present, must be the exact corresponding `https://` origin;
- safe read-only requests for that Host do not require the dashboard bearer;
- localhost API requests continue requiring the bearer;
- all other Host, Origin, cookie, query-token, method, and path checks remain
  unchanged.

On that configured tailnet Host, the static application does not require a
token fragment, does not show the localhost bootstrap instructions, and sends
its API requests without an Authorization header. On localhost it retains the
existing fragment, `sessionStorage`, and bearer behavior.

This treats membership in the owner's trusted tailnet as sufficient for this
read-only dashboard. It intentionally does not inspect Tailscale identity
headers because a local process can forge forwarded headers. Local processes
on the mini are inside the accepted read-only threat boundary and can also
reach other tailnet-published local services.

Deployment adds one Tailscale Serve entry for HTTPS port 47673 forwarding to
`127.0.0.1:47673`. It must preserve the existing Serve entries on ports 1610,
3006, 443, and 8000. The expected phone URL is:

`https://mac-mini.hornbill-dragon.ts.net:47673/`

No transcript route gains broader filesystem access, and no response gains
mutation authority.

### 4. Bounded live-evidence audit

Create `docs/dreaming-live-evidence-audit.md` as a finite review of the current
product, not an automated framework.

The audit covers:

- every current dashboard API section;
- scheduled run, discovery, review, evaluation, publication, estate, and
  watchdog claims visible in the dashboard;
- the producer and persisted source for each claim;
- one current installed example or an explicit statement that no live example
  exists;
- freshness, completeness, and backlog semantics;
- whether existing certification is live, fixture-only, or absent.

The audit specifically records:

- what `deferred_reviews` counts;
- the configured per-run review limit;
- observed queued work and arrival rate over a bounded sample;
- whether scheduled capacity is above or below the observed arrival rate;
- which historical names remain unattributed after exact aliases are applied.

Missing or fixture-only producers become named follow-ups. This work does not
build fixes for those follow-ups unless the missing producer prevents the
usage, tailnet, alias, or audit acceptance criteria below from passing.

## Failure model

| Failure | Required behavior |
| --- | --- |
| Index is absent | Start catch-up from retained transcripts |
| Index JSON or schema is invalid | Preserve it as a rejected cache, rebuild, and report the rebuild |
| Session root cannot be enumerated | Fail collection without pruning the index or replacing current mini evidence |
| Indexed file fingerprint matches | Reuse its summary without reading transcript content |
| File was modified within the last 300 seconds | Leave it pending without spending the work budget |
| File changes during streaming | Keep any prior matching summary, mark the current file pending, and retry later |
| One file exceeds the work budget | Stream that file alone and checkpoint it so later runs advance |
| Process exits between sessions | Retain every prior atomic checkpoint and resume with the next pending session |
| Historical alias has no unique current target | Leave the name unattributed |
| Alias table is malformed or conflicting | Fail collector startup rather than apply uncertain attribution |
| Tailnet host setting is absent or malformed | Retain localhost-only bearer behavior and do not add a remote Host |
| Request uses any unconfigured Host or Origin | Reject it before reading dashboard state |
| Tailscale Serve entry is absent | Dashboard remains safely available only through its existing localhost path |
| Audit cannot find a live producer | Record the claim as missing or fixture-only; do not infer success |

## Hard invariants

1. Only direct-child regular `events.jsonl` files under the configured MacBook
   Copilot session root can enter the index.
2. The index contains aggregates and fingerprints only, never transcript
   content or raw session names.
3. A cached summary is reused only for an exact file fingerprint match.
4. A run with pending work makes monotonic progress unless no pending file is
   both readable and at least 300 seconds old.
5. One oversized file cannot permanently block later sessions.
6. Corpus completeness, attribution completeness, and current-run budget status
   are separate facts.
7. Historical attribution requires an exact reviewed alias and a unique target
   in the current census.
8. Usage and alias evidence cannot authorize any estate mutation.
9. The dashboard process remains loopback-bound and read-only.
10. Tailnet token bypass applies only to the one configured exact HTTPS Host
    and Origin; localhost retains bearer authentication.
11. Invalid tailnet configuration fails closed to localhost-only behavior.
12. The audit distinguishes live evidence, fixture evidence, and missing
    evidence without success-shaped defaults.

## Acceptance criteria

- **AC-E1:** Two bounded runs over a fixture corpus advance from the first
  pending sessions to later sessions instead of rescanning unchanged files.
- **AC-E2:** A fixture larger than the byte budget is streamed and checkpointed
  in one run without reading the whole file into memory, and the following run
  proceeds beyond it.
- **AC-E3:** Restarting after a completed session reuses its indexed summary;
  changing that file causes a full replacement summary without double-counting.
- **AC-E4:** The dashboard reports indexed and pending sessions and bytes,
  corpus completeness, attribution completeness, and current-run work
  separately; incomplete zeroes remain unknown.
- **AC-E5:** Every currently unattributed observed name is listed with either a
  shipped alias whose cited Git evidence has been verified or a concrete reason
  it cannot be safely attributed; exact personal-skill renames are attributed.
- **AC-E6:** The installed mini serves the dashboard at the exact MagicDNS URL
  from a tailnet peer without a bearer token while localhost API access still
  requires its bearer.
- **AC-E7:** Non-loopback bind attempts and requests with an unconfigured Host,
  foreign Origin, write method, query token, or cookie receive no private
  response content.
- **AC-E8:** Existing Tailscale Serve entries remain unchanged except for the
  added port-47673 entry, and removing only that entry restores the prior
  network state.
- **AC-E9:** Repeated installed collection reaches complete transcript corpus
  indexing for the retained MacBook session root or reports the exact remaining
  failed or changing sessions; repeated runs show forward progress.
- **AC-E10:** A measured steady-state run processes only new or changed
  sessions, and its work rate exceeds the observed transcript arrival rate.
- **AC-E11:** The live-evidence audit covers every current dashboard API
  section and records each claim's producer, stored source, live proof,
  freshness/backlog meaning, and certification level.
- **AC-E12:** The audit records whether the review queue can burn down at the
  configured schedule and names a follow-up if observed arrival meets or
  exceeds capacity.
- **AC-E13:** Existing estate mutation, usage-receipt, dashboard containment,
  installer, watchdog, and self-test contracts remain green.

## Check contract

### CHK-E1: Indexed collection progression

- **Protects:** AC-E1, AC-E2, AC-E3 and invariants 1 through 6.
- **Setup:** Build direct-child session fixtures containing unchanged, changed,
  malformed, deleted, recently modified, changing, and oversized event files.
  Set a work budget smaller than the corpus and smaller than the oversized
  file. Interrupt once after an atomic session checkpoint.
- **Pass signal:** Unchanged fingerprints are not opened; each successful run
  indexes new sessions; the oversized file completes alone; restart resumes
  after the last checkpoint; a changed session replaces rather than adds to its
  previous counts; files newer than 300 seconds are skipped without reading;
  pending files are attempted oldest-first; and a changing oversized file moves
  behind older stable work on the following run.
- **Failure signal:** A later run rereads the same unchanged prefix, the
  oversized file blocks progress, the collector loads the complete file as one
  byte string, restart loses prior completed sessions, or replacement counts
  are duplicated, recent files spend the work budget, pending order is not
  deterministic and oldest-first, or any run attempts more than one file after
  exceeding the byte budget.
- **Why it proves the contract:** It exercises the exact progress failure that
  prevents the current collector from ever completing.

### CHK-E2: Index privacy and invalidation

- **Protects:** AC-E3, AC-E4 and invariants 1 through 3.
- **Setup:** Index events containing prompts, paths, arbitrary arguments,
  results, and skill calls; inspect the stored index; then alter inode, size,
  modification time, schema, and JSON validity independently.
- **Pass signal:** The index contains only allowed fingerprints and aggregates;
  every fingerprint change causes reprocessing; invalid state is preserved as
  rejected and reported while a rebuild starts.
- **Failure signal:** Transcript text or raw session names enter the index, a
  changed file reuses stale counts, or invalid state becomes silently trusted.
- **Why it proves the contract:** The local cache is safe only if it is both
  privacy-bounded and never mistaken for current source evidence.

### CHK-E3: Alias attribution

- **Protects:** AC-E5 and invariants 7 and 8.
- **Setup:** Exercise direct names, exact aliases, missing targets, conflicting
  targets, alias cycles, duplicate alias keys, and an observed plugin name with
  no reviewed evidence. Separately enumerate every shipped alias and verify its
  repository, immutable evidence reference, historical name, target, and
  evidence kind against Git. Compare the current unattributed-name list with
  the shipped table and audit reasons.
- **Pass signal:** Direct unique mappings win; exact aliases reach one current
  canonical capability; every uncertain case remains unattributed; the alias
  table has no path into mutation authorization; every shipped alias matches
  its cited evidence; and every current unattributed name has either a verified
  alias or a concrete recorded reason.
- **Failure signal:** An uncertain name is assigned, an alias overrides a
  direct mapping, malformed aliases load, alias evidence changes authority, a
  cited commit does not prove the recorded mapping, or an observed name has
  neither an alias nor a recorded reason.
- **Why it proves the contract:** It permits useful historical attribution
  without turning name similarity into governance evidence.

### CHK-E4: Coverage projection

- **Protects:** AC-E4 and AC-E9.
- **Setup:** Feed the API complete, catching-up, failed-session, changing-file,
  and attribution-incomplete snapshots.
- **Pass signal:** Corpus, attribution, and work-budget states remain distinct;
  indexed and pending counts balance against discovered counts; incomplete
  absence never renders as zero.
- **Failure signal:** Catch-up looks complete, attribution gaps erase indexed
  coverage, or missing data becomes a zero-use claim.
- **Why it proves the contract:** The dashboard must explain progress without
  overclaiming what has been scanned or mapped.

### CHK-E5: Tailnet request boundary

- **Protects:** AC-E6, AC-E7 and invariants 9 through 11.
- **Setup:** Start the server on loopback with and without one configured
  tailnet Host. Send localhost bearer and no-bearer requests; exact tailnet Host
  requests with absent, correct, and foreign Origins; invalid Host, cookie,
  query-token, and write-method requests; and non-loopback bind attempts.
- **Pass signal:** Localhost API requires the bearer; the exact configured
  tailnet HTTPS origin can read without it; every other case fails before state
  reads; non-loopback bind remains impossible.
- **Failure signal:** A broader Host or Origin bypasses the token, localhost
  loses its bearer requirement, a write method succeeds, or the process binds
  beyond loopback.
- **Why it proves the contract:** It proves the intentionally relaxed path is
  exact rather than a general authentication removal.
- **Pre-build guard:** This request-boundary fixture must be updated and shown
  red for the new tailnet acceptance case, then green with all existing
  negative cases, before frontend or installer work proceeds.

### CHK-E6: Tailscale Serve lifecycle

- **Protects:** AC-E6 and AC-E8.
- **Setup:** Record `tailscale serve status --json`, add only the HTTPS 47673
  route, fetch the exact URL from a tailnet peer, remove only that route, and
  record status again.
- **Pass signal:** The peer receives the current dashboard; unrelated Serve
  entries are byte-for-byte equivalent before and after; the browser renders
  the real dashboard without a token fragment or bootstrap-instructions state;
  rollback removes phone access while localhost remains healthy.
- **Failure signal:** Another service changes, the route exposes a non-loopback
  listener, the exact URL fails or renders only token-bootstrap instructions,
  or rollback damages existing Serve state.
- **Why it proves the contract:** It verifies the real proxy boundary shared
  with the owner's other mini services.

### CHK-E7: Installed catch-up and steady state

- **Protects:** AC-E9, AC-E10 and AC-E13.
- **Setup:** Install one reviewed candidate on both hosts, run receiver-bound
  collection repeatedly until no ordinary pending session remains, record each
  run's index counters, then observe transcript-byte growth across a bounded
  representative interval and create or extend one real Copilot session. Run
  again after it stabilizes and compare parsed bytes per scheduled interval
  with observed arriving bytes per interval.
- **Pass signal:** Indexed sessions and bytes increase monotonically during
  catch-up; the 1.64-GiB session completes; the final corpus is complete or
  names exact remaining failures; the steady-state run reads only changed
  sessions; and measured parsing capacity exceeds measured arrival rate.
- **Failure signal:** Counters stall on readable data, runs rescan unchanged
  sessions, the oversized file remains pending, new usage is absent, or a
  numeric capacity comparison is absent or does not exceed arrivals.
- **Why it proves the contract:** It proves the real 18.68-GiB corpus can catch
  up and then stay caught up.

### CHK-E8: Live-evidence audit

- **Protects:** AC-E11, AC-E12 and invariant 12.
- **Setup:** Enumerate the current dashboard API routes and visible run claims,
  trace each to its producer and persisted source, and compare fixtures with
  current installed API and receipt examples. Measure queued reviews and new
  arrivals over a bounded interval against configured scheduled capacity.
- **Pass signal:** Every in-scope claim has one audit row with producer, source,
  live example or explicit absence, freshness/backlog meaning, and
  certification level; review throughput has a numeric conclusion.
- **Failure signal:** A route or claim is omitted, a fixture is described as
  live evidence, absence becomes success, or queue capacity is asserted
  without measured inputs.
- **Why it proves the contract:** It closes the process gap that allowed a
  complete-looking dashboard to lack a real usage producer.

### CHK-E9: Existing contract regression suite

- **Protects:** AC-E13 and invariants 8 through 11.
- **Setup:** Run the existing estate mutation, usage-receipt, dashboard request
  and transcript-containment, installer, self-test, and watchdog checks on the
  candidate.
- **Pass signal:** Every named existing suite passes without weakening its
  localhost, receiver, evidence, or mutation assertions.
- **Failure signal:** Any named suite fails or is skipped without an explicit
  repository-health control result.
- **Why it proves the contract:** The new cache and exact tailnet read path must
  not weaken the already proved governance and private-data boundaries.

## Migration

Deployment is coordinated because the receiver pins the collector:

1. Install the candidate collector and receiver on the MacBook.
2. Install the same candidate on the mini and regenerate adapter hashes.
3. Leave existing census and usage receipts untouched.
4. Run collection. The absent index begins catch-up and the current usage
   pointer advances only when the receiver-bound snapshot validates.
5. Configure the exact tailnet Host, restart the dashboard, and add only the
   Tailscale Serve 47673 entry.

The first indexed snapshot may remain incomplete while catch-up advances.
Existing lower-bound presentation remains in force.

## Rollback

1. Remove only the Tailscale Serve 47673 entry.
2. Restore the prior dashboard configuration and candidate on the mini.
3. Restore the matching prior collector and receiver on the MacBook and
   regenerate adapter hashes.
4. Leave the rebuildable MacBook index on disk; prior code ignores it.
5. Preserve all census, usage, run, evaluation, and governance receipts.

Rollback succeeds when the prior authenticated localhost dashboard and prior
receiver-bound collection run again, all unrelated Serve entries match their
pre-change state, and no mutation-authorizing state changed.

## Fail-closed evidence

The boundary fails closed when deterministic and live checks show:

- unset or malformed tailnet configuration does not add a remote Host;
- any Host or Origin other than the exact configured values is rejected before
  state reads;
- localhost still requires its bearer;
- the process cannot bind beyond loopback;
- removing the Serve entry removes remote access without changing localhost;
- malformed aliases fail startup or remain unattributed;
- unreadable source or invalid receiver generations preserve prior current
  evidence rather than publishing complete or zero-use claims.

## Definition of Done: Dreaming evidence completeness

- [x] CHK-E1 through CHK-E5 and CHK-E9 pass on deterministic fixtures.
- [x] Exact Git-proven aliases among the current unattributed personal names are
      recorded and CHK-E3 proves uncertain names remain unresolved.
- [x] The installed MacBook index makes forward progress across bounded runs,
      streams the oversized session, and reaches the CHK-E7 terminal state.
- [x] The dashboard shows corpus and attribution completeness plus pending
      sessions and bytes without turning incomplete absence into zero.
- [x] The exact MagicDNS dashboard URL works from a tailnet peer without a
      bearer while localhost retains bearer authentication.
- [x] CHK-E6 proves existing Tailscale Serve entries survive deployment and
      rollback unchanged.
- [x] `docs/dreaming-live-evidence-audit.md` satisfies CHK-E8, including a
      measured review-backlog capacity conclusion and named follow-ups.
- [x] Existing estate mutation, receipt, dashboard containment, installer,
      self-test, and watchdog checks pass.
- [x] A live-proof receipt covers CHK-E6 and CHK-E7 on the reviewed candidate.
- [x] Dual implementation review has no unresolved in-scope must-fix finding.
- [x] Final installed two-host proof passes on the reviewed tree.
- [x] The design, implementation, audit, and proof references are committed
      locally; nothing is pushed.

Completion evidence:

- Deterministic checks, installed self-test, and final activation:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/evidence-completeness-live-proof/installed-selftest-enable-eb62266.log`
- CHK-E6, CHK-E7, final two-host identity, and governance ownership:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/evidence-completeness-live-proof/CHK-E6-E7-live-proof.md`
- Adversarial real-user behavior validation:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/behavior-validation-evidence-completeness-206e5bb/REPORT.md`
- Dashboard producer and backlog-capacity audit:
  `docs/dreaming-live-evidence-audit.md`
