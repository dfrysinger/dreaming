# Dreaming review backlog capacity

## Objective

Make the installed Mac mini review current Dreaming queue entries faster than
new entries arrive, while preserving the existing single-writer, bounded-review
and fail-closed mutation controls.

## Lane

**Critical.** This changes production scheduling for an autonomous process
that may mutate learned skills and consume model credits. It therefore carries
an explicit rollback path and live evidence that overlap, malformed
configuration, halt state and insufficient capacity all fail closed.

## Non-goals

- Do not add another queue, worker process, daemon or review ledger.
- Do not add dynamic autoscaling, feedback control or a general-purpose job
  scheduler.
- Do not increase the existing limit of 25 queue entries considered per core
  run.
- Do not run reviews concurrently within one core run or across overlapping
  launchd ticks.
- Do not change review routing, model selection, mutation policy, the 30-day
  autonomous mutation window, recurrence admission or the prohibition on
  autonomous new-skill creation.
- Do not delete historical queue revisions or rewrite existing queue, attempt
  or ledger records.
- Do not add another coalescing mechanism. `_queue_session` already marks the
  prior queued revision for the same qualified session as `superseded`; the
  current 1,399 queued rows represent 1,399 distinct session identities.
- Do not fix watchdog presentation, learned-skill usage or other dashboard
  producer gaps in this change.
- Do not redesign weekly memory roll or estate pruning.

## Current state and measured problem

The installed Mac mini has one launchd owner. Its `skills-consolidate` pass
already detects the configured standalone adapter and executes
`dreaming-core.py run` directly; it does not use the legacy prompt-scored
three-session sweep in this installation.

The deterministic core discovers sessions, supersedes older queued revisions,
reviews at most `max_reviews_per_run`, records attempts and terminal ledger
entries, then publishes through the existing owners. The installed
`max_reviews_per_run` is 25.

The launchd job currently runs once daily at 09:15. That supplies a nominal 25
queue slots per day. The bounded live sample measured 97 arriving revisions per
day, so scheduled capacity trails arrivals by 72 per day before addressing the
existing backlog. The current queue contains 1,399 queued rows, each for a
different qualified session.

The latest installed core pass processed 25 queue rows in 1,146 seconds:
19 reached an accepted terminal result and six were marked stale before review.
That fits inside the existing 1,800-second pass timeout. Keeping the 25-row
limit while scheduling six runs per day yields a nominal 150 slots per day.
Against the measured 97 arrivals per day, that leaves 53 slots per day for
backlog reduction, or approximately 27 days for the current backlog if the
sample remains representative.

## Reuse contract

This change extends the installed owner rather than creating a capacity
subsystem.

- Reuse `dreaming.plist.tpl` and the installer as the only schedule owner.
- Reuse `dreaming-run.sh`, `daemon-pass.sh` and `dreaming-core.py run` as the
  complete execution path.
- Reuse the shared writer lock and halt switch. More frequent ticks gain no
  separate concurrency mechanism.
- Reuse `max_reviews_per_run=25` as the per-run cost and duration bound.
- Reuse the existing bounded child-process helper to enforce the current
  1,800-second pass limit on the standalone core path as well as the
  agent-owned path.
- Reuse queue supersession, stale-before-review checks, mutation transactions,
  attempt records and the review ledger unchanged.
- Reuse the existing cadence state to remember the local calendar day of the
  latest weekly-pipeline attempt so a failed weekly pass is retried no more
  often than it is today.
- Reuse `DashboardData.overview`, `dream_rows` and the retained queue and
  ledger timestamps for a small observed-throughput presentation.

The interval is a fixed property of this installation, not a new runtime
configuration surface. The launchd template renders
`StartInterval=14400`. The adapter configuration does not gain a schedule
field and no runtime component parses the installed plist.

## Architecture and data flow

### Scheduled execution

Replace the single 09:15 `StartCalendarInterval` with launchd
`StartInterval=14400`.

Every tick follows the existing path:

1. launchd starts `dreaming-run.sh`;
2. the orchestrator checks the halt switch and acquires the shared writer lock;
3. `skills-consolidate` calls `daemon-pass.sh`;
4. the configured standalone path calls `dreaming-core.py run` through the
   existing bounded child-process helper with the current 1,800-second limit;
5. discovery adds new revisions and supersedes the prior queued revision for
   the same qualified session;
6. the core considers at most 25 queued rows in retained queue order;
7. each terminal review updates the existing attempt, transaction, queue and
   ledger state;
8. the run report records review outcomes and deferred current rows;
9. weekly roll and prune remain gated by the existing stable weekly bucket
   and may be attempted at most once per local calendar day until that bucket
   succeeds.

