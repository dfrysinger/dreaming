# Dreaming dashboard

## Objective

Provide a private localhost dashboard that lets the owner verify Dreaming is
running reliably, burning down its dream backlog, producing useful skills, and
retaining inspectable evidence without creating another runtime authority.

## Lane

Critical.

The dashboard is systemic because it joins scheduler, review, skill,
evaluation, publication, and filesystem state into one user-facing surface. It
is critical because it serves retained transcript snapshots over HTTP. A path
escape, non-loopback bind, DNS-rebinding weakness, stale evidence claim, or
success-shaped parse failure could expose private content or misrepresent the
state of a fail-closed learning system.

Rollback and the evidence that proves this boundary fails closed are specified
below.

## Interface contract

The approved interface is
[`docs/prototypes/dreaming-dashboard.html`](prototypes/dreaming-dashboard.html).
Its decisions comment and five primary screens are the product contract:

- Overview answers whether Dreaming runs successfully and regularly, whether
  the backlog is shrinking, which skills were learned recently, and how current
  evaluations perform.
- Activity presents scheduled executions as parent cards with ordered
  consolidate, roll, and prune passes. On-demand dream reviews and evaluation
  runs remain separate top-level executions.
- Dreams is a searchable, filterable, sortable, paginated catalog that uses
  human-readable dream names when available.
- Learned skills is a scalable catalog. Skill details contain the skill text,
  evidence, publication state, known usage, and evaluation history.
- System reports service health, source health, actual storage use, retained
  state counts, and limits that really exist.

The prototype's sample values are illustrative. The implementation must show
`Unknown`, `Unavailable`, or an explicit unhealthy state when Dreaming does not
retain the requested fact. It must not fabricate usage, evaluation impact,
transcript anchors, capacity limits, run summaries, or historical values to
match the prototype.

## Non-goals

- Add a dashboard database, telemetry collector, event bus, metrics service,
  OpenTelemetry exporter, or second queue, ledger, evaluation, publication, or
  provenance authority.
- Mutate Dreaming state, skills, evaluations, schedules, publication targets,
  or source transcripts from the dashboard.
- Read unrestricted native transcript stores from an HTTP request.
- Recover exact historical evidence anchors through text matching or model
  inference.
- Infer real-world skill usage from evidence count, evaluation trials, skill
  text mentions, or publication targets.
- Make the dashboard remotely accessible, multi-user, cloud-hosted, or
  configurable to bind a non-loopback address.
- Add user accounts, remote authentication, TLS termination, or a general local
  web-service framework.
- Replace the existing CLI status, self-test, watchdog, or repair commands.
- Generalize the dashboard server for unrelated products or operating systems.
- Add write controls, live log streaming, raw native-session browsing, or
  unrestricted filesystem browsing.
- Delete snapshots or other Dreaming state when storage is full.
- Redesign Dreaming's evaluation policy. The dashboard reads and explains the
  existing per-candidate authority.

## Reuse contract

### Existing authorities

The dashboard reads these owners without changing their authority:

| Concern | Existing authority |
|---|---|
| Scheduled cadence and pass results | `cadence.json`, `runs/<run_id>.json`, and `ledger.jsonl` through `dreaming-state.py` semantics |
| Discovery and backlog | `discovery.json`, `queue.json`, `unsettled.json`, `review-ledger.json`, `review-attempts.json`, and `review-transactions.json` |
| Reviewed transcript | Immutable digest-named snapshots under `DREAMING_DATA_DIR/snapshots` |
| Learned skill content and history | The Git repository at `DREAMING_SKILLS_ROOT` |
| Skill evidence and routing | `.agent-created.json` validated by `evidence-envelope.py` |
| Evaluation result and currency | Evaluation receipts, certificates, authority documents, and latest pointers owned by `skill-evaluation.py` |
| Publication | Content-addressed bundles and the publisher ownership journal |
| Runtime health | LaunchAgent state, self-test receipt, watchdog receipt, activation generation, halt switch, and adapter diagnostics |
| Storage capacity | The host filesystem containing each configured Dreaming root |

The server must use shared parsing and validation helpers extracted from the
current owners where practical. It must not duplicate a weaker interpretation
of source revision, evaluation currency, evidence validity, or run status.

### New components

Add only the components that the existing command-line runtime does not
provide:

- `skills/skill-review/scripts/dreaming-dashboard.py`: a Python
  standard-library loopback HTTP server and read-only snapshot assembler.
- `skills/skill-review/assets/dashboard/`: static HTML, CSS, and JavaScript
  implementing the approved prototype without a build step or external assets.
- `skills/skill-review/assets/launchd/dashboard.plist.tpl`: a dedicated
  long-running LaunchAgent.
- Focused dashboard tests plus installer and self-test coverage.

