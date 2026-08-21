# Dreaming proof-first development design

## Objective

Give Dreaming changes a fast path to one real end-to-end result before spending
time on broad tests, installation hardening, adversarial review, natural
cadence, or rollback proof.

## Non-goals

- This does not replace `development-loop`; it supplies Dreaming's
  project-specific development and proof mechanics inside that loop.
- A tracer result is not release evidence and cannot authorize merge,
  installation enablement, mutation, retirement, or a success claim.
- This does not weaken the halt switch, writer lease, report-only boundary,
  mutation authority, receiver pins, or rollback requirements.
- This does not require production architecture, complete schemas, broad
  fixtures, or exhaustive edge-case handling before the idea proves useful.
- This does not create another general test framework. Existing Dreaming
  commands, private roots, installer, self-test, dashboard, and proof skills
  remain the execution surfaces.

## Lane

**Systemic.** This changes the standard development order for Dreaming work
across implementation, testing, installation, review, and live proof. It does
not change production runtime behavior by itself.

Rollback is removal of the local `dreaming-proof-first` skill and reversal of
this document. The installed Dreaming generation is outside this change.

## Reuse contract

The process builds on:

- `development-loop` for risk classification, durable acceptance, review, and
  landing;
- the existing private preview roots and ports for report and dashboard work;
- direct Dreaming commands for foreground tracer runs;
- targeted deterministic tests for the path being changed;
- the installer halt, generation-bound self-test, explicit enable, and
  rollback commands for the final installed candidate;
- `behavior-validation`, `visual-proof`, `live-governance-lever-verification`,
  and `dual-review` after the tracer has shown that the behavior is useful.

The new piece is a Dreaming-specific phase boundary between an inexpensive
experiment and a release candidate. The current general loop has a UI-only
tracer branch, while Dreaming also needs tracers for census, scheduling,
evaluation, publication, cross-machine transport, and rollback behavior.

## The two candidates

### Tracer candidate

The tracer answers one product or mechanism question using the thinnest real
vertical path. It may use temporary wiring, one selected row, fixed sample
data, direct foreground execution, or a private snapshot when those choices
are stated plainly.

It must still use the real boundary being tested. A transport tracer crosses
the real SSH receiver. A scheduler tracer runs the real owner command. A
dashboard tracer renders the real API response. Calling an internal helper
does not substitute for the boundary under question.

The tracer is disposable. It is never enabled as the scheduled owner and never
mutates installed skill roots or plugin settings.

### Release candidate

The release candidate begins only after the tracer's result is accepted as
effective. It replaces temporary seams with the reviewed production
architecture, closes bounds and failure behavior, adds durable tests, and
passes installation, review, live proof, and rollback gates.

Tracer evidence may explain why the behavior is worth keeping. It cannot be
relabelled as release evidence.

## Process

### 1. Write the experiment card

Record:

- one question;
- one real trigger;
- one observable terminal result;
- the smallest real boundary that must be crossed;
- temporary seams being allowed;
- hard safety boundaries that remain active;
- deferred hardening work.

The card is complete when a stranger can say pass or fail from the observation
without reading the implementation.

### 2. Choose the cheapest honest runtime

Use the first applicable option:

1. Reuse an already running private preview.
2. Run the changed Dreaming command directly in the foreground with private
   data and state roots.
3. Use one halted installed generation when the behavior exists only under
   launchd or installed configuration.
4. Use the scheduled owner only when cadence itself is the question.

Do not install merely to test logic that a foreground command can exercise.
Do not wait four hours when an immediate manual run proves the same behavior;
retain one later natural tick only when cadence is an acceptance requirement.

### 3. Build one thin vertical slice

Implement only enough to cross the named boundary and reach the terminal
result. Prefer one subject, one row, one model call, one viewport, and one
state transition.

Run only the targeted checks needed to make that slice executable. Broad
self-test, full dashboard suites, paired review, browser matrices, natural
cadence, and rollback wait.

Temporary seams are acceptable only when they are visible in the experiment
card and cannot touch live writable state. Misleading fallback data and
success-shaped error handling remain unacceptable.

### 4. Run the tracer immediately

Drive the real boundary and inspect the terminal state directly. Record the
candidate identity, command or process identity, observation, and first
divergence.