If a prior tick is still active, the next tick records lock contention and
launches no pass. The interval does not authorize overlap.

### Capacity projection

Extend the overview's `dreams` object with only:

- `queued`: current distinct rows whose raw status is `queued`;
- `oldest_queued_at` and `oldest_queued_age_seconds`;
- `arrivals_24h`: queue revisions whose `queued_at` falls in the prior 24
  hours, including revisions later superseded;
- `completed_24h`: review-ledger rows whose `reviewed_at` falls in the prior 24
  hours;
- `recovery_required`: current distinct rows whose raw status is
  `recovery-required`;
- `observed_net_24h`: completed reviews minus arriving revisions;
- `estimated_burn_down_days`: ceiling of current queued rows divided by net
  observed throughput when `observed_net_24h` is positive;
- `capacity_status`: `burning_down`, `not_burning_down` or `unknown`.

The projection is read-only. It grants no review or mutation authority.

`capacity_status` is `unknown` if required timestamps cannot produce the
24-hour measurements. `estimated_burn_down_days` is `null` unless observed net
throughput is positive. Zero, negative or unknown observed net throughput must
never render as a successful burn-down estimate.

The Overview page adds one compact capacity row near the backlog chart:

- arrivals and completed reviews over 24 hours;
- oldest queued age;
- sessions quarantined for transaction recovery;
- estimated burn-down time or the explicit text `Not burning down` /
  `Capacity unavailable`.

The existing backlog chart and Dreams table remain unchanged.

## Failure model

### A review pass exceeds its time bound

`daemon-pass.sh` applies its 1,800-second limit to the standalone core path.
Completed review transactions remain terminal. The interrupted session is
quarantined as `recovery-required` by the existing transaction journal; later
queued rows remain retryable on the next run. The orchestrator records an
aborted pass and does not advance weekly cadence. The fixed four-hour launch
interval remains greater than the enforced pass bound.

### Two launchd ticks overlap

The second tick cannot acquire the existing writer lock. It records
`lock-contention` and performs no discovery, review, publication, memory or
curator mutation.

### The halt switch appears

The existing pre-run and between-pass checks stop later work. Increasing the
tick frequency does not weaken the halt boundary.

### The rendered schedule is invalid

Installer and self-test fixtures require the fixed
`StartInterval=14400` and refuse a candidate that retains the old daily
calendar or adds another schedule owner. The adapter configuration is not part
of schedule validation.

### Arrivals exceed scheduled capacity

If retained 24-hour completions do not exceed retained arrivals, the dashboard
reports `not_burning_down` and no estimated completion date. The runtime does
not automatically raise concurrency, review limits or cadence.

### The Mac mini sleeps or a tick fails

Missed work remains visible because burn-down state uses retained completions,
not the configured schedule. Queue age and run history expose the same gap.

### A weekly roll or prune pass fails

The existing cadence state records that the weekly pipeline was attempted on
the current local calendar day before starting the first weekly pass. Later
ticks that day still run consolidation but report the weekly pipeline as
already attempted. A failed weekly pipeline becomes eligible again on the next
local day; a successful pipeline remains suppressed for the rest of its stable
weekly bucket.

### Old or malformed timestamps exist

Rows with invalid timestamps do not become zero-age arrivals or completions.
If the 24-hour projection cannot be established honestly, capacity status is
`unknown`.

## Hard invariants

1. Exactly one installed Dreaming launchd job owns scheduled discovery and
   review.
2. A core run considers no more than 25 queued rows.
3. Review execution remains sequential.
4. The writer lock and halt switch are checked at the same boundaries as
   before this change.
5. Existing queue supersession, transaction recovery, stale-review rejection
   and ledger idempotency semantics do not change.
6. Weekly roll and prune succeed at most once per stable weekly bucket and are
   attempted at most once per local calendar day.
7. The fixed 14,400-second launch interval is greater than the enforced
   1,800-second pass bound.
8. Dashboard capacity fields are derived from validated retained queue and
   ledger state and
   cannot authorize mutation.
9. Missing, malformed, zero or negative observed throughput never produces a
   successful burn-down estimate.
10. Rollback does not delete queue, attempt, ledger, transaction, run or skill
    evidence.

## Acceptance criteria

- **AC-C1:** An install renders `StartInterval=14400`, contains no daily
  `StartCalendarInterval` for the Dreaming job, and leaves the adapter
  configuration free of schedule state.
- **AC-C2:** The installed standalone core pass is forcibly terminated after
  the existing 1,800-second limit, leaving completed rows terminal, the
  interrupted session `recovery-required`, and later rows retryable.