The dashboard process is separate from the daily orchestrator because it is a
long-running read service, while the orchestrator is a bounded mutation owner.
The separation prevents HTTP lifecycle failures from changing scheduler or
writer-lock behavior.

## Hard invariants

1. The dashboard process binds only to a validated loopback address.
2. Every state-derived or API response requires the install-owned access token.
   Static application assets contain no private data and may load before
   authentication.
3. Requests with an unapproved `Host` or foreign `Origin` fail before any state
   or transcript read.
4. The server implements only `GET` and `HEAD`; every mutation method returns
   `405`.
5. The server writes no Dreaming state, skill, metric, cache, access log, or
   transcript artifact.
6. Bulk endpoints never contain transcript event text.
7. Transcript text is loaded only by the evidence or transcript routes after
   explicit navigation.
8. Transcript routes can open only an immutable snapshot whose identifier is a
   lowercase SHA-256 digest and whose resolved regular file remains inside the
   configured snapshot root.
9. Exact highlighting is shown only when the evidence anchor names event IDs
   present in the anchored immutable snapshot.
10. Historical evidence without an exact anchor is labeled unanchored. It
    never claims that displayed turns established the finding.
11. A malformed authority record, evidence envelope, run record, queue, ledger,
    snapshot, or ownership journal produces an explicit unavailable or
    unhealthy result. It never becomes an empty successful collection.
12. Skill evaluation is always attributed to an exact candidate. A changed
    skill cannot inherit a stale result.
13. Capability and encoded-preference evaluations are not collapsed into one
    universal score.
14. Pagination, filtering, and sorting operate on bounded summaries, never on
    transcript text.
15. Storage reporting shows measured bytes and real filesystem capacity. It
    does not imply per-category quotas that do not exist.
16. Full-disk and read failures remain visible. The dashboard never deletes
    evidence or reports a failed write as healthy.

## Data model changes

### Dream display name

Extend source identity with optional presentation metadata:

```json
{
  "display_name": "Debugging Copilot plugin installation"
}
```

Each native source adapter extracts the name from an existing title or summary
field in that source's supported metadata. Extraction must:

- avoid rereading or summarizing transcript text;
- normalize to Unicode NFC;
- replace control characters and line breaks with spaces;
- collapse repeated whitespace;
- limit the encoded UTF-8 value to 160 bytes without splitting a code point;
- return no value when the source has no supported title.

`display_name` is presentation metadata. It is persisted with queue, unsettled,
and ledger records. `dreaming-core.py` strips it from the identity object before
building an immutable reviewed snapshot. It is excluded from
`source_revision`, source event-stream digests, and the content-addressed
snapshot body. A title-only change must not trigger another review or produce
another immutable snapshot.

The API fallback is `Untitled dream · <short-id>`, where `<short-id>` is the
first eight visible characters of the opaque native identifier after replacing
non-displayable characters. The full native or qualified session ID is never a
primary label.

Existing records migrate lazily. Missing names use the fallback; no bulk native
transcript scan is required.

### Exact evidence anchor

Keep `.agent-created.json` at schema version 2 and add one optional,
forward-compatible object to each new evidence entry:

```json
{
  "transcript_context": {
    "schema_version": 1,
    "snapshot_sha256": "64-lowercase-hex-characters",
    "source_revision": "opaque-revision",
    "event_ids": ["opaque-event-id"]
  }
}
```

The anchor stores no path and no transcript text. `snapshot_sha256` is the
digest of the canonical snapshot JSON object that the existing runtime uses in
the immutable snapshot filename. It is not a hash of the newline-terminated
file bytes and is not the source event-stream digest. `event_ids` contains 1 to
20 unique `source_event_id` values in snapshot order.

The review-executor result contract gains `evidence_event_ids` for skill and
support-file outcomes. Before mutation, `dreaming-core.py` verifies that every
ID is present in the immutable reviewed snapshot, preserves snapshot order,
uses the existing canonical snapshot-object digest, and passes the validated anchor to
`evidence-envelope.py`. Missing, duplicate, unknown, or excessive IDs reject
the artifact result before skill or ledger mutation.

This contract is mandatory for new skill and support-file outcomes. The review
prompt version increments, and every executor fixture must prove that source
event IDs are available to the model and emitted in the structured result. For
one source revision, three consecutive `evidence-anchor-invalid` results move
the queue entry to `recovery-required`. Recovery-required entries remain
visible in backlog counts but do not consume later automatic review budgets
until an explicit repair or newer source revision makes them eligible.

The evidence helper accepts and validates the optional anchor while continuing
to read old schema-v2 envelopes. Historical entries remain valid without an
anchor. An exact context card contains the cited events plus at most two
preceding and two following retained events, clipped to the snapshot boundary.

### Parent execution identity

New scheduled review records may carry:

```json
{
  "parent_run_id": "opaque-orchestrator-run-id"
}
```

