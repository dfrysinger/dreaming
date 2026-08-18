# Skill usage and estate table repair

## Objective

Make the governed MacBook skill estate show trustworthy per-skill usage from
its retained Copilot transcripts, and present the estate in tables that support
cleanup decisions instead of exposing empty implementation fields.

## Lane

**Systemic.** This change adds a new receiver-bound evidence record, persists it
through the Dreaming runtime, joins it into a dashboard API, and changes the
main estate presentation from physical files to enabled capabilities.

It does not change authentication, mutation authorization, archive behavior, or
plugin-disable behavior.

## Non-goals

- Do not authorize removal from usage alone.
- Do not reclassify uncertain personal skills as mutation-authorized merely
  because the dashboard labels their installation location as personal.
- Do not collect usage from the Mac mini's own Copilot sessions.
- Do not add hosted telemetry, a database, cloud credentials, or a second
  transcript pipeline.
- Do not claim that absence from retained local transcripts means a skill was
  never used.
- Do not solve semantic skill quality, overlap, or dependency evaluation in
  this change. Those remain separate governance evidence.

## Observed failure and cause

The live estate API returns `usage_complete: null`,
`dependencies_complete: null`, and an empty `evaluation_state` for every
physical skill row. The dashboard renders those absent fields as "not
recorded."

Copilot's session history is not empty. A direct 30-day query returned real
invocations for 72 skills, including current last-used timestamps and nonzero
counts. The existing `skill-usage-report.sh` only prints a query for an agent to
run. No scheduled path persists its result, and the estate collector never adds
usage fields. The first incorrect transition is therefore the estate evidence
boundary: transcript skill calls exist, but no structured usage record crosses
from the MacBook collector into Dreaming state.

## Reuse contract

This change extends the existing remote estate census connection instead of
adding another service:

- `dreaming-estate.py` already runs on the MacBook under the pinned
  `ssh-estate-census.py` receiver and owns bounded estate observation.
- Copilot's existing `events.jsonl` files are already the source consumed by
  `dreaming-vendor-adapter.py`.
- `dreaming-core.py` already writes content-addressed census receipts and a
  replaceable current pointer.
- `dreaming-dashboard.py` already verifies the receiver identity and current
  census before exposing estate data.

A separate usage receipt is required because the action-authorizing census
schema is exact and fail-closed. Adding optional dashboard fields to that
schema would unnecessarily change mutation authority. Usage remains evidence
associated with the same receiver and collection run, but it cannot authorize
an estate action.

## Data flow

1. The Mac mini starts the existing receiver-bound MacBook estate collection.
2. `dreaming-estate.py` inventories skills as it does today.
3. The same collector scans only direct child session directories under the
   MacBook's configured Copilot `session-state` root. It does not descend into
   session artifacts, test homes, or archives.
4. For each `events.jsonl`, it pairs `tool.execution_start` calls for the
   `skill` tool with successful `tool.execution_complete` events using the
   native tool-call identifier.
5. It resolves each invoked skill name against the enabled runtime mappings in
   the census produced by the same collection. A name is attributable only
   when every matching enabled runtime entry identifies the same
   `canonical_capability_id`. It aggregates attributable successful loads by
   that canonical identity:
   - uses in the last 7, 30, and 90 days;
   - total uses in retained local history;
   - most recent successful use;
   - earliest retained event;
   - sessions and bytes scanned;
   - any files that could not be read or parsed.
   Successful invocations with no enabled mapping or conflicting canonical
   mappings are retained as unattributed diagnostics by normalized name and
   reason. They are not assigned to any capability, and usage coverage is
   incomplete.
6. The collector returns the unchanged census plus a separate sealed usage
   snapshot. The snapshot records whether transcript coverage was complete.
7. `dreaming-core.py` writes a content-addressed usage receipt and an atomic
   `estate-usage-current.json` pointer. The receipt binds the same receiver
   identity used by the census.
8. `dreaming-dashboard.py` accepts usage only when its receipt, snapshot digest,
   receiver identity, host identity, and collection timestamp match the current
   census collection.
9. The Estate API builds one primary row per enabled canonical capability.
   Physical-only, duplicate, cached, and stale copies move to a separate
   diagnostic table.
10. The browser renders recent counts and last use directly. A skill absent
    from a complete retained-history scan displays zero recent use and "No use
    in retained history." An incomplete scan displays "Usage incomplete" and
    never turns missing data into zero.

