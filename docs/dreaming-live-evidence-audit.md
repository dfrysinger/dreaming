# Dreaming live-evidence audit

## Scope and evidence levels

This audit covers every dashboard API section and the scheduled-run,
discovery, review, evaluation, publication, estate, and watchdog claims shown
or implied by the installed dashboard.

Evidence levels:

- **Live:** observed from the installed Mac mini dashboard or its retained
  state.
- **Live and fixture-certified:** observed live and covered by deterministic
  contract tests.
- **Fixture-only:** the route or claim is tested, but the installed state has
  no valid example.
- **Absent:** no dashboard producer exists for the claim.

## API inventory

| API section | Producer | Persisted source | Installed example | Freshness, completeness, and backlog meaning | Certification |
| --- | --- | --- | --- | --- | --- |
| `GET /api/v1/health` | `DashboardData.health` | Activation generation, halt and publication-recovery switches, latest orchestrator run | `healthy`, generation `20260818T083611Z-install-50172` | `healthy` means the latest retained run is `ok` or `skipped`; this route does not enforce run age | Live and fixture-certified |
| `GET /api/v1/overview` | `DashboardData.overview` | Queue, unsettled map, review ledger, learned-skill Git tree, evaluation transitions, candidate records, estate receipt | 1,382 remaining dreams, 404 completed, 6 active, 6 learned skills, 93 enabled estate capabilities | Aggregates the current retained sources. Dream backlog is the latest queued revision not closed by a matching ledger record | Live and fixture-certified |
| `GET /api/v1/estate` | `DashboardData.estate` and `_estate_usage` | Sealed census and usage current pointers plus immutable receipts; estate-action state and ledger | 107 physical instances, 93 enabled capabilities, 5 plugin packages, 61 governance records | Census is fresh for 24 hours. Corpus, attribution, current-run budget, failures, and pending work are independent facts | Live and fixture-certified |
| `GET /api/v1/activity` | `DashboardData.activity` | Orchestrator run records, review attempts, evaluation authority transitions | 319 retained activity rows; recent rows include scheduled runs and attributed reviews | Ordered by retained start time. Unlinked review attempts remain separate instead of being assigned to a run | Live and fixture-certified |
| `GET /api/v1/dreams` | `DashboardData.dreams` | `queue.json`, `unsettled.json`, `review-ledger.json` | 1,878 known session revisions represented as current dream rows | `remaining` means the latest revision is queued or held; `completed` requires a matching review-ledger entry; active unsettled work is separate | Live and fixture-certified |
| `GET /api/v1/dreams/{id}` | `DashboardData.dream_detail` | Same dream sources, including all retained revisions and reviews for one session | Live Copilot session detail returned with revisions and reviews | Detail is a current projection over retained queue and ledger state | Live and fixture-certified |
| `GET /api/v1/skills` | `DashboardData.skills` | Dreaming-managed skill Git tree, agent-created envelopes, evaluation authority, publication journals | 6 current learned skills; all 6 report `not_evaluated`; all 6 publish to Claude and the MacBook Copilot target | This catalog is only Dreaming-managed learned skills, not the 93-skill enabled estate. Its usage field has no producer and remains unknown | Live and fixture-certified, except usage is absent |
| `GET /api/v1/skills/{name}` | `DashboardData.skill_detail` | Skill text and agent-created envelope plus catalog projection | `bounded-deep-research` returned 2 evidence records | Candidate identity is recalculated from current files. Evidence summaries come from the retained envelope | Live and fixture-certified |
| `GET /api/v1/skills/{name}/evidence` | `DashboardData.evidence` | Agent-created envelope and retained snapshot files | `bounded-deep-research` returned 2 historical-unanchored evidence records | An exact anchor requires a valid retained snapshot and matching source revision; absence remains historical-unanchored or invalid | Live and fixture-certified |
| `GET /api/v1/candidates` | `DashboardData.candidates` | Conservative candidate records and immutable candidate packages | Installed list is valid and empty | Empty means no retained shadow records, not that candidate discovery is universally complete | Live and fixture-certified |
| `GET /api/v1/candidates/{id}` | `DashboardData.candidate_detail` | One candidate record and its immutable package | No installed candidate exists | The route validates lifecycle, recurrence, freshness, evaluation, and shadow-only authority when a record exists | Fixture-only |
| `GET /api/v1/transcripts/{digest}` | `DashboardData.transcript` | Digest-addressed retained snapshot | No valid installed snapshot can currently be opened from retained skill evidence | Only event identity and kind are public. Transcript text remains private. A missing or invalid digest is not treated as an empty transcript | Fixture-only |
| `GET /api/v1/system` | `DashboardData.system` | Installed roots, measured directory trees, filesystem usage, adapter snapshot limit | 9 measured categories, 1 filesystem, 100,000-byte per-snapshot limit | Storage is measured at request time. There is no aggregate retention quota or automatic cleanup | Live and fixture-certified |