The orchestrator exports its run ID to each pass, and Dreaming copies it into
new review attempts, review ledger entries, review evidence, and evaluation
run metadata when those operations occur inside that pass. On-demand work has
no parent ID.

This field allows exact nesting under a scheduled execution. Historical
records without it remain separate or show aggregate pass status only. The
dashboard must not associate them by timestamp guessing.

## Read-only aggregation

### Consistent request snapshot

Every API request builds a bounded in-memory view:

1. Resolve configured roots once.
2. Open only an allowlisted set of files or directories beneath those roots.
3. Read each file with an upper bound appropriate to that store.
4. Validate its schema and type.
5. Record a response fingerprint from every validated owner used by that
   response, plus an endpoint-scoped pagination fingerprint from only the
   owners that can change the ordered list.
6. Return one response assembled from that view.

The process retains no cross-request data cache. Static assets may use ETags;
all API and HTML responses use `Cache-Control: no-store`.

List cursors are base64url-encoded canonical JSON containing the normalized
query, last stable sort tuple, and endpoint-scoped pagination fingerprint. A
malformed cursor returns `400 invalid_cursor`. If that pagination fingerprint
changed, the endpoint returns `409 stale_snapshot` and the client restarts from
the first page. The cursor is an opaque pagination token, not durable state.

For Dreams, the pagination fingerprint covers queue, unsettled, and review
ledger state only. For Learned skills, it covers the canonical skill tree plus
the current evaluation and publication pointers used in list ordering. An
unrelated cadence, run, log, or evaluation write that cannot change that list
must not invalidate its cursor.

### Dream catalog

The catalog is the union of queue, unsettled, and accepted review-ledger
records, grouped by `qualified_session_id`.

- The newest source revision is the visible dream.
- A newer queued revision replaces an older reviewed or superseded revision.
- `remaining` means the newest eligible revision is `queued`,
  `recovery-required`, or `migration-hold` and has no accepted ledger record.
- `completed` means the newest eligible revision has an accepted ledger record.
- `active` means the source remains unsettled and is not yet eligible.
- Historical superseded revisions are available only in a dream detail
  response, not as additional backlog units.

`migration-hold` is a visible blocked state inside the remaining backlog, not a
completed or hidden dream. `recovery-required` and `migration-hold` rows explain
why automatic review is not progressing.

Backlog history is reconstructed from retained timestamps:

- admission time is `queued_at`;
- completion time is the matching accepted `reviewed_at`;
- replacement time is the successor revision's `queued_at`.

At time `t`, a revision contributes one remaining dream when it was admitted by
`t`, was not accepted by `t`, and was not replaced by `t`. If a required
timestamp is absent, the affected historical interval is unavailable rather
than estimated.

### Activity

Scheduled cards come from validated `runs/<run_id>.json` records. Their pass
order is always consolidate, roll, prune. The API reports stored status,
timestamps, duration, reason, and exact child counts only when child records
carry the same `parent_run_id`.

When weekly maintenance was not scheduled, the API derives:

- the last successful weekly timestamp from cadence authority;
- the next stable weekly bucket boundary;
- whole days until due, rounded up.

On-demand dream reviews and evaluation runs are separate activity kinds.
Generic labels such as Learning, Discovery, or Run are not independent event
types.

### Learned skills

A learned skill is a current directory in `DREAMING_SKILLS_ROOT` with both a
valid `.agent-created` marker and a valid `.agent-created.json` envelope.
Invalid marked skills remain visible as unhealthy rows but contribute no
evidence or evaluation claims.

The catalog reports:

- skill name and current Git candidate identity;
- creation time from the envelope;
- UTF-8 byte count, line count, and whitespace-delimited word count for
  `SKILL.md`;
- evidence count and verified independent-task count;
- current evaluation status and measured effect, when authority is current;
- publication targets verified by the publisher journal;
- known usage count and last-used time only from an explicit runtime
  skill-load record.

Until Dreaming retains explicit interactive skill-load records, usage is
`{"known": false, "count": null, "last_used_at": null}`. Evaluation trials,
publication, evidence, and text mentions do not count as usage.

Skill text is served as escaped text, never injected as HTML.

### Evaluation portfolio

Evaluation detail resolves the exact skill candidate through
`skill-evaluation.py` authority rules. The dashboard reads immutable receipts
and current pointers, but does not decide authority itself.

Portfolio capability values use only trustworthy, comparable
`capability_uplift` gate-profile results:

1. For each skill candidate, calculate intended candidate completion percentage
   and matched control completion percentage from valid completed trials.
2. Give each skill equal weight, regardless of its number of cases or trials.
3. Macro-average candidate and control percentages separately.
4. Exclude inconclusive, unavailable, malformed, non-gate, or incomparable
   results and report their counts.

