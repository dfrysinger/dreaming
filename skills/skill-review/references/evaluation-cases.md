# Skill evaluation cases

Create `.skill-evaluation-cases.json` at the candidate skill root. It is local
evaluation input, not part of the public skill.

```json
{
  "schema_version": 1,
  "source": {
    "task_id": "source:unique-task-id",
    "prompt": "A representative task the skill should improve.",
    "required_regex": [
      {"id": "required-outcome", "pattern": "(?i)observable result"}
    ],
    "forbidden_regex": [
      {"id": "harmful-action", "pattern": "(?i)unsafe shortcut"}
    ],
    "friction_regex": [
      {"id": "unnecessary-step", "pattern": "(?i)redundant step"}
    ]
  },
  "sibling": {
    "task_id": "sibling:distinct-task-id",
    "prompt": "A related task where an overfitted rule would be harmful.",
    "required_regex": [
      {"id": "preserved-outcome", "pattern": "(?i)correct sibling result"}
    ],
    "forbidden_regex": [],
    "friction_regex": []
  }
}
```

Assertions are never sent to the model. The runner executes each prompt in an
empty working directory and isolated `COPILOT_HOME`, once with no candidate
plugin and once with the candidate loaded as the only non-builtin skill. Only
the `skill` and `view` tools are exposed. Candidate runs are invalid unless the
model actually loads the named skill.

The gate passes only when the candidate makes a required source outcome newly
pass, while a passing sibling baseline continues to pass without added
friction. A friction-only delta from one sample is marginal and remains
`inconclusive`; malformed evidence is also `inconclusive`. A failed candidate
source or sibling regression is `regression`.

Run:

```bash
skill-review/scripts/run-skill-evaluation.sh <skill-dir> --model <exact-model>
skill-review/scripts/skill-evaluation.py gate <skill-dir>
```

Receipts are content-addressed under
`~/.copilot/skill-state/skill-review/evaluations/`. They bind the candidate
inventory, case manifest, exact model, Copilot CLI version, runner, prompt,
comparator, and flags. Any candidate or case edit makes the gate stale.

## Cross-CLI schema v2 (not yet the active gate)

M5.1 adds a separately validated, non-authoritative cross-CLI contract. Keep
the suite in the same local `.skill-evaluation-cases.json` file and put its
executor/comparator policy in local `.skill-evaluation-policy.json`; neither
file is candidate runtime input.

```json
{
  "schema_version": 2,
  "graders": [
    {
      "id": "safe-outcome",
      "type": "regex",
      "safety": true,
      "identity": "sha256:<sealed-deterministic-grader>"
    }
  ],
  "cases": [
    {
      "id": "intended-case",
      "class": "intended",
      "task_id": "intended:unique-task-id",
      "prompt": "A capability the skill should improve.",
      "deterministic_graders": ["safe-outcome"]
    },
    {
      "id": "related-case",
      "class": "related",
      "task_id": "related:other-unique-task-id",
      "prompt": "A related capability that must not regress.",
      "deterministic_graders": ["safe-outcome"]
    },
    {
      "id": "activation-positive",
      "class": "activation_positive",
      "task_id": "activation:positive-unique-task-id",
      "prompt": "A prompt that should activate the skill.",
      "deterministic_graders": ["safe-outcome"],
      "activation": {"expected_load": true}
    },
    {
      "id": "activation-negative",
      "class": "activation_negative",
      "task_id": "activation:negative-unique-task-id",
      "prompt": "A prompt that must not activate the skill.",
      "deterministic_graders": ["safe-outcome"],
      "activation": {"expected_load": false}
    }
  ]
}
```

Each case ID and task ID is unique. Every case references at least one
declared deterministic safety grader. Unknown fields, duplicate IDs, shared
task IDs, unsupported grader types, and activation expectations that disagree
with the case class are refused.

The policy has `schema_version: 2`, `profile` (`gate` or `iterate`),
`policy_kind` (`capability_uplift` or `encoded_preference`), a non-empty ordered
`required_executors` selection, and an exact comparator configuration. Selected
executors follow `copilot`, `claude`, then `codex` order, but a policy may
require any explicit subset. Each executor binds its exact model, adapter
identity/version/executable digest, and CLI executable digest. The comparator
binds its route, exact model, adapter identity/version/executable digest,
timeout, token budget, and rubric digest. Gate profiles have three trials per
arm; iteration profiles have one.

Validate or prepare identities without starting a CLI or writing authority:

```bash
skill-review/scripts/skill-evaluation.py v2-suite-validate <suite.json>
skill-review/scripts/skill-evaluation.py v2-policy-validate <policy.json>
skill-review/scripts/skill-evaluation.py v2-prepare <skill-dir>
```

Schema-v1 source/sibling manifests remain readable. They compile only to one
legacy intended and one related case, with `cross_executor_authority: false`;
they do not create activation cases or M5 authority.

Future M5 phases supply sealed executor certificates and aggregates. The
schema helpers store content-addressed aggregate receipts and schema-v3
authority only under
`~/.copilot/skill-state/skill-review/evaluations/v2/`. Authority paths are
`authority/<skill-path-key>/<candidate-id>.json`; the optional schema-v2
`.agent-created.json` field `evaluation_v3_sha256` is only an opaque digest.
The current `gate` command reads only legacy M2 state and cannot accept that
authority.

`v2-waive` and `v2-waiver-validate` are likewise non-authoritative helpers.
They require a passing version-2 aggregate, exact current candidate, suite,
policy, required executor list, restricted changed `scripts/` paths, an
unchanged test script identity, and a bound JSON test-result digest. Legacy M2
receipts cannot anchor these waivers.
