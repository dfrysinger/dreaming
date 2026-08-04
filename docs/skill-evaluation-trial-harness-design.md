# Skill evaluation trial harness

## Objective

Provide a local, replaceable harness that runs matched skill and no-skill
trials through Copilot CLI, Claude Code, and Codex, captures trustworthy
evidence, and returns sealed results for Dreaming to certify.

## Lane

Critical.

The harness crosses authentication, tool execution, skill loading, local file
access, and cross-provider prompt boundaries. It also supplies evidence used by
a fail-closed promotion gate. A false success, leaked input, mismatched trial,
or forged result can publish a harmful skill or expose private material.

## Non-goals

- Decide whether a skill may be promoted, consolidated, waived, archived, or
  published.
- Write Dreaming queues, ledgers, evidence envelopes, Git repositories, skills,
  promotion records, or latest-receipt pointers.
- Support operating systems beyond Dreaming's existing macOS runtime.
- Provide a hosted service, dashboard, leaderboard, multi-user database, or
  approval interface.
- Depend on private infrastructure or private repositories.
- Reimplement a general container platform or cloud sandbox service.
- Treat Copilot CLI, Claude Code, and Codex as interchangeable execution
  environments.
- Run native session discovery or use completed session content as an
  evaluation input.
- Allow a model, skill, or grader to choose executable commands, writable
  roots, receipt paths, or retention policy.
- Replace content review, public-safety review, or live product validation.

## Public design sources

This design adopts public patterns without copying their implementation:

- Anthropic Skill Creator: independent runs, repeated benchmarks, blind A/B
  comparison, and positive/negative trigger testing.
  https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills
- Anthropic agent evaluation guidance: task, trial, grader, transcript,
  outcome, and harness separation.
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- SkillsBench and BenchFlow: matched skill/no-skill conditions, clean task
  environments, deterministic verifiers, exact task digests, and harness
  compatibility gates.
  https://www.skillsbench.ai/
  https://docs.benchflow.ai/running-benchmarks
- Harbor: task, trial, sandbox, verifier, trajectory, and result separation.
  https://www.harborframework.com/docs/run-jobs/run-evals
- SWE-bench: intended failures must become passes while existing passing
  behavior remains passing.
  https://www.swebench.com/SWE-bench/guides/evaluation/
- LangSmith and AgentEvals: deterministic and model-based trajectory grading.
  https://docs.langchain.com/langsmith/trajectory-evals
- Inspect: task, solver, scorer, sandbox, and transcript separation across
  model providers.
  https://inspect.aisi.org.uk/

The harness remains a small local component because Dreaming must work without
a hosted evaluator, and because native CLI skill loading and trigger behavior
must be exercised directly.

## Reuse contract

Reuse existing Dreaming boundaries:

- `dreaming-vendor-adapter.py` owns vendor executable discovery, authentication
  projection, macOS sandbox construction, bounded process execution, and
  vendor-specific structured output parsing.
- `dreaming-adapter-contract-v1.json` supplies versioned role and capability
  discovery.
- `configure-adapters.py` compiles the selected vendor set and explicit
  authorization.
- `skill-evaluation.py` owns candidate and suite identity, receipt validation,
  certification policy, and the promotion gate.
- `lib-daemon.sh` supplies bounded process cleanup and authentication helpers.
- `atomic_json`, canonical JSON, and SHA-256 helpers supply deterministic local
  artifacts.

Add a trial harness because the current evaluator combines process launch,
skill projection, scoring, and policy in one Copilot-specific path. The harness
separates repeatable evidence production from Dreaming's authority decision.

Do not overload `review-executor`. Add a separate
`skill-evaluation-executor` role with the capabilities:

```json
{
  "protocol": "dreaming.skill-evaluation-executor",
  "contract_version": 1,
  "capabilities": [
    "isolated-control",
    "isolated-candidate",
    "skill-load-proof",
    "bounded-tools",
    "normalized-trace",
    "artifact-export",
    "exact-model"
  ]
}
```