## Visible claim producers

| Claim area | Producer and source | Installed result | Certification |
| --- | --- | --- | --- |
| Scheduled reliability and pass order | Orchestrator run records under the configured orchestrator state root | Latest retained run is `ok`; scheduled rows expose consolidate, roll, and prune status and reason | Live and fixture-certified |
| Discovery | `dreaming-core` adapters write queue and unsettled state; the core run report records source watermarks and outcomes | Copilot is the configured discovery source; 1,399 queue entries are currently `queued` and 6 sessions are unsettled | Live and fixture-certified |
| Reviews | Review attempts record every execution; the review ledger records accepted terminal results; `parent_run_id` links scheduled work | 544 attempts, including 407 `ok` and 137 `failed-before-mutation`; 404 ledgered reviews | Live and fixture-certified |
| Evaluations | Evaluation authority transitions and sealed portfolio receipts | No current learned skill has an active comparable evaluation; capability percentages remain unknown rather than zero | Live state, fixture-certified behavior |
| Publication | Local publisher ownership journal plus receiver-bound remote publication summary | All 6 learned skills are recorded for Claude and MacBook Copilot; no other target is claimed | Live and fixture-certified |
| Estate inventory and governance | Receiver-bound census receipt, usage receipt, estate-action authority state, and recommendation ledger | Census is current and complete for its bounded contexts; 61 governance decisions or actions are retained | Live and fixture-certified |
| Watchdog health | No dashboard method reads watchdog output or a watchdog receipt | The dashboard's `healthy` label reflects the latest run status, not an observed watchdog result | Absent |
| Learned-skill usage | `DashboardData.skill_rows` emits `known: false` without reading the estate usage receipt | Every learned-skill usage value is `Unknown` even though estate usage now has a live producer | Absent |

## Transcript usage coverage

The installed receiver-bound catch-up discovered 2,237 direct-child Copilot
sessions totaling 20,183,511,561 bytes.

- 2,231 sessions and 18,825,953,883 bytes are indexed.
- Six sessions and 1,357,557,678 bytes remain pending because they were
  modified inside the 300-second quiet period.
- Two indexed sessions report `usage_session_invalid_skill_name`. Their opaque
  session IDs are retained in the usage receipt.
- The largest indexed session is 1,758,979,878 bytes, proving that the
  oversized-session exception streamed and checkpointed the 1.64-GiB file.
- Exact fingerprint matches produced repeated zero-byte, zero-session runs
  after ordinary catch-up work was exhausted.

The productive catch-up advanced from 284,252,354 indexed bytes to
18,825,953,883 indexed bytes over 360.55 seconds between retained receipts.
That is 51.4 MB/s of observed catch-up progress. During the following
95.49-second steady interval, actively changing transcript bytes grew by
777,517 bytes, or 8.1 KB/s, while unchanged sessions consumed no parsing
budget. Observed catch-up capacity therefore exceeded observed arrival by more
than 6,300 times.

Corpus completeness remains false while the six changing sessions are inside
the quiet period. Attribution completeness remains false while observed names
lack exact current mappings. Neither condition is presented as zero usage.

## Historical usage attribution

Static aliases are accepted only when every rename step is an immutable Git
rename and the final name maps to exactly one enabled capability. Eighteen
rename records verify ten historical aliases:

| Historical name | Current capability |
| --- | --- |
| `architecture-guardrails` | `guardrails` |
| `autopilot-brief` | `unattended-run` |
| `context-hygiene` | `self-compact` |
| `feature-development-loop` | `development-loop` |
| `gated-pr-merge` | `development-loop` |
| `gaw-development` | `gaw` |
| `loop` | `microsoft-loop` |
| `nexus-dev` | `nexus-gotchas` |
| `prototype-reference-integration` | `absorb-poc` |
| `upstream-contribution` | `upstream-pitch` |

Twenty-three observed names remain unresolved:

| Observed name | Reason it is not attributed |
| --- | --- |
| `architecture-fitness-functions` | It was promoted between repositories under the same name, but no exact rename connects it to a current enabled capability. |
| `caveman` | Git records archive and retirement, not a unique replacement. |
| `create-canvas` | No rename record in the reviewed personal or shared skill histories connects it to a current capability. |
| `design` | No reviewed Git rename connects it to `design-doc` or another unique current capability. |
| `dfrysinger-dreaming-shared:dual-review` | This is a namespaced observed invocation, not a Git-renamed skill path; prefix normalization would be an inference. |
| `e2e-test-author` | No reviewed Git rename connects it to a current capability. |
| `explaining-to-users` | The retained skill history does not contain an exact rename to `explain`; conceptual overlap is insufficient. |
| `gh-auth-macos` | It moved between repositories and was later retired without an exact rename to a current capability. |
| `goals` | No reviewed Git rename or unique current target exists. |
| `implement` | No reviewed Git rename or unique current target exists. |
| `legible-communication` | It was split into multiple skills and archived, so no unique target exists. |
| `parallelize` | No reviewed Git rename or unique current target exists. |
| `plan` | No reviewed Git rename or unique current target exists. |
| `problem-framing` | No reviewed Git rename or unique current target exists. |
| `prompt-prose-reviewer` | No reviewed Git rename or unique current target exists. |
| `questions` | No reviewed Git rename or unique current target exists. |
| `research` | No reviewed Git rename connects this historical skill name to a current enabled skill. |
| `reviewer-protocol` | No reviewed Git rename or unique current target exists. |
| `rust-coding-skill` | Several current Rust skills exist, but no exact rename selects one target. |
| `secret-leak-remediation` | Git records explicit archive and hard removal, not a replacement. |
| `structure` | No reviewed Git rename or unique current target exists. |
| `using-qrspi` | No reviewed Git rename or unique current target exists. |
| `write-a-skill` | It coexisted with `writing-great-skills` before retirement, so the history does not prove a rename. |

## Review backlog capacity

`deferred_reviews` counts each queue entry whose status is `queued` after the
run has already attempted the configured review limit. It excludes reviewed,
deleted, superseded, active, and recovery-held entries. It is a per-run
deferral count, not a lifetime total.

The installed limit is 25 review attempts per run. The LaunchAgent schedules
one run daily at 09:15 local time, giving nominal scheduled capacity of 25
reviews per day.

For the bounded 24-hour sample ending
`2026-08-18T03:32:07+00:00`:

- 97 queue revisions arrived, or 97 per day.
- 78 of those arrivals were still pending at the sample boundary.
- 148 reviews completed because several manual runs supplemented the daily
  schedule.
- The latest successful core run attempted 25 reviews and reported 1,388
  deferred reviews.
- The retained queue currently contains 1,399 queued entries.

Scheduled capacity is below observed arrival by 72 reviews per day. The queue
cannot burn down at the configured daily schedule even before accounting for
the existing backlog.

## Named follow-ups

1. **Review capacity:** reduce noisy arrivals, increase safe scheduled
   frequency, or raise the bounded per-run limit after measuring review runtime
   and failure cost. The present 25-per-day schedule is insufficient.
2. **Watchdog visibility:** add a sealed watchdog status producer before the
   dashboard claims watchdog health. Until then, dashboard health means latest
   run status only.
3. **Learned-skill usage:** either join learned skills to the validated estate
   usage receipt or keep the field explicitly unavailable. Do not copy usage by
   name without an exact capability identity.
4. **Candidate and transcript examples:** retain a valid shadow candidate and
   exact evidence snapshot before certifying those detail routes with live
   examples.
5. **Invalid usage events:** inspect the two opaque
   `usage_session_invalid_skill_name` failures and decide whether the source
   format is unsupported or malformed. Preserve the failures until resolved.