## Presentation contract

The primary table is titled **Enabled skills** and contains:

- Skill
- Installed from
- Automation authority
- Origin evidence
- Recent use (7d / 30d / 90d)
- Last used
- State
- Latest decision

Raw values such as `unknown_provenance`, `legacy_machine`, and `plugin_managed`
may remain in the API but are translated into plain labels in the browser.
"Personal" means the personal skill installation root, not human authorship.

The secondary **Other physical copies** table contains only physical-only
instances and identifies their location, reason, and authority. The default
cleanup view no longer repeats enabled skills solely because the inventory also
tracks files.

The summary above the tables states the usage source, collection time,
retained-history start, and whether coverage is complete.

## Failure model

| Failure | Required behavior |
| --- | --- |
| A transcript file is malformed, unreadable, or changes during collection | Record usage as incomplete, identify the failed session without exposing transcript content, and do not report absent skills as zero-use |
| A skill call starts but does not complete successfully | Do not count it as use |
| The same tool-call identifier appears twice | Reject that session from complete coverage rather than double-counting |
| A transcript timestamp is malformed or in the future | Reject that session from complete coverage rather than mix trusted and untrusted event ordering |
| An invoked name has no enabled runtime mapping or maps to more than one canonical capability | Keep the invocation unattributed, mark coverage incomplete, and do not assign it to any capability |
| The usage receipt is missing, stale, malformed, or from another receiver or host | Keep the estate inventory visible, label usage unavailable, and do not synthesize zeros |
| A physical-only copy shares a name with an enabled skill | Do not assign the enabled skill's usage to the inactive copy |
| Collection exceeds configured session or byte bounds | Stop, mark usage incomplete, and report the bound that was reached |
| A matching older installation has no usage state | Continue serving the estate with an explicit "Usage unavailable" state until the next successful census |
| The mini and MacBook collector or receiver hashes do not match | Fail the receiver-bound census as today, preserve the prior current census as stale, and report the pin mismatch; do not claim that only usage degraded |

## Hard invariants

1. Usage collection reads only direct child `events.jsonl` files inside the
   configured MacBook Copilot session root.
2. Only successful native `skill` tool executions count as use.
3. Transcript content, prompts, tool results, file paths, and arguments other
   than the normalized skill name never enter the usage receipt or dashboard.
4. Missing usage is distinct from a proved zero inside complete retained
   coverage.
5. Usage receipts cannot authorize archive, consolidation, disable, or restore
   operations.
6. The existing census and estate-action schemas remain unchanged.
7. Usage is assigned only to a canonical capability identity resolved from the
   enabled runtime mappings in the same census snapshot.
8. The dashboard does not attach usage to physical-only copies.

## Acceptance criteria

- **AC-U1:** A fixture containing two successful loads and one failed load for
  a skill records two uses and the later successful timestamp.
- **AC-U2:** A complete fixture with no calls for an enabled skill displays
  zero recent use and "No use in retained history."
- **AC-U3:** A malformed transcript makes usage incomplete and the dashboard
  does not display zero for missing skills.
- **AC-U4:** The live MacBook collection shows nonzero usage and a last-used
  timestamp for at least one known recently invoked skill.
- **AC-U5:** The live estate page has an Enabled skills table with recent-use
  counts for 7, 30, and 90 days and a last-used column, plus a separate
  physical-copy diagnostic table.
- **AC-U6:** The page explains that Personal is an installation source and does
  not imply human authorship.
- **AC-U7:** Removing or corrupting the usage receipt leaves inventory visible
  with usage explicitly unavailable.
- **AC-U8:** Existing estate action validation and mutation authorization
  fixtures pass without schema changes.
- **AC-U9:** An invoked name that is absent from the enabled runtime mapping or
  maps to conflicting canonical capabilities is not assigned to a dashboard
  row and makes usage coverage incomplete.
- **AC-U10:** A collector or receiver hash mismatch fails the census and leaves
  the prior census stale; a matching older generation with no usage snapshot
  keeps the estate visible with usage unavailable.

## Check contract

### CHK-U1: Transcript usage aggregation

- **Protects:** AC-U1, AC-U2, AC-U3, AC-U9, and invariants 1 through 4 and 7.
- **Setup:** Create direct session fixtures containing successful, failed,
  duplicate, malformed, out-of-root, future-timestamp, unmapped-name, and
  conflicting-name events, with enabled runtime mappings from the same census.