- **AC-C3:** Each core run still considers at most 25 queued rows and reports
  every later current row as deferred.
- **AC-C4:** Multiple same-day consolidation ticks may review queue rows, while
  a failed weekly pipeline is attempted at most once that day and a successful
  weekly pipeline at most once in its bucket.
- **AC-C5:** An overlapping tick records lock contention and causes no review,
  publication or skill mutation.
- **AC-C6:** The installed Overview API reports current queued count, oldest
  queue age, 24-hour arrivals and completions, observed net throughput and an
  honest burn-down state, plus the current recovery-required count.
- **AC-C7:** Positive observed net throughput produces a rounded-up burn-down
  estimate; zero, negative, malformed or unavailable observed throughput
  produces no estimate.
- **AC-C8:** The browser presents the same values as the API and labels
  arrivals and completions as observed 24-hour values while making
  recovery-required sessions visible.
- **AC-C9:** A live installed core pass remains within the existing 1,800
  second bound while processing 25 queue rows, and a later natural interval
  starts another pass without human intervention.
- **AC-C10:** Across a bounded live interval, retained completed reviews exceed
  retained arrivals or the dashboard honestly reports that the queue is not
  burning down.
- **AC-C11:** Rollback restores the previous daily plist without changing the
  adapter configuration or deleting retained queue or review evidence.

## Deterministic check contract

### CHK-C1: Fixed bounded schedule

- **Protects:** AC-C1, AC-C2 and invariants 1 and 7.
- **Setup:** Render the installer fixture, inspect the adapter configuration,
  then run a standalone fake core child that exceeds the pass limit.
- **Pass signal:** The plist contains only `StartInterval=14400`, the adapter
  configuration contains no schedule field, and the child is forcibly stopped
  at the configured 1,800-second pass bound. Completed rows remain terminal,
  the interrupted session becomes `recovery-required`, and later rows remain
  queued for the next run.
- **Failure signal:** The daily calendar remains active, schedule state leaks
  into adapters, the standalone child outlives its bound, or the fixture
  weakens the existing pending-transaction quarantine.
- **Why it proves the contract:** The installed cadence is fixed and remains
  safely longer than an enforced full-pass bound.
- **Pre-build guard:** This fixture must first fail because the current
  template still renders one daily calendar interval and the standalone core
  bypasses the bounded runner, then pass after both changes.

### CHK-C2: Bounded repeated core runs

- **Protects:** AC-C3, AC-C4 and invariants 2, 5 and 6.
- **Setup:** Use fake source and executor adapters with more than 50 current
  queued sessions. Run the core under multiple same-day orchestrator ticks,
  first with a weekly failure and then with a weekly success.
- **Pass signal:** Each run considers at most 25 rows, later current rows are
  deferred, reviewed rows are not repeated, the failed weekly pipeline is not
  retried that day, and a successful weekly pipeline is not retried in its
  bucket.
- **Failure signal:** A run exceeds 25, repeats terminal work, loses queue
  entries, or triggers another weekly pass.
- **Why it proves the contract:** Frequency changes while review and weekly
  safety bounds remain fixed.

### CHK-C3: Overlap and halt

- **Protects:** AC-C5 and invariants 3 and 4.
- **Setup:** Hold the existing writer lock during a tick, then repeat with the
  halt switch present and with the halt switch appearing between passes.
- **Pass signal:** Each case records the existing explicit skip or abort and
  produces no review attempt, ledger, publication or skill change.
- **Failure signal:** Any second owner or halted pass reaches mutation.
- **Why it proves the contract:** Higher frequency does not create a second
  writer or weaken the emergency stop.

### CHK-C4: Capacity projection

- **Protects:** AC-C6, AC-C7 and invariants 8 and 9.
- **Setup:** Feed dashboard fixtures with current/superseded/recovery-required
  queue revisions and ledger completions covering positive, zero, negative,
  missing and malformed observed throughput. Include a case where a six-run
  nominal schedule would be healthy but observed completions trail arrivals.
- **Pass signal:** Counts and ages match the retained records; positive net
  capacity rounds burn-down days up; every other case returns a null estimate
  and `not_burning_down` or `unknown`.
- **Failure signal:** Superseded rows inflate current backlog, malformed
  timestamps become zeros, scheduled slots substitute for completions, or
  non-positive observed throughput gets a completion date.
- **Why it proves the contract:** The dashboard cannot convert absent or
  worsening evidence into a healthy claim.

### CHK-C5: Browser rendering