Encoded-preference results are reported as pass, regression, inconclusive, or
unevaluated counts. They do not enter the capability percentage.

For each new gate-profile aggregate, `skill-evaluation.py` writes a compact,
content-addressed portfolio receipt under the versioned evaluation state root.
It records per intended case and executor:

- candidate and control valid trial counts;
- candidate and control successful trial counts;
- evaluation class;
- completion status and exclusion reason when the pair is not comparable.

The portfolio receipt contains no prompts, traces, native logs, arbitrary
paths, or transcript text. It is written for pass, regression, and
inconclusive outcomes and binds the immutable aggregate receipt digest. It does
not add a key to the existing strict aggregate schema. Existing aggregate
receipts and validators remain byte-for-byte compatible. The dashboard never
follows a `result_dir` or reads trial workspaces.

The evaluation owner also writes an immutable transition record for every
gate-profile outcome and explicit authority revocation:

```json
{
  "schema_version": 1,
  "effective_at": "RFC3339 timestamp",
  "skill_key": "opaque-skill-key",
  "candidate_id": "sha256:...",
  "status": "pass | regression | inconclusive | revoked",
  "authority_sha256": "sha256:... or null",
  "aggregate_receipt_sha256": "sha256:... or null",
  "portfolio_receipt_sha256": "sha256:... or null"
}
```

Transition records live under the versioned evaluation state root, are
content-addressed, and are written before the mutable latest pointer changes
or is removed. A pass transition requires a current authority, aggregate
receipt, and portfolio receipt for the same candidate. Regression and
inconclusive transitions require the sealed aggregate and portfolio receipts
but have no authority reference. Revocation has no authority and may omit both
receipts when it records invalidation without a new evaluation. These records
are evaluation authority history, not dashboard telemetry.

The historical capability chart selects the latest validated transition
effective by each time bucket and reads metrics only when that transition is
`pass` and its candidate matches the skill candidate in Git at that bucket.
Regression, inconclusive, revoked, candidate-mismatch, and legacy intervals
without transition or portfolio receipts are unavailable, not backfilled or
inferred. The skill-count series is
reconstructed from the canonical skills Git history by counting agent-created
skills at the selected commit. No dashboard-owned metric history store is
added.

### Storage

Category sizes are bounded, non-following filesystem walks over approved
Dreaming roots. The API reports bytes and item counts for state, immutable
snapshots, evaluations, bundles, learned skills, and logs.

Per-category bars are prohibited because these categories have no quotas. The
only capacity bar represents the actual filesystem volume. If configured roots
occupy different devices, the API returns one capacity row per distinct device.
Snapshot storage also reports the configured 100 KB event-payload limit for
each retained snapshot. There is no aggregate retention ceiling or automatic
cleanup claim.

## HTTP service

### Process and configuration

The server uses `ThreadingHTTPServer` with bounded request threads, request
header limits, response size limits, and per-request timeouts. It serves from
static files bundled in the repository and never executes templates supplied
by state.

Installed configuration:

```text
DREAMING_DASHBOARD_ENABLED=1
DREAMING_DASHBOARD_HOST=127.0.0.1
DREAMING_DASHBOARD_PORT=47673
```

Only `127.0.0.1` and `::1` are accepted host values. The installed default is
`127.0.0.1`. An empty, wildcard, hostname, interface, or non-loopback address
is a startup refusal.

The installer creates a 256-bit random access token at
`DREAMING_STATE_DIR/dashboard/access-token` with mode `0600`. The server refuses
to start if the token is missing, malformed, symlinked, group-readable, or
world-readable.

`scripts/install.sh dashboard-open` opens a tokenized bootstrap URL without
printing the token. `scripts/install.sh dashboard-url` prints it only on an
explicit command. `status` reports the non-secret base URL and service state.

### Browser access boundary

`scripts/install.sh dashboard-open` opens:

```text
http://127.0.0.1:47673/#access_token=<token>
```

URL fragments are not sent in HTTP requests. The static application reads the
fragment once, stores the token in `sessionStorage`, and immediately removes
the fragment with `history.replaceState`. Browser session storage is scoped to
the exact origin, including the port. The application sends an `Authorization`
header with the `Bearer` scheme and the access token on every API request. The
server compares it in constant time and rejects credentials supplied only
through cookies or query parameters.

The unauthenticated HTML, CSS, and JavaScript contain no state-derived data.
Every API route, including health, requires the bearer token. A browser opened
without the fragment shows only instructions to run `dashboard-open`.

The server:

- accepts only an allowlisted `Host` matching its bound loopback address and
  configured port;
- accepts an absent `Origin` or the exact same origin only;
- sends no CORS allow headers;
- sends `Content-Security-Policy: default-src 'self'; script-src 'self';
  style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src
  'none'; base-uri 'none'; frame-ancestors 'none'`;