- **Pass signal:** Successful calls aggregate exactly; incomplete sources are
  named only by opaque session identifier; uniquely mapped calls carry the
  matching canonical identity; unresolved names remain unattributed; no
  transcript content is emitted.
- **Failure signal:** Failed calls count, malformed input becomes zero, or
  nested/out-of-root files enter the result, or name-only evidence is assigned
  to a capability without a unique same-census mapping.
- **Why it proves the contract:** It exercises the earliest boundary where the
  current implementation loses usage evidence.

### CHK-U2: Usage receipt ownership

- **Protects:** AC-U7, AC-U8, AC-U10, and invariants 5 and 6.
- **Setup:** Record valid usage, then alter its snapshot, receiver, host,
  timestamp, and current pointer independently. Exercise a matching older
  generation with no usage snapshot and mismatched collector and receiver
  hashes separately.
- **Pass signal:** Only the exact current receiver-bound receipt is accepted;
  usage-receipt mismatches become unavailable without changing census
  validity; a matching older generation serves inventory with usage
  unavailable; code-pin mismatches fail census collection and preserve the
  prior census as stale.
- **Failure signal:** Tampered usage is shown, or missing usage breaks estate
  inventory and action validation, or a code-pin mismatch is presented as a
  usage-only degradation.
- **Why it proves the contract:** It keeps display evidence separate from the
  fail-closed action authority.

### CHK-U3: Estate API projection

- **Protects:** AC-U2, AC-U3, AC-U5, and invariant 8.
- **Setup:** Provide enabled, duplicate, physical-only, zero-use, used, and
  incomplete-coverage fixtures.
- **Pass signal:** Enabled rows receive the correct usage; physical-only rows
  do not; zero and unknown remain distinct.
- **Failure signal:** Counts duplicate across inactive copies or null becomes
  zero under incomplete coverage.
- **Why it proves the contract:** It verifies the join that supplies the user
  interface.

### CHK-U4: Live MacBook dashboard flow

- **Protects:** AC-U4, AC-U5, and AC-U6.
- **Setup:** Install the current candidate on both hosts, run one receiver-bound
  census, open the authenticated dashboard through its SSH tunnel, and inspect
  a known recently used skill plus a skill with no retained use.
- **Pass signal:** The known skill has nonzero recent use and a last-used time;
  its 7-day, 30-day, and 90-day counts are shown; the unused row is honestly
  labeled; the primary and secondary tables are visually distinct and
  readable.
- **Failure signal:** All usage is absent, stale physical copies inherit usage,
  raw enum labels dominate the table, or Personal reads as authorship.
- **Why it proves the contract:** It exercises the real transcript, receiver,
  persistence, API, and browser path in order.
- **Status:** PASS on reviewed candidate
  `d95de54430212eb5de9c7da396a503c0962ac8ad`, live run
  `20260818T031824Z-10537`. The durable token-free receipt is
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/skill-usage-final-live-proof/CHK-U4-final-receipt.md`.

## Migration and rollback

No existing census or action record is rewritten. Install creates usage state
on the next census.

Because the SSH boundary pins the receiver and collector file hashes, deployment
is a coordinated two-host operation. Install the candidate collector and
receiver on the MacBook, install the same generation on the mini, regenerate
the adapter command and expected hashes, and then run the first census. A
matching older generation that predates usage snapshots renders usage
unavailable; mixed generations do not degrade gracefully and must fail the
census with the prior current census left stale.

Rollback likewise restores the prior MacBook collector and receiver before or
with the prior mini generation, then regenerates the adapter command and pins
before collection resumes. The added usage receipts are inert and may remain
on disk because prior code does not read them. No skill or plugin mutation is
part of this change.

## Definition of Done: Skill usage and estate table repair

- [x] The MacBook collector emits bounded, privacy-preserving successful skill
      usage with honest completeness.
- [x] Dreaming persists and verifies a separate receiver-bound usage receipt.
- [x] The Estate API projects one row per enabled capability and keeps
      physical-only copies separate.
- [x] The dashboard shows 7-day, 30-day, and 90-day counts plus last use.
- [x] Zero use and unavailable usage are visibly different.
- [x] Personal installation source is not presented as human authorship.
- [x] CHK-U1, CHK-U2, and CHK-U3 pass on deterministic fixtures.
- [x] CHK-U4 passes on the installed two-host system.
- [x] Existing estate action and dashboard contract checks remain green.