## Ownership boundary

### Dreaming supplies

- a sealed evaluation suite;
- the immutable candidate snapshot;
- selected executors and exact models;
- the explicitly authorized comparator route, exact model, adapter, and
  budgets;
- treatment and trial matrix;
- tool, network, time, token, and artifact budgets;
- deterministic grader definitions;
- blind-comparison rubric;
- retention policy;
- expected harness, adapter, and executable digests.

### The harness supplies

- fresh directories and CLI homes;
- candidate projection for one trial;
- control isolation;
- bounded trial scheduling;
- raw event capture;
- normalized traces;
- declared artifact collection;
- deterministic grader execution;
- blind comparator execution;
- repeated-trial aggregation;
- a sealed result bundle.

### The harness cannot

- alter the suite or candidate;
- add an executor, model, tool, grader, or trial;
- weaken a timeout, budget, or sandbox;
- decide pass, waiver, promotion, or consolidation;
- update a latest pointer or evidence envelope;
- retain credentials in an artifact;
- treat a partial or malformed run as success.

## Concepts

### Task

A task is one case from the sealed suite. It contains:

- stable case ID and class;
- user-visible prompt;
- declared input files or initial working-state fixture;
- allowed tools and network policy;
- expected artifacts;
- deterministic and semantic grader references;
- resource budgets.

Task definitions contain no executor-specific command line.

### Trial

A trial is one attempt at one task, executor, model, treatment, and repetition.
It has one immutable identity and one fresh environment.

### Pair

A pair contains the control and candidate trials for the same task, executor,
model, repetition, fixture, tools, and budgets. A pair is invalid unless every
field except treatment and arm-specific skill projection matches.

### Treatment

- `control`: built-in CLI behavior with no candidate or user-installed skill.
- `candidate`: the exact candidate snapshot as the only non-built-in skill.

### Grader

A grader receives declared outputs from a completed trial and emits structured
evidence. Graders do not mutate the trial workspace.

### Comparator

A comparator receives anonymized A/B outputs from one valid pair and a sealed
rubric. It does not receive treatment names, candidate paths, skill names,
execution order, or prior scores.

### Trace

A normalized trace describes observable CLI activity. It supports comparison
and diagnosis but does not override final-state grading.

### Result bundle

A result bundle contains immutable trial evidence and no policy decision. Its
digest is an input to Dreaming's per-executor certificate.

## Input bundle

Dreaming writes one read-only run directory:

```text
run/
  manifest.json
  suite.json
  candidate/
    SKILL.md
    references/
    scripts/
    assets/
  fixtures/
  graders/
  comparator/
```

`manifest.json` binds:

```json
{
  "contract_version": 1,
  "run_id": "sha256:...",
  "invocation_nonce": "random-caller-issued-value",
  "candidate_id": "sha256:...",
  "suite_id": "sha256:...",
  "profile": "gate",
  "trials_per_arm": 3,
  "executors": {
    "copilot": {
      "model": "exact-model-id",
      "adapter_id": "sha256:...",
      "adapter_version": 1,
      "adapter_executable_sha256": "sha256:..."
    }
  },
  "comparator": {
    "route": "explicitly-authorized-route",
    "model": "exact-comparator-model-id",
    "adapter_id": "sha256:...",
    "adapter_version": 1,
    "adapter_executable_sha256": "sha256:...",
    "timeout_seconds": 120,
    "token_budget": 4096,
    "rubric_id": "sha256:..."
  },
  "harness_executable_sha256": "sha256:...",
  "tool_policy_id": "sha256:...",
  "grader_set_id": "sha256:...",
  "retention_policy_id": "sha256:..."
}
```

The harness recomputes every file digest before starting. A mismatch produces
one run-level refusal and no trial process.

## Output bundle

The harness writes only beneath a caller-provided empty output directory:

```text
result/
  manifest.json
  trials/
    <trial-id>/
      result.json
      prepared.json
      raw.jsonl
      trace.json
      artifacts/
      grader-results.json
  comparisons/
    <pair-id>.json
  aggregate.json
```

The output manifest records:

- input run ID;
- caller-issued invocation nonce;
- harness version;
- observed harness executable digest and per-executor adapter identity;
- observed CLI executable identity and version;
- authorized comparator route, exact model, adapter identity, and budgets;
- host and process-boundary metadata that is safe to retain;
- every trial and pair ID;
- file inventory and SHA-256 digest;
- start and completion timestamps;
- complete, incomplete, or refused state.

The harness never writes a `pass` promotion decision. `aggregate.json` reports
counts, scores, deltas, variance, trigger rates, invalid trials, and
infrastructure errors. Dreaming applies policy afterward.

## Trial matrix

For every selected executor:

1. expand each behavioral task into `trials_per_arm` matched pairs;
2. expand each activation prompt into candidate-only trigger trials and
   control canaries where useful;
3. assign stable trial IDs from run, executor, model, task, treatment, and
   repetition identity;
4. alternate arm launch order by pair;
5. cap concurrency globally and per executor;
6. create a fresh home, working directory, logs, and artifact directory for
   each trial;
7. run both arms even when the first arm fails, unless the failure proves the
   shared pair setup invalid.

Gate runs use three trials per arm. Iteration runs use one and are visibly
non-authoritative.

## Executor adapter contract

Each adapter implements:

```text
skill-evaluation-executor doctor
skill-evaluation-executor version
skill-evaluation-executor prepare --trial <trial.json>
skill-evaluation-executor run --trial <trial.json> --prepared <prepared.json> --output <raw.jsonl>
skill-evaluation-executor normalize --raw <raw.jsonl> --trace <trace.json>
skill-evaluation-executor collect --trial <trial.json> --artifacts <dir>
```

`doctor` verifies executable identity, supported version, authentication,
candidate projection, structured output, skill-load observability, tool
restriction, and sandbox denial.

`version` returns the observed adapter identity and version plus the resolved
CLI executable identity and version. Unsupported, ambiguous, or drifting
identity refuses that executor.

`prepare` creates only adapter-owned files inside the fresh trial home. It
returns the exact command, environment allowlist, sandbox profile identity, and
projection inventory without launching the model. The harness writes this as a
versioned `prepared.json` and binds its digest to the trial.

`run` consumes the sealed prepared-execution record and executes it without
shell interpolation. It returns the prepared-record digest and structured
effective model, CLI version, adapter identity, tool policy, limits, sandbox
identity, and projection identity. Native evidence is required where the CLI
exposes it. An unobservable fallback or any difference from `prepare` makes the
trial invalid.

`normalize` converts raw vendor events into the common trace vocabulary.

`collect` exports only paths declared by the task. It cannot scan unrelated
workspace or home content.

## Native CLI adapters

### Copilot CLI

- Use a fresh `COPILOT_HOME`.
- Load the candidate through an isolated plugin or skill directory.
- Disable custom instructions, built-in MCP servers, remote execution, and
  undeclared tools.
- Use structured JSON output and the exact configured model.
- Record `skill` tool invocation as the primary skill-load proof.
- Control receives no candidate plugin.

### Claude Code

- Use a fresh Claude home and empty setting sources.
- Load the candidate through a trial-local plugin projection.
- Disable user and project instructions, hooks, MCP servers, slash commands,
  and undeclared tools while retaining the narrow authentication projection.
- Use structured output and the exact configured model.
- Record the native skill load or file-read event that resolves the candidate
  `SKILL.md`.
- Control receives no candidate plugin or marketplace entry.

### Codex

- Use an ephemeral trial-local home with projected authentication only.
- Ignore user configuration and project rules.
- Install or project the candidate into the isolated native plugin inventory.
- Use the exact configured model, structured output schema, and declared
  sandbox policy.
- Record the native plugin or skill-load event and resolved candidate identity.
- Control receives an empty trial plugin inventory.