- **Protects:** AC-C8.
- **Setup:** Render Overview from the capacity fixtures at the installed
  desktop viewport and one narrow viewport.
- **Pass signal:** Observed arrivals, completions, oldest age,
  recovery-required count and burn-down state agree with the API without
  clipping or ambiguous labels.
- **Failure signal:** The UI hides a non-burning state or drops required
  values.
- **Why it proves the contract:** The human-facing governance view carries the
  same meaning as the producer.

### CHK-C6: Installed throughput

- **Protects:** AC-C9 and AC-C10.
- **Setup:** Install on the Mac mini behind the halt switch, self-test, enable,
  record queue and ledger watermarks, observe one 25-row pass and the next
  natural four-hour tick, then compare arrivals and completions over the
  bounded interval.
- **Pass signal:** Both ticks belong to the reviewed generation, neither
  exceeds 1,800 seconds, no overlap occurs, retained state remains valid, and
  completions exceed arrivals or the dashboard reports otherwise.
- **Failure signal:** The timer does not fire, a pass overruns, state becomes
  invalid, another owner appears, or the dashboard claims burn-down contrary
  to the measured interval.
- **Why it proves the contract:** The configured schedule supplies real
  capacity at the installed boundary rather than only in plist text.

### CHK-C7: Rollback preservation

- **Protects:** AC-C11 and invariant 10.
- **Setup:** Capture the installed plist, adapter config and evidence-file
  digests; install the candidate; then execute the existing rollback.
- **Pass signal:** The prior daily schedule returns, the adapter configuration
  is unchanged, retained queue/review evidence digests remain unchanged, and
  enable still requires a passing self-test.
- **Failure signal:** Rollback deletes evidence, changes adapter state or
  enables an untested generation.
- **Why it proves the contract:** Capacity tuning remains reversible without
  sacrificing governance history.

## Migration

1. Record the current queue, attempts, ledger, run records, adapter
   configuration, launchd plist and active generation.
2. Activate the existing halt switch.
3. Install the reviewed candidate.
4. Confirm the rendered plist carries `StartInterval=14400`, the adapter
   configuration is unchanged, the standalone pass bound is 1,800 seconds and
   the 25-review limit is unchanged.
5. Run the installed self-test and require zero failures.
6. Enable Dreaming.
7. Kickstart one canary core pass and capture its runtime, 25-row bound, queue
   transitions and dashboard projection.
8. Leave the reviewed generation installed for the next natural four-hour
   tick and capture CHK-C6.

No queue, ledger or skill migration is required.

## Rollback

1. Activate the halt switch.
2. Wait for the writer lock to clear.
3. Run the existing installer rollback to the recorded prior generation.
4. Verify the prior daily plist is restored and adapter configuration is
   unchanged.
5. Run the prior generation's self-test.
6. Enable only after the self-test passes.
7. Confirm queue, attempt, ledger, transaction, run and skill evidence remain
   present and parseable.

## Fail-closed evidence

The boundary is proved fail-closed when deterministic and live checks show:

- the fixed cadence installs without adding a second schedule owner;
- standalone core execution cannot exceed the 1,800-second pass bound;
- the 25-row cap remains binding on every tick;
- an overlapping tick records lock contention and performs no work;
- the halt switch stops work at the existing boundaries;
- a failing weekly pipeline is attempted no more than once per local day;
- a timed-out or failed pass leaves remaining queue rows retryable;
- non-positive or unknown observed throughput has no burn-down estimate;
- rollback restores the prior schedule without deleting evidence.

## Definition of Done: Dreaming review backlog capacity

- [ ] CHK-C1 through CHK-C5 pass on deterministic fixtures.
- [ ] The installed launchd plist uses one 14,400-second interval, the
      standalone pass is forcibly bounded at 1,800 seconds, and
      `max_reviews_per_run` remains 25.
- [ ] The Overview API and browser show honest queue age, arrivals,
      completions, recovery-required count, observed net throughput and
      burn-down state.
- [ ] CHK-C6 observes the reviewed generation complete one canary and one
      natural four-hour tick without overlap or timeout.
- [ ] The bounded installed interval demonstrates positive net throughput or
      presents `not_burning_down` without an estimated completion date.
- [ ] CHK-C7 proves rollback preserves all retained queue and review evidence.
- [ ] Installed self-test and the existing core, orchestrator, dashboard,
      installer and watchdog suites pass.
- [ ] Dual implementation review has no unresolved in-scope must-fix finding.
- [ ] Final installed proof passes on the reviewed tree.
- [ ] The design, implementation and proof references are committed locally;
      nothing is pushed.