Stay in a short diagnose-edit-trace loop. After three unsuccessful candidate
attempts or two incompatible explanations of the same failure, stop editing
and re-scope the experiment. Do not answer uncertainty by adding a framework.

### 5. Make the effectiveness decision

The tracer has one of three outcomes:

- **effective**: freeze the observed behavior and continue;
- **ineffective**: discard the candidate without hardening it;
- **inconclusive**: improve the observation surface, not the architecture.

An effective result freezes the user-visible or operational contract. Later
hardening may change internals but must preserve that result.

### 6. Harden the proven slice

Replace each temporary seam deliberately:

- bind exact input and candidate identity;
- add realistic size, time, model-call, and concurrency bounds;
- make refusals explicit and inspectable;
- preserve halt and writer-lease checks at every side-effect boundary;
- add focused regression and negative tests;
- document migration and rollback;
- remove temporary data and direct-only shortcuts.

Build a targeted validation closure before freezing the candidate. For each
changed executable, trust anchor, or generated configuration entry:

- enumerate every exact-byte authorization, stored digest, publisher or
  receiver pin, and focused test that consumes it;
- run the producer checks and all focused consumer checks together;
- generate the candidate adapter configuration in a private root;
- exercise its required local and remote health checks against the exact paths
  and byte identities that installation will use;
- classify adapter groups explicitly as preserved or regenerated.

Estate census and curator commands may remain preserved while executors,
comparators, or publishers are regenerated. A single preservation switch must
not silently retain code-bound adapter entries after their executable bytes
change.

Batch these changes into one coherent candidate. Use the complete targeted
closure while editing. Do not repeatedly run the installed full self-test.

### 7. Freeze and validate once

When all known hardening work is present:

1. run the targeted deterministic checks needed to produce the hardened
   candidate;
2. generate and exercise the candidate adapter configuration in a private
   preflight;
3. freeze the candidate;
4. install once behind halt;
5. run the generation-bound full self-test once;
6. enable only after it passes;
7. pass `development-loop`'s complete live-proof gate for the current
   candidate, including immediate behavior and every cadence, browser, and
   rollback checkpoint that the governing work order makes part of acceptance;
8. run the remaining broad deterministic validation;
9. run paired implementation review;
10. fix material findings, rerun the affected deterministic checks, and apply
   `development-loop`'s post-review live-proof rules. Systemic and critical
   work reruns the complete final end-to-end flow on the reviewed tree.

A runtime-changing review fix creates one successor candidate. Its proof scope
is the scope required by `development-loop`, including the complete final
end-to-end flow for systemic and critical work. It does not reopen exploratory
design.

## Failure model

- A tracer uses a mock or helper that bypasses the actual broken boundary and
  produces false confidence.
- Temporary wiring reaches live writable state.
- The tracer grows into production code without identity, bounds, rollback, or
  tests.
- Broad suites run after every small edit and become the debugging loop.
- A changed executable passes its own tests while a stored digest or remote
  authorization still names its previous bytes.
- A blanket adapter-preservation setting keeps stale executors or publishers
  while protecting unrelated stable estate commands.
- Generated adapter configuration is first exercised during enablement rather
  than in a private preflight.
- Review begins before anyone knows whether the behavior is useful.
- A natural cadence requirement is confused with a need to wait during normal
  iteration.
- Hardening changes the behavior that the tracer proved.

## Hard invariants

- Installed mutation and scheduled ownership remain off during tracer work.
- A tracer result is labelled exploratory until hardening and final proof
  finish.
- The real boundary named by the experiment card is exercised end to end.
- Full installed self-test runs only on a frozen candidate, except when
  diagnosing the self-test system itself.
- Review findings cannot expand scope beyond the frozen behavior and its
  realistic safety boundaries.
- Immediate proof and natural cadence are separate evidence with separate
  reasons.

## Check contract

### DREAM-FAST-CHK-01: Real-boundary tracer

- **Protects:** The experiment proves the integration being discussed.
- **Setup:** Run one thin candidate through the named external boundary.
- **Pass:** The terminal result is observed outside the helper that initiated
  it.
- **Failure:** Only an internal return value, fixture assertion, or source read
  exists.

### DREAM-FAST-CHK-02: Tracer isolation

- **Protects:** Fast iteration cannot change installed or live writable state.
- **Setup:** Inventory roots, launchd, halt state, and mutation endpoints before
  and after the tracer.