Every adapter must prove that the candidate projection is immutable for the
trial and that the final skill path resolves inside the trial bundle.

## Normalized trace

The normalized trace uses ordered JSON events:

```json
{
  "sequence": 7,
  "timestamp": "2026-08-04T00:00:00Z",
  "kind": "skill_load",
  "tool": "skill",
  "input_digest": "sha256:...",
  "output_digest": "sha256:...",
  "artifact_paths": []
}
```

Supported kinds:

- `user_message`
- `assistant_message`
- `skill_load`
- `tool_call`
- `tool_result`
- `artifact_write`
- `final_answer`
- `usage`
- `trial_end`

Unknown vendor events remain in the raw log and are listed in normalization
diagnostics. A schema change that could hide skill loading, tool use, final
answer, or trial completion makes the trial invalid.

Raw logs may contain sensitive task content and follow the declared retention
policy. Normalized traces use bounded text or digests wherever full content is
not required for grading.

## Skill-load proof

A candidate behavioral trial is valid only when:

- the candidate is present in the adapter's sealed projection inventory;
- the native trace records loading the named skill or resolving its exact
  `SKILL.md`;
- the resolved path is the projection inventory's `SKILL.md`;
- the loaded `SKILL.md` digest matches that inventory entry;
- the complete projection inventory hashes to `candidate_id`;
- no other non-built-in skill loads.

An activation-positive trial is successful only when the same proof exists.
An activation-negative trial regresses when any candidate load proof exists.

Model prose claiming that it used a skill is not proof.

## Graders

### Deterministic graders

Supported initial grader types:

- `regex`: required, forbidden, and friction matches over bounded output;
- `json_schema`: structured result validation;
- `file`: required, forbidden, digest, size, and content checks on declared
  artifacts;
- `command`: a sealed executable and arguments stored in the input bundle;
- `trace`: required and forbidden normalized event predicates;
- `numeric`: bounded values derived from another deterministic grader.

A command grader:

- executes after the model process exits;
- runs inside the same sandbox with network denied unless the task explicitly
  allows it;
- receives declared artifact paths only;
- has a fixed timeout and output limit;
- is part of the grader-set digest;
- cannot construct another command from model output.

Any deterministic safety assertion is mandatory and cannot be overridden.

### Blind model comparator

Semantic comparison uses the exact Dreaming-supplied comparator route, model,
adapter, rubric, and budgets bound in the run manifest:

1. create an A/B assignment after both paired trials complete;
2. provide the task, rubric, and bounded declared outputs;
3. remove transport metadata such as treatment, executor command, temporary
   paths, execution order, and prior grader results;
4. scan the resulting packet for suite-declared skill names, slugs, projection
   paths, and identity markers without rewriting judged task content;
5. mark the comparison inconclusive if treatment identity remains observable;
6. require structured `A`, `B`, or `tie` plus criterion scores and evidence;
7. validate the response against a schema;
8. unblind only after comparator output is durable.

Comparator execution uses no candidate skill, no task tools, and no writable
task workspace. The route must be explicitly authorized before any task or
output transfer. The result records the resolved route, model, adapter,
budgets, packet digest, and response digest. An unauthorized route, identity
leak, configuration mismatch, or invalid comparator output makes the semantic
comparison inconclusive.

## Artifact and outcome handling

Tasks declare artifact paths before execution. After the trial:

1. record whether each declared source path exists in the completed trial
   workspace;
2. reject symlinks, devices, escaped paths, and undeclared outputs selected for
   grading;
3. copy declared artifacts into the result bundle;
4. record path, mode, size, and digest;
5. run deterministic graders over the copied immutable set;
6. retain or remove raw working state according to policy.

The harness grades actual artifacts and external state when available. A final
answer that claims success cannot compensate for a failed artifact or command
grader. Absence recorded in an otherwise valid completed workspace is a task
outcome. Failure to inspect, copy, index, or seal that workspace is an
infrastructure error and cannot be scored as candidate behavior.