- sends `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy: camera=(), microphone=(), geolocation=()`, and
  `X-Frame-Options: DENY`;
- escapes every state-derived string before DOM insertion and uses text nodes
  rather than `innerHTML`.

The trust boundary is the local OS user who can read the token file and the
exact loopback origin holding the token in session storage. The dashboard does
not claim protection from another process already running as that user,
because that process can read the underlying Dreaming files. Another loopback
service running under a different user or container does not receive the
credential through browser cookie leakage.

### API routes

All JSON responses use:

```json
{
  "schema_version": 1,
  "generated_at": "RFC3339 timestamp",
  "source_fingerprint": "sha256:...",
  "data": {}
}
```

Errors use:

```json
{
  "schema_version": 1,
  "error": {
    "code": "stable_machine_code",
    "message": "non-sensitive explanation",
    "sources": ["named-state-owner"]
  }
}
```

Routes:

| Route | Contract |
|---|---|
| `GET /api/v1/overview` | Runtime health, scheduled reliability, backlog totals and burn rate, skill count, latest skills, evaluation coverage, capability macro-averages, skill-history series, and backlog series |
| `GET /api/v1/task-opportunities` | Read-only projection of validated profile receipts, catalog-audit dispositions, canonical occurrences, candidate recurrence, and immutable pass-accounting terminals, bounds, and deferred backlog |
| `GET /api/v1/activity?kind=&cursor=&limit=` | Scheduled, on-demand review, and evaluation executions with nested scheduled passes |
| `GET /api/v1/dreams?status=&source=&result=&sort=&query=&cursor=&limit=` | Bounded dream summaries only |
| `GET /api/v1/dreams/<qualified-id>` | One dream's revisions and review metadata, without transcript text |
| `GET /api/v1/skills?status=&evaluation=&sort=&query=&cursor=&limit=` | Scalable learned-skill catalog |
| `GET /api/v1/skills/<skill-name>` | Skill metadata, escaped skill text, publication, current evaluation, evaluation history, and evidence previews |
| `GET /api/v1/skills/<skill-name>/evidence?cursor=&limit=` | Evidence cards and bounded anchored context for the explicitly opened evidence page |
| `GET /api/v1/transcripts/<snapshot-sha256>` | The complete retained normalized snapshot for an explicit Open transcript action |
| `GET /api/v1/system` | Service, adapter, state-owner, storage, capacity, limit, self-test, watchdog, activation, and halt status |
| `GET /api/v1/health` | Non-sensitive process readiness; authenticated like every other API route |

Default and maximum page sizes are 25 and 100. Search is case-insensitive over
bounded names and summaries only. Skill names must match the existing
lowercase-hyphenated skill-name grammar. Qualified dream IDs are decoded as one
path segment and matched as opaque values; they are never converted to paths.

## Frontend behavior

The frontend is a static single-page application with progressive navigation:

- URLs use real hash or history state so refresh and back navigation preserve
  the selected screen.
- Overview and list routes load without transcript payloads.
- Opening a skill fetches only that skill.
- Opening its evidence page fetches evidence context.
- Evidence-preview links include a stable evidence anchor and scroll to the
  correct card after the page has loaded.
- Open transcript fetches the full retained immutable snapshot in the same
  page.
- Relative times render `Now`, minutes, hours, and days. Full run headings use
  local time in `Tuesday, May 14 at 7:30pm` form.
- Missing values have explicit copy. Empty, loading, unhealthy, partial, and
  unavailable states remain visually distinct.
- Tables remain keyboard navigable and preserve visible focus.
- The layout follows the approved desktop-first prototype and remains usable at
  narrower browser widths without hiding data behind hover-only interactions.

## Installer and LaunchAgent lifecycle

Add `dashboard` to the installer-owned `NEW_KINDS` set. Its LaunchAgent:

- starts after installation and at login;
- keeps the read-only server alive;
- receives only the configured Dreaming roots, port, loopback host, token path,
  and static-asset root;
- writes bounded startup and crash diagnostics to
  `$HOME/Library/Logs/Dreaming/dashboard.log`, outside the configured Dreaming
  state, data, and skills roots, without
  request URLs, query strings, response bodies, skill text, evidence summaries,
  or transcript text;
- uses the same repository and runtime configuration generation as the other
  installed jobs.

`install` creates or preserves the access token, renders and validates the
plist, bootstraps the service, and leaves the mutation halt semantics
unchanged. `selftest` verifies the process identity and authenticated health
route through a token read from the protected file. `enable` does not control
dashboard readability; the UI accurately shows the mutation halt state.

`uninstall` boots out and removes the owned dashboard plist. It retains
Dreaming state, skills, snapshots, and the token for reversible reinstall.
Rollback restores the exact pre-install LaunchAgent set from the migration
backup. A dashboard plist absent from that backup remains absent after
rollback.

