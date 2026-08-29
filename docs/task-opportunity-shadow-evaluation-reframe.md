# Reframe: bounded shadow evaluation of a routed candidate

## Status

The bounded shadow-evaluation slice is **not implementable at this integration
commit without new architectural subsystems**. This record names the exact
missing pieces, the code that proves each one is missing, and the re-scoped
slice sequence. It replaces the attempt rather than faking a partial result.

## What the funnel produces today

`derive_evaluation_routing` gives every retained catalog-audit disposition one
terminal route. A `no-covering-skill` disposition whose lifecycle record has
satisfied the three-current-independent-occurrence gate routes to
`candidate-evaluation` and names the exact immutable candidate package.

That row is the complete, correct hand-off subject. Nothing consumes it,
because the shadow evaluator cannot be driven from the scheduled owner.

## Why the existing harness cannot be called yet

The evaluator exposes exactly three shadow subcommands —
`shadow-compile`, `shadow-execute`, `shadow-certify`
(`skills/skill-review/scripts/skill-evaluation.py:11344-11368`). Driving them
from `scheduled_run()` requires four inputs the funnel cannot produce.

### 1. Shadow executor authority is not a role the runtime knows

`shadow-execute` hands routing to the harness, which invokes
`[*argv, "version"]` and requires the response to equal the declared executor
identity byte-for-byte
(`skill-evaluation-harness.py:1887-1894`, `shadow_attest`). The identity keys
are `adapter_id`, `adapter_version`, `adapter_executable_sha256`, `model`,
`cli_executable_sha256`, `cli_version`, `tool_policy_id`, `limits`,
`sandbox_id`, plus `real_backend` and `real_backend_source`
(`skill-evaluation-harness.py:57-60`).

`dreaming-vendor-adapter.py` does implement that protocol under the
`skill-evaluation-executor` role (`dreaming-vendor-adapter.py:6758-6772`), but
`dreaming-core.py` has **no reference to that role at all**. Core's adapter
configuration recognises only `sources`, `executors`, and `publishers`
(`dreaming-core.py:146-150`, `ROLE_CONFIG_KEYS`), and `ROLES`
(`dreaming-core.py:106-128`) declares only `session-source`,
`review-executor`, and `skill-publisher`.

Teaching core a fourth adapter role means a new config key, a new protocol and
capability manifest, new doctor/version/contract validation, and new adapter
identity handling. That is a new adapter subsystem, which this slice was
explicitly forbidden to add.

### 2. No production boundary designs a shadow suite

`shadow-compile` requires a suite with
`kind: shadow_candidate_evaluation_suite`, a `routing_mode`, an `environment`,
graders, and cases covering all five routing classes — `routing_positive`,
`routing_close_negative`, `routing_unrelated`, `routing_conflict`, and
`task_value` — each carrying `routing`, `artifacts`, `graders`, and `fixture`
(`skill-evaluation.py:10592-10720`, `shadow_suite`).

The only model boundary that designs evaluation cases is the
`evaluation-input-author` role
(`dreaming-vendor-adapter.py:3465-3520`,
`evaluation_input_author_schema:2986`,
`evaluation_input_author_prompt:3021`). It is correctly candidate-blind — the
model fills only `id`, `task_id`, and `prompt` over a fixed template — but it
speaks the **installed-capability** suite contract, not the shadow contract:
its packet validator runs `skill-evaluation.py v2-input-author-packet`
(`dreaming-vendor-adapter.py:3309-3335`), which loads the suite through
`load_suite` (`skill-evaluation.py:645-690`) with keys
`{schema_version, graders, cases}` and requires `cross_executor_authority`
(`skill-evaluation.py:2298-2305`).

There is no shadow authoring packet builder and no converter: the subcommand
list (`skill-evaluation.py:11053-11368`) contains `v2-input-author-packet`,
`v2-input-repair-packet`, and `v2-input-review-packet`, and no shadow
equivalent. Producing a shadow suite means a second authoring stack — shadow
suite template, shadow policy and compilation contracts, shadow packet builder,
packet validation, and response validation.