## Resource fairness

Matched arms must have identical:

- exact model;
- observed CLI executable identity and version;
- adapter executable identity and version;
- effort or reasoning setting;
- system and task prompt outside the treatment projection;
- tools and permissions;
- network policy;
- timeout;
- token and output limits;
- initial files and environment;
- grader versions;
- comparator route, exact model, adapter, rubric, and budgets.

Usage is reported, not normalized away. A candidate that succeeds by consuming
more time or tokens may still pass behavior policy, but the receipt exposes the
cost for later policy changes.

Different executors are not matched arms. Their results remain separate
certificates because CLI scaffolding, model access, skill loading, and tool
semantics differ.

## Concurrency and cleanup

- One trial owns one process group and one fresh directory set.
- Global and per-executor concurrency are bounded.
- Cancellation terminates the process group, waits for exit, and records an
  incomplete trial.
- Cleanup runs only after result files are flushed and hashed.
- Credentials and projected authentication are removed before ordinary
  artifacts.
- Cleanup failure is reported and blocks authoritative completion.
- The halt switch prevents new trials and cancels active disposable trials
  without deleting durable result evidence.

## Failure model

| Failure | Required behavior |
|---|---|
| Input digest mismatch | Refuse the run before creating a trial |
| Unsupported CLI or adapter version | Refuse that executor and mark its evidence unavailable |
| Authentication missing | Record setup required, not candidate failure |
| Unauthorized executor | Do not prepare or invoke it |
| Candidate projection differs from inventory | Refuse the candidate trial |
| Control sees candidate or user skill | Mark the trial invalid and fail the run boundary |
| Candidate skill load is unproved | Mark the candidate trial invalid |
| Unknown event hides load, tool, or completion meaning | Mark the trial invalid |
| One arm has different model, tools, or budget | Reject the pair |
| Prepared or effective execution identity differs | Mark the trial invalid and reject the pair |
| Model exits nonzero | Record agent failure if setup remained valid |
| Sandbox or harness fails | Record infrastructure error, not candidate failure |
| Declared artifact is absent from a valid completed workspace | Let the deterministic grader fail the task |
| Artifact collection, copy, indexing, or sealing fails | Mark the trial invalid and the pair inconclusive |
| Undeclared artifact appears | Record it for diagnostics; do not grade or export it |
| Deterministic safety assertion fails | Trial fails regardless of model comparator |
| Comparator output is invalid | Semantic comparison is inconclusive |
| Comparator route is unauthorized or packet leaks treatment identity | Do not transfer, or mark the comparison inconclusive if detected before execution |
| Raw log or trace is missing | Trial is invalid |
| Cleanup fails | Result bundle remains incomplete |
| Output directory is not empty | Refuse before writing |
| Result file changes after sealing | Dreaming rejects its digest |

## Security and privacy

- Run only caller-sealed commands and grader executables.
- Construct subprocess arguments as arrays without shell evaluation.
- Pass an explicit environment allowlist.
- Keep provider credentials out of logs, traces, artifacts, and receipts.
- Deny native session roots, unrelated home content, SSH material, keychains
  beyond the narrow existing authentication boundary, and repository roots not
  declared by the task.
- Treat task files, skill content, model output, tool output, and artifacts as
  untrusted.
- Bound every text field, event count, file count, file size, process duration,
  and aggregate output.
- Store sensitive raw evidence locally with mode `0600`.
- Never publish evaluation cases, raw traces, or artifacts as part of a public
  skill.
- Require explicit executor authorization before provider transfer.
- Treat the reviewed harness and adapter executable digests as the local
  producer trust anchor. Result content remains untrusted until the caller
  verifies producer identity, run nonce, native-event structure, effective
  execution, inventory, and file digests.

## State and retention

The harness owns disposable run state only. Durable evaluation state remains
under Dreaming's versioned evaluation directory.

Retention classes:

- `failure`: retain raw log, normalized trace, declared artifacts, and grader
  results until explicit cleanup;
- `pass`: retain normalized trace, graders, comparator, and digests; raw logs
  may expire after the configured period;
- `iteration`: retain only while the authoring run is active unless pinned;
- `audit`: retain every sealed file until explicitly released.

Retention changes future cleanup only. It never removes files already bound by
a receipt unless the receipt remains sufficient to verify the retained
digests, and the promotion policy permits that evidence class.

## Migration and rollback

The current Copilot-specific runner remains authoritative until:

1. the harness input and output schemas are installed;
2. all three adapters pass deterministic contract tests;
3. Dreaming can verify sealed result bundles without invoking a model;
4. the public synthetic three-CLI acceptance suite passes;
5. the promotion gate is switched to the new receipt version.

The new harness writes separate versioned run and result directories. It does
not alter existing receipts.

Rollback activates the halt switch, cancels disposable trials, restores the
previous runner and adapter configuration, retains sealed results as inert
audit evidence, and keeps promotion disabled until the previous self-test
passes. Rollback never deletes cases, candidates, traces, artifacts, or
receipts automatically.

## Check contract

### Input sealing

- **Invariant:** every trial derives from the exact sealed run.
- **Setup:** change one candidate, suite, fixture, grader, executor, model,
  budget, or policy file after manifest creation.
- **Pass signal:** harness refuses before trial creation.
- **Failure proof:** any process start would show unsealed input reached an
  executor.

### Treatment isolation

- **Invariant:** candidate is the only arm difference.
- **Setup:** seed user instructions and unrelated skills, then prepare both
  arms.
- **Pass signal:** control has no non-built-in skill; candidate has exactly one
  matching snapshot; prepared commands otherwise match.
- **Failure proof:** leaked content or another differing field invalidates the
  causal comparison.

### Native skill-load proof

- **Invariant:** candidate results count only when the exact skill loaded.
- **Setup:** return a correct answer without loading, load a different skill,
  and load the correct candidate.
- **Pass signal:** first two trials are invalid; the third resolves the sealed
  `SKILL.md`, matches its file digest, and matches the full inventory to
  `candidate_id`.
- **Failure proof:** accepting either invalid trial would let base-model
  behavior masquerade as skill effect.

### Pair matching

- **Invariant:** paired arms use identical resources and fixtures.
- **Setup:** vary one prepared or effective model, CLI version, adapter, tool,
  timeout, token budget, sandbox, or initial file.
- **Pass signal:** pair validation refuses scoring and identifies the differing
  field.
- **Failure proof:** a scored mismatch would attribute scaffold differences to
  the skill.

### Outcome authority

- **Invariant:** observable state outranks model claims.
- **Setup:** final answer claims success while a declared file or command
  grader fails.
- **Pass signal:** trial fails and records both the claim and deterministic
  failure.
- **Failure proof:** passing would trust self-report over the task outcome.

### Blind comparison

- **Invariant:** semantic graders cannot see treatment identity.
- **Setup:** swap A/B assignments across asymmetric fixture outputs, including
  one output with a declared candidate identity marker.
- **Pass signal:** clean packets contain no arm identity, identity-leaking
  packets are inconclusive before transfer, and unblinding maps clean results
  correctly.
- **Failure proof:** identity leakage allows preference bias.

### Activation accuracy

- **Invariant:** positive prompts load the skill and negative prompts do not.
- **Setup:** deterministic fixture events cover correct trigger, missed
  trigger, and false trigger.
- **Pass signal:** normalized traces classify all three correctly.
- **Failure proof:** final-answer grading alone cannot establish activation
  behavior.

### Adapter parity

- **Invariant:** all three executors implement the same protocol semantics.
- **Setup:** run one fake deterministic case through Copilot, Claude, and Codex
  fixture CLIs.
- **Pass signal:** equivalent normalized traces, artifacts, and structured
  status despite different raw events.