## Failure model

| Failure | Required behavior |
|---|---|
| Port already in use | LaunchAgent remains failed and status names the bind failure; it does not choose another interface or port silently |
| Non-loopback host configured | Process refuses before opening a socket |
| Missing or weak token file | Process refuses before opening a socket |
| Invalid Host or foreign Origin | Request returns `403` before state access |
| Missing or invalid bearer token | Request returns `401` without state details |
| Credential supplied by cookie or query parameter | Request returns `401`; only the exact-origin authorization header is accepted |
| Unsupported method | Request returns `405`; no body is parsed and no state is touched |
| Malformed shared state | Affected API returns explicit unavailable or unhealthy data and names the state owner |
| State changes during pagination | Request returns `409 stale_snapshot`; client restarts the list |
| Snapshot digest malformed or absent | Transcript request returns `404` or `422` without probing arbitrary paths |
| Snapshot is a symlink, non-regular file, oversized, malformed, or digest-mismatched | Transcript request fails closed and reports snapshot corruption |
| Evidence event ID absent from anchored snapshot | Evidence is shown as invalid anchor with no highlighted claim |
| Historical evidence has no anchor | Card is labeled historical and unanchored; no exact-turn claim is made |
| Evaluation pointer is stale | Skill is labeled evaluation due or stale; no prior score is treated as current |
| Git history cannot be read | Current skill data may remain available, but the historical series is unavailable |
| Filesystem fills | Existing write owners fail normally; System shows the real capacity problem; dashboard deletes nothing |
| One adapter is unavailable | Source health is unavailable while unrelated validated state remains visible |
| Dashboard process crashes | Daily Dreaming and all mutation owners continue independently |

## Migration and rollback

Migration is additive:

1. Land readers and tests for optional display names, evidence anchors,
   evaluator-owned portfolio receipts, and immutable authority transitions.
2. Extend source adapters, review results, and evaluation writers to write
   those fields for new records.
3. Add the read-only server and static frontend.
4. Add installer, LaunchAgent, self-test, status, and open-URL integration.
5. Install while the existing halt switch is active, validate the authenticated
   loopback service, then preserve the existing enable sequence.

No historical envelope rewrite, transcript rescan, queue rewrite, or evaluation
backfill is required.

Rollback:

1. Boot out the dashboard LaunchAgent.
2. Restore the prior installer and source/review writers.
3. Retain optional display names and transcript-context anchors as inert
   fields. Retain portfolio and authority-transition receipts in a new
   versioned directory that the previous evaluator does not enumerate. Restore
   the previous latest authority pointers without rewriting any old aggregate
   receipt.
4. Retain the access token, snapshots, run records, evidence, and evaluation
   records.
5. Run the previous self-test before re-enabling mutation.

Rollback never deletes private evidence. It removes the HTTP exposure by
stopping the server and removing its owned plist.

## Observable acceptance criteria

**AC1. Private startup:** A normal install starts exactly one dashboard process
on the configured loopback address, and an authenticated health request
identifies the installed candidate.

**AC2. Network refusal:** Non-loopback configuration, wildcard binding,
unapproved Host, foreign Origin, missing token, permissive token mode, and
unauthenticated API requests all fail before a Dreaming state read.

**AC3. Read-only operation:** Browsing every screen, paginating lists, opening
evidence, and opening a transcript changes no file below the configured
Dreaming state, data, or skills roots.

**AC4. Honest Overview:** Overview reports scheduler health, current backlog,
burn rate, learned-skill count, latest skills, evaluation coverage, comparable
candidate and control capability percentages, and separate preference counts.
Unavailable inputs remain explicitly unavailable.

**AC5. Scalable dreams:** The Dreams page can search, filter, sort, and paginate
at least 1,750 fixture dreams without loading transcript text or losing stable
pagination when state is unchanged.

**AC6. Dream naming:** New Copilot, Claude, and Codex fixture sessions retain
bounded sanitized display names; missing historical names use the documented
fallback without changing source revision.

**AC7. Activity hierarchy:** Scheduled executions show the fixed pass order,
full local date and time, stored pass outcomes, and weekly-not-due details.
Only records with exact parent IDs appear nested.

**AC8. Scalable skills:** The Learned skills page paginates at least 150 fixture
skills and reports content size, creation time, evidence, current evaluation,
publication, and honest usage availability.

**AC9. Candidate-specific evaluation:** Skill details reject stale authority,
keep encoded-preference results separate, and calculate capability effects from
matched trustworthy trials sealed into evaluator-owned portfolio receipts
only. Regression, inconclusive, revoked, candidate-mismatch, and legacy
intervals without validated authority transitions remain unavailable.