- **Pass:** Only declared private tracer roots changed.
- **Failure:** An installed root, scheduler, plugin setting, or live claim
  changed.

### DREAM-FAST-CHK-03: Deferred-hardening ledger

- **Protects:** Temporary seams do not silently become production architecture.
- **Setup:** Compare the experiment card with the frozen release candidate.
- **Pass:** Every temporary seam is removed or explicitly rejected from the
  release.
- **Failure:** A temporary seam remains without a reviewed production contract.

### DREAM-FAST-CHK-04: Expensive-gate discipline

- **Protects:** Broad suites remain final validation rather than the debugger.
- **Setup:** Inspect the retained command sequence.
- **Pass:** Targeted checks and the tracer preceded the frozen candidate's one
  full installed self-test.
- **Failure:** Full self-tests or broad matrices were repeatedly used to locate
  ordinary implementation defects.

### DREAM-FAST-CHK-05: Behavior preservation

- **Protects:** Hardening does not erase the value proven by the tracer.
- **Setup:** Repeat the tracer scenario against the reviewed release candidate.
- **Pass:** The same terminal result occurs with production seams and bounds.
- **Failure:** The hardened candidate changes or loses the accepted behavior.

### DREAM-FAST-CHK-06: Release gates

- **Protects:** Speed does not become permission to ship an unsafe candidate.
- **Setup:** Apply the existing Dreaming installation and proof sequence.
- **Pass:** Deterministic checks, review, halted installation, self-test,
  enablement, live proof, and rollback satisfy the governing work order.
- **Failure:** Tracer evidence is used to skip a release gate.

### DREAM-FAST-CHK-07: Effectiveness decision

- **Protects:** Hardening is spent only on behavior that worked and was judged
  useful.
- **Setup:** Compare the experiment card's decision timestamp and state with
  the first hardening change.
- **Pass:** The card records `effective` before hardening begins.
- **Failure:** Hardening begins with no decision or while the result is
  `ineffective` or `inconclusive`.

### DREAM-FAST-CHK-08: Immediate and natural evidence split

- **Protects:** Fast behavioral proof and scheduler-cadence proof remain
  separate claims.
- **Setup:** Inspect the live-proof receipt and governing acceptance criteria.
- **Pass:** Immediate proof and each required natural tick have separate
  evidence entries and state why each run was required.
- **Failure:** An immediate run is described as natural cadence, a natural tick
  is treated as unnecessary duplication, or the two are collapsed into one
  unsupported claim.

### DREAM-FAST-CHK-09: Targeted validation closure

- **Protects:** A changed producer cannot leave an exact-byte consumer pinned
  to its previous identity.
- **Setup:** Enumerate changed executables, stored digests, authorization
  checks, publisher and receiver pins, and focused consumer tests.
- **Pass:** The producer and every identified consumer pass in one targeted
  validation set, and no retired digest remains in an active configuration.
- **Failure:** Only the changed file's direct tests run, or installation finds
  a stale trust anchor that targeted validation could have exercised.

### DREAM-FAST-CHK-10: Adapter configuration preflight

- **Protects:** Full installed self-test and enablement are not the first
  exercise of generated adapter paths and identities.
- **Setup:** Generate the candidate adapter configuration in a private root,
  with adapter groups explicitly marked preserved or regenerated.
- **Pass:** Every required executable exists, every stored digest matches, and
  required local and remote health checks pass without changing launchd or live
  writable state.
- **Failure:** Enablement discovers a stale path, digest, receiver, publisher,
  or preservation decision.

## Definition of Done: Dreaming proof-first development

- [x] The `dreaming-proof-first` skill names the tracer and hardening phases
      and routes final release work back through `development-loop`.
- [x] The skill distinguishes immediate proof from natural cadence.
- [x] The skill keeps installed mutation, scheduled ownership, and live
      writable state outside tracer work.
- [x] The skill requires an effectiveness decision before broad hardening.
- [x] The skill prevents repeated full self-tests from becoming the debugging
      loop.
- [x] The skill requires a targeted validation closure for changed executable
      bytes, trust anchors, and generated configuration.
- [x] The skill requires adapter-config preflight before the installed full
      self-test.
- [x] Mechanical skill validation passes.
- [x] Paired review has no unresolved material finding.
- [x] The document and skill are committed locally; nothing is pushed.