### 3. No production fixtures or graders exist for shadow cases

Every shadow case names a `fixture` and requires at least one safety grader
(`skill-evaluation.py:10683-10692`), and the harness resolves both per trial
(`skill-evaluation-harness.py:1713`). The only generator in the repository is
`make-shadow-fixture.py`, which `test-shadow-mutation-boundary.sh` writes into
its own temporary `tools` directory at run time (`test-shadow-mutation-boundary.sh:51`).
It does not exist as production code.

### 4. The existing scheduled evaluation owner has the wrong subject class

`execute_evaluation_input_owner` (`dreaming-core.py:9391-9454`, called from
`scheduled_run` at `dreaming-core.py:10454`) selects rows from the estate
census queue by `capability_id` and `skill_path`, reads its content files from
the sealed `evaluation-input-owner` root through
`evaluation_input_owner_content`, and runs `v2-input-owner-run`
(`skill-evaluation.py:11102-11121`). A shadow candidate has no capability id,
no census row, and no owner content, and the owner's output is authoring input
rather than a shadow certificate. Reusing it would mean giving shadow
candidates a synthetic capability identity — a second evaluation queue.

### 5. The bounded pass has no allowance for shadow trials

`shadow-execute` runs one trial per case per executor plus a control trial for
every `task_value` case (`skill-evaluation-harness.py:2344-2352`), each a real
CLI invocation. The pass deadline is 3,600 seconds
(`daemon-pass.sh:33`) and already carries the profile, review, and
evaluation-input allowances. No existing bound covers shadow trials.

## What changed instead

The prior successor added a `handoff-evaluation` subcommand that transitioned a
routed candidate to `evaluating`. Because nothing consumes `evaluating`, and
because `candidate-lifecycle.py` refuses `collect` outside
`{collecting, ready_for_draft, expired, rejected}` (`candidate-lifecycle.py:1011`)
while core's `ELIGIBLE_CANDIDATE_GROUP_STATES` (`dreaming-core.py:100-105`)
hides evaluating groups from the catalog-aware reviewer, that subcommand was a
one-way trap: a later semantic occurrence of the same procedure would fork a new
recurrence group, against hard invariant 3 and AC-02.

That surface is removed. No production code path can now reach `evaluating`.
A `candidate-evaluation` row instead carries an explicit
`evaluation_execution` block naming the unmet prerequisites
(`profile_evaluation_routing.py`, `SHADOW_EXECUTION_BLOCKERS`), and the run
report repeats them as `evaluation_routing.execution_blockers`, so the
projection never implies an evaluation that cannot run.

## Re-scoped slice sequence

Each step is independently useful and separately reviewable.

1. **Shadow evaluation executor role.** Teach core the
   `skill-evaluation-executor` adapter role: config key, protocol, capability
   manifest, doctor and version validation, and an attested executor identity
   plus routing document derived from the configured adapter. Proof: a real
   `version` call whose response satisfies `shadow_attest`.
2. **Shadow fixtures and graders.** Promote a minimal production fixture and
   safety-grader set for the five routing classes out of test scaffolding.
   Proof: `shadow-compile` accepts a hand-written suite over them.
3. **Shadow suite authoring.** Extend the existing candidate-blind
   `evaluation-input-author` boundary with a shadow suite template and packet,
   reusing its fixed-field/model-fills-prompt discipline. Proof: a designed
   suite validates through `shadow_suite` and leaks no identity marker.
4. **Bounded shadow run in the scheduled pass.** Materialize and re-verify the
   routed package, run compile/execute/certify under an explicit allowance,
   retain the certificate bound to the exact candidate and occurrence
   authorities, and always leave `evaluating` in the same pass — back to
   `ready_for_draft` on pass, refusal, or failure, with no install or
   publication authority.
5. **Evidence accumulation during evaluation.** Decide whether an evaluating
   group stays visible to the catalog-aware reviewer for aliasing only. Until
   then, step 4's same-pass exit keeps the invisibility window bounded to one
   pass.

Repair outcomes stay report-only throughout; no repair candidate is invented.