**AC10. Exact evidence:** New anchored evidence opens the correct immutable
snapshot, highlights only cited event IDs, and includes bounded neighboring
events. Historical unanchored evidence carries no exact-support claim.

**AC11. Transcript containment:** Open transcript can read only a valid retained
snapshot selected by its digest. Traversal, symlink, malformed, oversized, and
digest-mismatch fixtures are refused.

**AC12. Honest storage:** System reports measured category sizes, the real
filesystem capacity for each device, the configured 100 KB snapshot event
limit, and no fictional category quota or cleanup policy.

**AC13. Failure visibility:** Corrupt queue, run, evidence, evaluation,
publication, and snapshot fixtures produce explicit scoped errors rather than
empty successful data or a server crash.

**AC14. Lifecycle:** Install, reinstall, status, self-test, dashboard-open,
uninstall, and rollback manage only the owned server, plist, and token while
preserving all Dreaming evidence and mutation behavior.

**AC15. Approved interface:** A live browser run covers Overview, Activity,
Dreams, Learned skills, skill detail, evidence, transcript, and System and
matches the approved information architecture and settled interaction
decisions.

## Deterministic check contract

### Security and privacy checks

| Check | Protects | Setup and transition | Pass signal | Failure proves |
|---|---|---|---|---|
| Loopback bind test | AC1, AC2 | Start with `127.0.0.1`, then wildcard, hostname, and non-loopback fixtures | Loopback serves; every other address exits before listen | The bind guard can expose the service beyond the intended host |
| Token file test | AC2 | Exercise absent, symlinked, short, group-readable, world-readable, and valid `0600` token files | Only the valid token starts the server | The local-user access boundary can be bypassed |
| Request boundary test | AC2 | Send valid and invalid Host, Origin, bearer, cookie-only, query-token, and HTTP method combinations while state reads are instrumented | Only the exact-origin bearer succeeds; invalid requests return `401`, `403`, or `405` with zero state reads | Request validation occurs too late, accepts a cross-origin caller, or leaks authority across loopback ports |
| Read-only tree test | AC3 | Hash metadata and content for state, data, and skills roots; browse all APIs; hash again | Before and after manifests match exactly | The dashboard became a mutation or cache owner |
| Transcript containment test | AC10, AC11 | Request valid digest plus traversal, uppercase, short, symlink, directory, oversized, malformed, and digest-mismatch fixtures | Only the exact regular immutable snapshot is returned | Private path containment or integrity validation is incomplete |
| Response hardening test | AC2, AC10 | Fetch HTML, assets, APIs, evidence, and transcript routes | Required CSP, no-store, no-CORS, frame, referrer, and MIME headers are present; query token is absent from logs | Browser isolation or secret-handling assumptions are false |

The loopback, token, request-boundary, read-only-tree, and transcript-containment
tests must exist before the frontend is connected to real state. They constrain
the critical boundary rather than testing a completed UI.

### Data contract checks

| Check | Protects | Setup and transition | Pass signal | Failure proves |
|---|---|---|---|---|
| Display-name adapter fixtures | AC6 | Run all three adapters over valid, missing, multiline, control-character, Unicode, and oversized titles | Sanitized names satisfy the byte bound; revisions and snapshot filename digests remain unchanged when only title changes | Names can leak unsafe text, cause spurious re-review, or change immutable transcript identity |
| Evidence-anchor schema test | AC10 | Validate old envelopes and new anchors with valid and invalid snapshot digests, revisions, counts, duplicates, and event IDs | Old envelopes remain readable; only valid anchors write | Compatibility or exact-citation integrity is broken |
| Snapshot-anchor digest test | AC10, AC11 | Write the existing canonical snapshot object with its newline-terminated file representation and create an anchor | The canonical object digest resolves the filename and file integrity validation succeeds | The design confused object identity with file-byte hashing |
| Review admission test | AC10 | Return skill results with valid, missing, duplicate, unknown, and excessive evidence IDs across repeated attempts | Only the valid result mutates skill, evidence, and ledger state; the third invalid result becomes recovery-required and leaves later automatic budgets | The model can forge or omit support, or one bad contract can stall every later run |
| Dream reduction fixtures | AC4, AC5 | Feed queue, unsettled, review, replacement, migration-hold, and timestamp fixtures | Current totals and historical backlog match the hand-calculated table; migration-hold remains visible and remaining | Backlog counts revisions incorrectly or hides blocked work |
| Activity hierarchy fixtures | AC7 | Combine scheduled run records and child events with matching, missing, and wrong parent IDs | Only exact matches nest; weekly due text matches cadence | The UI invents execution relationships |
| Evaluation aggregation fixtures | AC4, AC9 | Mix capability, preference, stale, incomplete, advisory, differently sized suites, legacy aggregates, and pass-to-regression, pass-to-inconclusive, and revocation transitions for one candidate | Legacy aggregate receipts still validate unchanged; portfolio receipts bind their aggregate digest; transition status selects metrics only for a matching pass; equal-skill macro averages and coverage counts match expected values; all invalid intervals remain unavailable | The portfolio score breaks legacy authority, pools unlike evidence, follows raw trial paths, or applies stale authority after a non-passing transition |
| Skill catalog fixtures | AC8 | Build 150 valid skills plus malformed markers, envelopes, pointers, and publisher records | Pagination is complete; unhealthy rows remain explicit; usage remains unknown without load records | The catalog hides corruption or invents metadata |
| Storage fixtures | AC12 | Measure files, symlinks, multiple roots, and mocked devices | Sizes exclude symlink targets and capacity rows match distinct devices | Storage reporting escapes roots or implies false limits |
| Corrupt-source matrix | AC13 | Corrupt each state owner independently | The affected field or route is unavailable with a stable code; unrelated validated data remains identified | Parse errors become success-shaped or crash the service |

