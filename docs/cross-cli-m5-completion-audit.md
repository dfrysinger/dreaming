# Cross-CLI skill certification completion audit

## Result

Every cross-CLI certification completion predicate is satisfied for installed
runtime `26d17d1c91e5b7bb6a06c83f483c0eb0d8d00cc4` and generation
`20260808T051913Z-install-49018`.

## Completion predicates

| Predicate | Evidence |
|---|---|
| Dreaming owns policy, identity, certification, receipts, authority, promotion, and rollback | `skill-evaluation.py` produces and validates the versioned records. The final isolated state root contains only Dreaming receipts, certification, latest pointer, and authority. The harness design assigns policy and authority exclusively to Dreaming. |
| The trial harness is replaceable and non-authoritative | `docs/skill-evaluation-trial-harness-design.md` defines sealed inputs and results and forbids Dreaming-state writes or policy decisions. The final run writes authority only through `skill-evaluation.py`. |
| All three providers implement one executor contract | The final public identity derivation returns Copilot, Claude, and Codex routes under `dreaming.skill-evaluation-executor` with one tool-policy identity. |
| Gate evidence uses three matched trials per arm with exact identities and budgets | The frozen audit reports `ALL_EMITTED_ARMS_THREE arms=18 trials=54` and validates executor, model, CLI, adapter, harness, grader, comparator, tool, and budget bindings. |
| Capability, encoded preference, related task, and activation outcomes are deterministic | The certification suites cover pass, regression, and inconclusive fixtures. The retained helpful and overfit live runs demonstrate pass and regression; unavailable advisers demonstrate inconclusive behavior. |
| Required-provider certificates are independent | The final result contains ordered independent certificates. Copilot is the only required certificate and the required-certificate-set digest excludes adviser certificates. |
| Copilot is the installed default and advisers are configurable | The final public policy derivation records Copilot as required and Claude and Codex as advisory. Policy supports promoting configured advisers to required. |
| Adviser states are visible and non-authoritative | Claude and Codex are retained as advisory/inconclusive with explicit adapter errors. The aggregate remains authoritative from Copilot alone. |
| Promotion rejects invalid evidence | Deterministic certification checks reject partial, stale, regressing, unauthorized, and legacy-only evidence. The retained overfit run is a real Copilot regression and authority issuance is refused. |
| Version compatibility fails closed | Schema-v2 cases remain readable. The rollback proof shows the older gate refuses version-2 authority rather than relabeling or authorizing it. |
| Trial isolation excludes unauthorized context | Sealed packets and traces validate candidate-versus-empty treatment inventories, authorized routes, fresh roots, bounded tools, and no native-session input. |
| The real Copilot acceptance suite passes with best-effort advisers | The final exact-generation run completed 54 trials, produced required Copilot pass authority, retained both unavailable advisers, and validated authority successfully. The blind judge ruled `SUPPORTED`. |
| Rollback preserves evidence and remains halted until self-test | The retained rollback restored the prior gate behind the halt, preserved authoritative aggregate hashes, and rejected version-2 authority. Restoration required a matching self-test receipt before explicit enablement. |

The installed runtime also passed enabled-operation canaries under normal
machine load. Discovery completed without the former adapter timeout, Copilot
reviewed Claude-source sessions from the scheduled execution environment, the
queue advanced under a bounded per-run review budget, controlled mutation
committed through the Dreaming ledger, and publication succeeded to all three
CLIs. The queue increase observed during earlier retries was continued
historical discovery, not duplication: every inspected
`(qualified_session_id, source_revision)` pair was unique.

## Release evidence

- Final behavior report:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/behavior-validation-m5-runtime-26d17d1/REPORT.md`
- Generation-bound live-proof receipt:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-live-proof.txt`
- Final implementation review:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/final-code-review-m5/REPORT.md`
- Rollback proof:
  `/Users/dfrysinger/.copilot/session-state/c8b94df9-d6e2-5cd7-a707-155405e27d8f/files/cross-cli-m5-rollback-20260806.log`

The compatibility receipt still reports unrelated local changes in
`skills/dual-review/SKILL.md` and
`skills/dual-review/references/reviewer-prompt-template.md`. Installation
rejected those skewed sources and used the verified repository source. The
skew does not contribute to cross-CLI authority.

The first frozen auditor used by the final proof failed eight assertions. Its
retained output showed six checks incorrectly applied a required-provider load
rule to advisory Codex trials, one compared a Codex Node shim with the resolved
executable identity already bound by the pre-run compilation, and one treated
unrelated documentation dirt as a runtime change. The corrected auditor and
its exact diff are retained. The independent judge re-read the raw public
responses, manifests, trials, traces, certificates, bindings, both auditor
outputs, and the disclosed diff, then ruled the scenario `SUPPORTED` without a
rerun because the immutable raw evidence independently satisfies every
criterion.