- **Failure proof:** a divergent adapter would make certificates incomparable
  at the contract level.

### Real CLI boundaries

- **Invariant:** isolation and load proof work in native CLIs.
- **Setup:** run one public synthetic control/candidate pair and activation
  positive/negative prompt through each authenticated CLI.
- **Pass signal:** exact model, skill load, tool policy, artifacts, and denied
  roots are directly observed.
- **Failure proof:** fixture parity alone cannot prove native command behavior.

### Result sealing

- **Invariant:** Dreaming receives immutable complete evidence.
- **Setup:** remove, alter, or move one result file after sealing; substitute
  one harness, adapter, CLI, or comparator identity; and falsify a deterministic
  grader result while preserving its artifact.
- **Pass signal:** result verification rejects every bundle, including the
  falsified grader result after deterministic recomputation.
- **Failure proof:** accepting it would allow evidence substitution.

### Cancellation and cleanup

- **Invariant:** interrupted trials leave no active process or credential copy.
- **Setup:** cancel during model execution and during artifact collection.
- **Pass signal:** process group exits, credentials are removed, durable partial
  evidence says incomplete, and no authoritative aggregate is emitted.
- **Failure proof:** a surviving process or complete-looking result breaks the
  fail-closed boundary.

### Retention

- **Invariant:** cleanup follows policy without invalidating receipts.
- **Setup:** expire pass and iteration evidence while retaining failure and
  audit evidence.
- **Pass signal:** only permitted files are removed and every surviving receipt
  remains verifiable.
- **Failure proof:** unverified deletion would destroy the evidence behind a
  certification.

## Live acceptance

Use public synthetic tasks and an exact reviewed candidate.

For each of Copilot CLI, Claude Code, and Codex:

1. run a control/candidate capability pair three times;
2. run a control/candidate encoded-preference pair three times;
3. run a related-task pair where an intentionally overfitted skill regresses;
4. run activation-positive and activation-negative prompts three times;
5. inspect native raw logs, normalized traces, skill-load proofs, declared
   artifacts, grader results, blind comparisons, and sealed output digests;
6. confirm seeded unrelated instructions, skills, and denied home files do not
   appear;
7. cancel one disposable trial and prove cleanup.

Then hand the sealed result to Dreaming and confirm Dreaming independently
recomputes its identity and produces the expected per-executor certificates and
aggregate refusal or pass.

## Skill evaluation trial harness Definition of Done

- The harness accepts only sealed versioned inputs and writes only to an empty
  caller-owned output directory.
- Task, trial, pair, treatment, grader, comparator, trace, and result contracts
  are explicit and schema-validated.
- Control and candidate arms differ only by the exact candidate projection.
- Copilot CLI, Claude Code, and Codex implement the same
  `skill-evaluation-executor` contract.
- Every candidate trial proves the exact skill loaded; every negative
  activation trial proves it did not.
- Deterministic final-state checks override model claims and semantic judges.
- Gate runs use three matched trials per arm and report distributions rather
  than one sample.
- Blind comparator packets reveal no suite-declared treatment identity;
  identity leakage detected before transfer makes the comparison inconclusive.
- Comparator provider, model, adapter, rubric, and budgets are explicitly
  authorized, sealed, and recorded.
- Prepared and effective execution records prove the observed CLI, adapter,
  model, tools, limits, sandbox, and candidate projection for each arm.
- Candidate identity binds the complete inventory while native load proof
  separately binds the projected `SKILL.md` file.
- Native raw logs and normalized traces remain linked by content digests.
- Unauthorized routes, inherited instructions, unrelated skills, credentials,
  and native session roots are excluded.
- Cancellation removes processes and projected credentials without emitting a
  complete-looking result.
- Result bundles are content-addressed, complete, and independently verifiable
  by Dreaming.
- The public synthetic three-CLI live acceptance passes on the reviewed tree.
- Rollback restores the prior runner, preserves evidence, and keeps promotion
  halted until self-test passes.