### HTTP and frontend checks

| Check | Protects | Setup and transition | Pass signal | Failure proves |
|---|---|---|---|---|
| API schema test | AC4-AC13 | Call every route against complete, empty, partial, and corrupt fixtures | Responses validate against schema version 1 and use stable errors | Frontend and server contracts can drift silently |
| Pagination scale test | AC5, AC8 | Load 1,750 dreams and 150 skills; traverse all pages under each supported sort | Every stable ID appears exactly once; no response contains transcript text | Pagination loses rows, duplicates rows, or leaks content |
| Stale cursor test | AC5, AC8 | Fetch page one, mutate fixture state, request page two | Server returns `409 stale_snapshot` and client restarts | Pages can silently combine different state generations |
| Cursor isolation test | AC5, AC8 | Traverse Dreams while adding an unrelated run record, and traverse skills while changing an unrelated log | Pagination continues without `409` and every stable ID appears once | A global fingerprint makes active-system pagination non-terminating |
| Static safety test | AC10, AC15 | Put HTML and script payloads in every state-derived display field | Payloads render as text and execute nothing | State content can become browser code |
| Relative-time test | AC7 | Freeze time across now, minute, hour, day, and full-run-title boundaries | Copy matches the approved formats | Time labels lose required granularity |
| Frontend route test | AC15 | Navigate all primary and detail routes with browser back, refresh, evidence anchor, and transcript open | Screen and anchor state remain correct with no console or network errors | The approved interaction flow is incomplete |

### Lifecycle and live checks

| Check | Protects | Setup and transition | Pass signal | Failure proves |
|---|---|---|---|---|
| Installer fixture suite | AC1, AC14 | Install, reinstall, status, self-test, open URL, uninstall, and rollback with fake launchctl | Exactly one owned dashboard plist/process is managed and data remains | Lifecycle ownership or rollback is unsafe |
| LaunchAgent live canary | AC1, AC14 | Install the current tree, bootstrap the real plist, and call authenticated health | Process identity, candidate identity, loopback socket, and health response agree | Fixture success does not represent the installed service |
| Systemic browser proof | AC3-AC15 | Use the real installed dashboard against retained local Dreaming state and exercise the complete approved flow | A live-proof receipt records every checkpoint, screenshots, API evidence, no state mutation, and no browser errors | The feature has not been proved at its actual boundary |
| Negative live boundary proof | AC2, AC11 | Against the installed server, attempt unauthenticated, foreign-Origin, invalid-Host, cookie-only, query-token, cross-port, and invalid-snapshot requests | Each request fails with the specified status and no private response content; another loopback port receives no dashboard credential | The critical localhost boundary is not fail-closed |

## Dreaming Dashboard Definition of Done

- The approved five-section interface is implemented with no external runtime
  or frontend dependency.
- New source records retain bounded display names, and new skill evidence
  retains validated immutable-snapshot event anchors.
- Every hard invariant has an executable check, including the pre-frontend
  critical-boundary guards.
- Targeted dashboard, adapter, evidence, evaluation, daemon, and installer tests
  pass.
- The real LaunchAgent serves exactly one authenticated loopback dashboard from
  the current candidate.
- The live browser proof passes every acceptance flow on retained local
  Dreaming state, including evidence highlighting and Open transcript.
- The negative live boundary proof shows unauthorized and path-invalid
  requests receive no private content.
- A before-and-after manifest proves the complete live browsing flow writes
  nothing below Dreaming's state, data, or skills roots.
- Dual implementation review has no unresolved in-scope must-fix finding.
- The systemic final end-to-end proof passes on the reviewed tree.
- Install, reinstall, status, self-test, uninstall, and rollback preserve
  existing Dreaming mutation, evidence, and recovery behavior.
- The implementation and this work order are committed locally on
  `feature/multi-cli-dreaming`; nothing is pushed.
