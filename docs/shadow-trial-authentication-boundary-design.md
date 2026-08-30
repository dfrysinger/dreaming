# Shadow trial authentication boundary — critical work order

Revision 2. Round 1 dual review returned six material findings (D1–D6) plus an
exact-schema finding from Terra. All seven are resolved in this revision; see
the [Round 1 finding resolution](#round-1-finding-resolution) appendix. The
architecture changed materially: no identity schema or version change is made,
the sandbox rules are restructured rather than appended, credential
*completeness* and credential *usability* are separated, and the rollback is
the evaluator config entry rather than the argv flag.

## Objective

Allow a supported real Copilot shadow-trial executor subprocess to authenticate
against the already-authenticated local CLI installation while running under a
synthetic `HOME`, so that shadow evaluation trials return real model verdicts
instead of `authentication-required`, without granting the trial access to the
caller environment, the real home, credential bytes, or any content outside its
declared inputs.

## Non-goals

1. **Not** authoring authentication. The authoring adapter path
   (`evaluation_input_author`, `sandboxed_command(..., "isolated")`) already
   works and is untouched.
2. **Not** general credential access. Nothing here grants the harness, the
   stage, the scheduler, or any reviewer process the ability to read credential
   material.
3. **Not** a caller-environment passthrough. `harness_environment`
   (`skills/skill-review/scripts/skill-evaluation-harness.py:675`) keeps
   supplying a fixed minimal environment; the parent environment is never
   forwarded.
4. **Not** an identity schema change. `EVALUATION_ADAPTER_VERSION` stays `1`,
   no top-level executor identity field is added, and no exact-key schema in
   `skill-evaluation-harness.py` or `skill-evaluation.py` is modified. The
   policy identity change is the shadow-mode `sandbox_id` digest; the adapter
   executable digest necessarily changes with the adapter bytes.
   (Revised in this round; see D1, D2, T1.)
5. **Not** a change to non-shadow behavior. Fixture adapters, unauthenticated
   adapters, the comparator route, and every non-`--shadow-contract` invocation
   keep byte-identical identities and byte-identical sandbox profiles.
6. **Not** the Agent Auth Broker. Its supported APIs cover attended login and
   specific authenticated reads; they do not cover projecting an authentication
   state into a sandboxed model-execution subprocess. Using it here would be a
   claim of generic credential access we cannot support.
7. **Not** executor content permission widening. The trial's access to
   candidate, catalog, transcript, and workspace content is unchanged by this
   work order.
8. **Not** a new subsystem. No queue, owner, scheduler, transport, catalog
   stage, or classifier is added.

## Lane

**Critical.**

- It changes an authentication projection.
- It changes a pinned fail-closed sandbox profile, which is a trust anchor: the
  sandbox profile digest is an input to `sandbox_id`, which is part of the
  attested executor identity.
- Failure modes are silent and severe: a too-wide rule leaks credential bytes
  into a model-visible context; a too-narrow rule silently degrades every trial
  to `infrastructure_error` and every candidate to `inconclusive`, which is
  exactly the PG-7 blocker.
- Round 1 confirmed a second severity driver: the naive version-bump approach
  would have been rejected by three independent exact-key schemas at runtime,
  and would have changed *non-shadow* identity. A critical-lane enumeration is
  what surfaced that.

## The three authentications, kept separate

This work order is about exactly one of three distinct authentication
boundaries. Conflating them is the primary design risk.

| Boundary | Who authenticates | Current state | Touched here |
|---|---|---|---|
| **Authoring** | The adapter's own `evaluation_input_author` Copilot call, run under `sandboxed_command(..., "isolated")` with the adapter's own environment | Working; proved live by PG-1/PG-2 | No |
| **Attestation** | `evaluation_version` reading the CLI version and digests | Working; needs no credential | No |
| **Trial** | The per-trial model execution subprocess launched under `harness_environment` with a synthetic `HOME` | **Broken**: `adapter failed (2): authentication-required: copilot` | **Yes** |

Separately, and orthogonally: **executor content permissions** — what candidate,
catalog, and workspace bytes a trial may read — are governed by the trial root
grant and the deny roots, and are *not* changed by this work order.

## Observed failure

Live tracer `live-6` (real backend, `--keep-scratch`) completed the whole
bounded stage — claim, author, suite assembly, compile, execute, certify,
settle to `ready_for_draft`, four durable writes, zero residue — and produced
`certificate_status: inconclusive`. Every trial in the aggregate carried:

```
adapter failed (2): authentication-required: copilot
infrastructure_error: true
```

All four routing cases and the task-value pair were `inconclusive`. Attestation
succeeded in the same pass, because reading a CLI version requires no
credential.

## Root cause

`skills/skill-review/scripts/dreaming-vendor-adapter.py:4656` resolves the
credential root:

```python
def evaluation_credential_root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "credential_root", None) or Path.home())
```

The shadow executor argv, unlike the reviewed authoring executor argv built by
`scripts/build-evaluation-input-source.py:197`, never carries
`--credential-root`. Under `harness_environment`'s synthetic `HOME`, `Path.home()`
therefore resolves to a scratch directory that has never held a credential, so
`copy_auth_file` (`dreaming-vendor-adapter.py:1198`) silently copies nothing,
`copilot_auth_token` (`:1249`) finds no token, and every trial fails
`authentication-required`.

The fix is therefore *not* a new mechanism. It is closing a gap between two
existing reviewed argv contracts.

## Constraint provenance and revisit conditions

| Constraint | Source | Revisit when |
|---|---|---|
| "A fixed minimal environment. No caller or routing allowlist exists." | `skill-evaluation-harness.py:675` docstring; `docs/skill-evaluation-trial-harness-design.md:639` | **Challenged and narrowed in this revision.** The rule is about *routing-supplied* environment, not about adapter-owned credential projection. Revisit if the harness ever gains a routing-controlled environment surface. |
| "Credentials never appear in logs, traces, artifacts, or receipts" | `docs/skill-evaluation-trial-harness-design.md:642` | Never relaxed. Reinforced here by I5, AC-T18, CHK-A18. |
| "Deny keychains beyond the narrow existing authentication boundary" | `docs/skill-evaluation-trial-harness-design.md:643` | Narrowed here: shadow mode denies keychains outright, because the measured evidence shows file-resident credentials suffice. |
| Adapter identity is exact-keyed and version-pinned to `1` | `skill-evaluation-harness.py:57`, `:682`; `skill-evaluation.py:10734`, `:10770` | Only if those three schemas are changed together in a separate work order. Treated as frozen here. |
| Credential root must equal the invoking account home | `scripts/build-evaluation-input-source.py:289` | Only if a supported host separates the account home from the credential home. |
| File-resident Copilot credentials are sufficient for real model execution under a synthetic `HOME` | **Measured**, this round. Retained proof root: `~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/shadow-trial-auth-probe` | If a supported host stops storing a file-resident credential, or Copilot changes where it reads authentication from. See the revisit gate in the reframe record. |
| macOS `sandbox-exec` `process-path` discriminates on the exact executable path, so it can confine reads to one binary location | **Measured**, this round. Retained proof root: `~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/shadow-trial-auth-sbpl-probe` | If the platform sandbox implementation changes, or if CHK-A3/CHK-A16 cannot reproduce the discrimination under the generated adapter policy. |

### The challenged rule, precisely

The inherited rule — *"no caller or routing allowlist exists"* — is a statement
about the **harness**, and it stays true. The harness continues to build one
fixed minimal environment and continues to pass routing-supplied argv only. It
gains no allowlist, no passthrough, and no knowledge of credentials.

What changes is the **adapter's own argv**, which the harness already forwards
verbatim, and which already accepts `--credential-root` as a defined adapter
argument (`dreaming-vendor-adapter.py:6851`). Adding it to the *configured*
evaluator argv creates no caller allowlist: the harness still learns nothing
about credentials and gains no environment surface.

## Reframe record — status CLEAR

**Reframe question.** Can a real Copilot trial subprocess authenticate under
the harness's synthetic `HOME` without passing the parent environment, the real
`HOME`, a token, or keychain contents?

**Status: CLEAR** (raised OPEN in revision 1; cleared this round by measured
mechanism evidence).

**Five answers.**

1. **What is the trigger?** Every real-backend shadow trial fails
   `authentication-required`, making PG-7 unreachable and every real-model
   certificate `inconclusive`.
2. **What is the smallest change that could work?** Add `--credential-root
   <account home>` to the shadow executor argv, reusing the adapter's existing
   projection, and confine the projected files to the CLI process path.
3. **What would make that change wrong?** If the projected files were readable
   by the model process itself, or if a token had to be injected into the trial
   environment, or if credential material could reach a record.
4. **What evidence closes the question?** A real Copilot model call that
   succeeds with only projected files present, under a synthetic `HOME`, with
   no parent environment, no `GH_TOKEN`/`GITHUB_TOKEN`, and no keychain access.
5. **What happens if it stays open?** PG-7 stays blocked and the stage keeps
   producing structurally correct but semantically empty certificates.

### Evidence that closed the premise

Three probes. Probes 1 and 2 are retained under
`~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/shadow-trial-auth-probe`;
probe 3 under its own root, named below.
No secret content was recorded, transcribed, or included in this document.

1. **Redacted source-structure probe.** Presence-only booleans:
   `hosts_file_present=true`, `oauth_token_key_present=true`,
   `user_key_present=true`, `github_host_entry_present=true`. This establishes
   that the supported host stores a file-resident credential with the expected
   shape.
2. **Direct real-model probe.** Copilot was launched under `env -i` carrying
   only `PATH`, a synthetic `HOME`, `LANG`, `LC_ALL`, and `COPILOT_HOME`. The
   synthetic home contained copies of the existing `.config/gh/hosts.yml`,
   `.config/gh/config.yml`, and `.copilot/config.json` at mode `0600`. No
   `GH_TOKEN`, no `GITHUB_TOKEN`, no keychain projection, no parent
   environment. Result: `gpt-5-mini` exited `0` with 26 JSON events, the
   assistant returned `AUTH_OK`, and no authentication error was raised.
3. **Platform `process-path` mechanism probe.** Retained under
   `~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/shadow-trial-auth-sbpl-probe`.
   Under `sandbox-exec` on macOS, a dummy credential path was guarded by
   `deny file-read* (subpath …)` plus
   `allow file-read* (require-all (subpath …) (process-path "/bin/cat"))`.
   Reading through the exact `/bin/cat` path succeeded (`rc=0`, marker
   returned); reading the same bytes through a copy of the same binary at a
   different path was denied and killed (`rc=137`). No credential material was
   involved: the fixture used a dummy file.

**What this evidence does and does not establish.** Probes 1 and 2 establish
the *authentication mechanism*: file-only projected authentication is reachable
on the supported host, so the architecture below is not speculative. Probe 3
establishes the *confinement primitive*: `process-path` discriminates on the
exact executable path, not merely on the executable's contents, which is the
property A3 depends on. Together they remove the two premise risks that would
have made this design speculative.

They do **not** establish the final generated adapter policy, nor real Copilot
behavior under it — the emitted rule set, its effective ordering, and the real
CLI's reads remain unproved until CHK-A3 and CHK-A16 execute. They also do not
establish the identity closure (CHK-A8) or the live end-to-end stage result
(CHK-A1). Probe 3 is a **prerequisite** for those checks, not a substitute for
them.

Round 2 must verify that this revision closes D1–D6, T1, and R1; the executed
checks remain the acceptance boundary.

**Revisit gate.** Reopen if a supported host ceases to hold a file-resident
credential, if Copilot changes where it reads authentication from, or if the
executed confinement proof (CHK-A3/CHK-A16) fails and cannot be satisfied by
restructuring the emitted rules.

## Enumeration of enforcement layers

Traced before selecting the architecture, per
`layered-validation-boundary-changes`. Ordered by pipeline position, not by
discovery order. Rows added this round are marked *(R2)*.

| # | Layer | File:line | What it enforces | Changed |
|---|---|---|---|---|
| L1 | Harness environment producer | `skill-evaluation-harness.py:675` | Fixed minimal env for every trial subprocess | No |
| L2 | Harness routing env digest | `skill-evaluation-harness.py:524` | Digest of the routing environment | No |
| L3 | Harness executor identity key set | `skill-evaluation-harness.py:57` | Exact key set | No |
| L4 | Harness adapter identity key set | `skill-evaluation-harness.py:61` | Exact key set | No |
| L5 | Harness attestation equality | `skill-evaluation-harness.py:682` | `set(response) != EXECUTOR_IDENTITY_KEYS` → refuse | No |
| L6 | Harness shadow attest superset | `skill-evaluation-harness.py:1888`, `:1939` | Adds `real_backend`, `real_backend_source` only | No |
| L7 | Harness trial prepare/run/normalize/collect | `skill-evaluation-harness.py:1955` | Per-trial protocol | No |
| L8 | Adapter argv surface | `dreaming-vendor-adapter.py:6850` | `--binary`, `--credential-root`, `--deny-root` are all defined adapter arguments (none of them is required by config today) | No (flag already exists) |
| L9 | Adapter credential root resolution | `dreaming-vendor-adapter.py:4656` | Defaults to `Path.home()` | **Yes** |
| L10 | Adapter credential projection | `dreaming-vendor-adapter.py:1198` | `copy_auth_file`, silent on missing | **Yes** |
| L11 | Adapter token acquisition | `dreaming-vendor-adapter.py:1249` | `gh auth token`, account-home bound, 10 s | Reused, not changed |
| L12 | Adapter executor environment | `dreaming-vendor-adapter.py:4660` | Env handed to the CLI subprocess | **Yes** (shadow only) |
| L12a | Adapter run-time token injection *(R2)* | `dreaming-vendor-adapter.py:5792` | Injects `GH_TOKEN` into the trial env today | **Yes** (shadow only: no injection) |
| L13 | Adapter sandbox profile builder | `dreaming-vendor-adapter.py:4915` | Emitted SBPL rules and their order | **Yes** |
| L13a | Adapter unconditional projected-file literal allows *(R2, D3)* | `dreaming-vendor-adapter.py:4989` | `deny file-write*` + **`allow file-read*`** per projected path, emitted after the trial-root grant | **Yes** (shadow only: suppressed and replaced) |
| L13b | Adapter keychain read grants *(R2)* | `dreaming-vendor-adapter.py:4950` | Broad `file-read*` on both keychain roots | **Yes** (shadow only: omitted) |
| L13c | Adapter process-path precedent *(R2)* | `dreaming-vendor-adapter.py:4985` | `allow network-outbound (process-path …)` | No (reused as precedent) |
| L13d | Adapter script-executable refusal *(R2)* | `dreaming-vendor-adapter.py:4977` | Refuses `#!` CLI executables because they cannot receive process-scoped access | No (constrains A3) |
| L14 | Adapter profile write | `dreaming-vendor-adapter.py:5015` | Writes `<home>/evaluation.sb`; list order is rule order | No |
| L15 | Adapter identity — version | `dreaming-vendor-adapter.py:3936` | `EVALUATION_ADAPTER_VERSION = 1` | **No** *(was "Yes" in revision 1; see D1/D2)* |
| L16 | Adapter identity — `sandbox_id` input | `dreaming-vendor-adapter.py:4038` | Hash input describing the confinement | **Yes** (shadow only) |
| L17 | Adapter identity — limits | `dreaming-vendor-adapter.py:4012` | Exact-keyed limits block | No |
| L18 | Adapter comparator identity | `dreaming-vendor-adapter.py:4066` | Comparator identity | No (digest changes only) |
| L19 | Adapter prepare | `dreaming-vendor-adapter.py:5127` | Emits sealed `prepared` record | **Yes** |
| L19a | Adapter prepared-drift equality *(R2)* | `dreaming-vendor-adapter.py:5784` | `prepared["adapter_prepared"] == expected_prepared` | No (shape unchanged) |
| L20 | Adapter doctor | `dreaming-vendor-adapter.py:6192` | Health, auth predicate `is_file()` at `:6211`, canaries `:6233` | **Yes** |
| L21 | Adapter boundary prover | `dreaming-vendor-adapter.py:1497` | `prove_boundary` private-data refusal | No (must keep passing) |
| L22 | Evaluator config argv requirement | `dreaming-core.py:170`, `:9533` | `SHADOW_EXECUTOR_REQUIRED_ARGV`, today exactly `("--shadow-contract", "--model")` | **Yes** (adds `--credential-root` as the third flag) |
| L23 | Strict config loader | `dreaming-core.py:9597` | Raises `RuntimeFailure` for the **whole** config | No (behavior relied on) |
| L23a | Strict config validator *(R2, D6)* | `dreaming-core.py:9838` | `validate_adapter_config` uses the strict loader | No (behavior relied on) |
| L24 | Shadow execution authority | `dreaming-core.py:9554` | Stage availability | No |
| L25 | Shadow executors normalizer | `skill-evaluation.py:10734` | Exact keys; `adapter_version != 1` → refuse at `:10770` | No (constrains A4) |
| L26 | Authoring adapter byte pin | `skill-evaluation.py:88` | `TRUSTED_AUTHORING_ADAPTER_SHA256` | **Yes** (digest only) |
| L27 | Input-source bundles | `skill-evaluation.py:3235`, `:3468`, `:4477` | Pinned adapter identity inside generated bundles | **Yes** (regeneration) |
| L28 | Builder ratchet test | `test-evaluation-input-source-builder.sh:160` | Asserts the pinned digest | **Yes** (digest only) |
| L29 | Installed config generator | `scripts/install.sh:702` | Generates `sources`, `executors`, `publishers` — **not** `evaluators` | No |
| L30 | Docs and error strings | this file; `docs/skill-evaluation-trial-harness-design.md:639` | Human-facing statement of the rule | **Yes** |

## Selected architecture

Minimizes trusted components by reusing the adapter's existing, already-reviewed
credential projection and by adding **no** new identity surface.

### A1 — Configured argv carries the credential root

Add `--credential-root <account home>` to `SHADOW_EXECUTOR_REQUIRED_ARGV`
(`dreaming-core.py:170`), so that `_require_role_argv` (`:9533`) refuses any
evaluator entry lacking it.

**Exact current fact (R1).** `SHADOW_EXECUTOR_REQUIRED_ARGV` is today
`("--shadow-contract", "--model")` and nothing more; `dreaming-core.py` contains
no reference to `--binary` or `--deny-root` at all. `--credential-root`
therefore becomes the **third** required configured flag, alongside
`--shadow-contract` and `--model`. Revision 2's claim that the same enforcement
already covers `--binary` and `--deny-root` was false and is withdrawn.

**`--binary` stays adapter-owned.** Executable selection remains the adapter's
responsibility and is *attested*, not configured: the resolved executable is
pinned into the identity as `cli_executable_sha256`
(`dreaming-vendor-adapter.py:4006`) and is the subject of the `process-path`
confinement in A3. No new configured requirement is added for it, and none is
added for `--deny-root`, because the existing design does not need one.
Widening the required-argv tuple beyond the single flag this boundary needs
would be an unreviewed config-contract change.

The adapter validates the argument in this order, and **refuses before any
projection, any profile emission, and any model launch**:

1. the flag is present and non-empty;
2. `Path(raw).is_symlink()` is false — checked on the **raw** argument, before
   `expanduser()` and before `resolve()`;
3. `Path(raw).expanduser().is_symlink()` is false — the second check catches a
   tilde-prefixed link before `resolve()` can launder it;
4. the path exists and is a directory;
5. `Path(raw).expanduser().resolve() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()`.

**Deliberate divergence from the builder precedent (D5).**
`scripts/build-evaluation-input-source.py:289` resolves first and *then* calls
`is_symlink()`, which is effectively vacuous because `resolve()` has already
followed the link. Checking the raw path first is a deliberate tightening: it
refuses a symlinked credential root outright rather than silently accepting its
target. This divergence is intentional and must not be "harmonized" back to the
builder's ordering.

Refusal codes: `shadow-credential-root-missing`,
`shadow-credential-root-symlink`, `shadow-credential-root-invalid`,
`shadow-credential-root-mismatch`.

**Where this authority lives.** These four validations belong to argument
validation at the command boundary. `evaluation_credential_root` and
`evaluation_sandbox_profile` must stay authority-free, so that profile
construction remains independently testable from synthetic inputs (see
[Layer separation in the executed checks](#layer-separation-in-the-executed-checks)).
This places the invariant in exactly one owner; it does not weaken it.

### A2 — Fail-closed projection *completeness* check

Today `copy_auth_file` (`:1198`) silently no-ops when the source is missing or
is a symlink, so a broken projection is discovered only when the model call
fails deep inside a trial.

Under `--shadow-contract`, after projection, the adapter asserts for each
required projected path:

- it exists and is a regular file;
- its destination is not a symlink;
- its mode is `0600`;
- its size is non-zero.

Failure raises `shadow-credential-projection-incomplete`.

**This check is named *completeness* and claims only completeness.** Presence,
mode, and size prove that projection happened; they prove nothing about whether
the credential is valid or the account is signed in. That is A2b's job. (D4.)

### A2b — Bounded adapter-owned authentication *usability* probe

Before any model launch, the adapter performs one bounded positive probe that
the projected authentication is actually usable:

- use the account-home branch of the existing
  `copilot_auth_token(credential_root)` implementation while deliberately
  ignoring ambient `GH_TOKEN` and `GITHUB_TOKEN`. It locates `gh`, runs
  `gh auth token --hostname github.com` under the invoking **account** home with
  its own minimal environment, and is already bounded at 10 s;
- treat the return value as a **boolean outcome only**. The returned string is
  discarded immediately: it is never written into the synthetic home, never
  placed in any environment, never returned to the harness, and never
  serialized into `prepared.json`, stdout, records, or logs;
- a `None`/failure outcome raises `shadow-credential-unusable` **before** the
  CLI subprocess is launched.

**Scope of the no-keychain claim.** This probe runs as the adapter process,
outside the trial sandbox, under the account home; `gh` may consult account
authentication there. The no-keychain guarantee (I4) applies strictly to the
**trial sandbox**, which is denied both keychain roots. Stating it any wider
would be false.

**Owner selection — traced, not assumed.** Three subcommands could own it:

| Subcommand | Semantics | Decision |
|---|---|---|
| `doctor` (`:6192`) | Health/qualification; already asserts an auth predicate at `:6211`, but only `is_file()` | **Owns it.** Replace the `is_file()` predicate with completeness + usability under shadow mode. This is where an unusable credential should surface before the stage claims anything. |
| `prepare` (`:5127`) | Per-trial; emits the sealed `prepared` record consumed by `run` | **Owns it after source completeness and before projection.** Runs one probe before emitting the command and profile, so an unusable account leaves no projected credential copies behind. |
| `run` (`:5792`) | Per-trial execution; today calls `copilot_auth_token` to inject `GH_TOKEN` | **Owns its one launch-time probe.** Under shadow mode the injection is removed and replaced by the same boolean probe, so `run` pays no *new* subprocess relative to today and does not duplicate the command-boundary check. |
| `normalize`, `collect` | Post-execution | Do not probe. |

**No new field is added to the `prepared` record.** Adding one would have to be
recomputed identically by the prepared-drift equality at `:5784`, creating a
drift surface for no gain. Keeping the probe as a precondition on both sides
leaves the sealed record's shape unchanged.

**Bound impact.** Today only `run` pays a `gh auth token` call per trial; after
this change `prepare` pays one too. Each is bounded at 10 s inside the adapter's
own `timeout_seconds`. The stage's provisional reservation must account for up
to one extra bounded 10 s call per trial.

### A3 — Process-path confinement of the projected credential files

This is the security core, and Round 1 found the original formulation
defective (D3).

**The defect.** `dreaming-vendor-adapter.py:4989` unconditionally loops over the
projected authentication paths and emits, for each one:

```
(deny file-write* (literal "<path>"))
(allow file-read* (literal "<path>"))
```

These are appended *after* the trial-root grant at `:4957`. An additive
confinement rule placed after them would not confine anything, because the
unconditional `allow file-read*` already grants the model process a literal
read.

**The fix.** Under `--shadow-contract` with vendor `copilot`, the adapter does
not emit those literal read allows for the three Copilot projected paths.
Instead it emits, at the end of the profile:

```
(deny file-read* file-read-metadata (subpath "<home>/.config/gh"))
(deny file-read* file-read-metadata (literal "<home>/.copilot/config.json"))
(deny file-write* (literal "<home>/.config/gh/hosts.yml"))
(deny file-write* (literal "<home>/.config/gh/config.yml"))
(deny file-write* (literal "<home>/.copilot/config.json"))
(allow file-read* file-read-metadata
       (require-all (literal "<home>/.config/gh/hosts.yml")
                    (process-path "<resolved cli binary>")))
(allow file-read* file-read-metadata
       (require-all (literal "<home>/.config/gh/config.yml")
                    (process-path "<resolved cli binary>")))
(allow file-read* file-read-metadata
       (require-all (literal "<home>/.copilot/config.json")
                    (process-path "<resolved cli binary>")))
```

The write denials on the three literals are preserved from today's behavior.

**Why `.copilot` is denied by literal, not by subpath.** `<home>/.config/gh`
holds nothing but projected authentication, so the whole subpath is denied.
`<home>/.copilot` is different: the CLI owns its session state there. Denying
that subpath's reads and metadata was implemented first and measured to break a
real trial — the CLI could create `session-state/<id>/events.jsonl` but could
not stat it, so the append failed with `File exists (os error 17)` and the run
never reached the model. The implemented rule therefore denies only the exact
projected literal under `.copilot`, and reads and writes elsewhere under
`<home>/.copilot` remain permitted by the trial-root grant. The set of
directories eligible for whole-subpath denial is named explicitly in
`SHADOW_DENIED_CREDENTIAL_ROOTS`; adding a projected path outside those roots
automatically produces a literal denial rather than silently widening one.

**No precedence claim.** This work order does **not** assert any SBPL
precedence or last-match-wins semantics. It specifies the *generated effective
policy* and requires that the confinement be proved **behaviorally**, by
executing reads, in CHK-A3 and CHK-A16. If the observed sandbox implementation
does not yield the intended effective policy from this rule ordering, the
implementation must restructure what it emits — for example by not emitting the
broad trial-root read grant over those two subpaths at all — until the executed
proof passes. The executed proof, not the rule text, is the acceptance
boundary.

**Platform prerequisite, already measured.** Probe 3 in the reframe record
confirmed on `sandbox-exec` that `deny file-read* (subpath …)` plus
`allow file-read* (require-all (subpath …) (process-path "<exact binary>"))`
permits the exact binary path and denies a byte-identical copy at another path.
That closes the question of whether the primitive can express this confinement
at all. It does **not** close whether the adapter's generated policy achieves
it, or how the real Copilot CLI behaves under it; those remain CHK-A3 and
CHK-A16.

**Precedent and constraint.** `process-path` confinement is already used in
this same profile for `network-outbound` (`:4985`). The existing refusal of
script-based (`#!`) CLI executables (`:4977`) exists precisely because such
executables cannot receive process-scoped access; that refusal must remain, and
A3 depends on it.

**Keychains.** Under shadow mode the broad keychain `file-read*` grants at
`:4950` are omitted. The measured probe authenticated with no keychain
projection, so the grant is unnecessary in this mode. Non-shadow mode keeps
them unchanged.

### A4 — Identity: `sandbox_id` only

Round 1 established that the revision-1 plan was impossible at runtime:

- `skill-evaluation.py:10770` hard-refuses any shadow executor whose
  `adapter_version != 1`;
- `skill-evaluation.py:10734` validates shadow executors against an **exact**
  key set via `require_exact_keys` at `:10742`;
- `skill-evaluation-harness.py:682` refuses any attestation response whose key
  set is not exactly `EXECUTOR_IDENTITY_KEYS` (`:57`), with the shadow superset
  at `:1888` adding only `real_backend` and `real_backend_source`;
- `EVALUATION_ADAPTER_VERSION` (`:3936`) is a module global feeding **both**
  executor and comparator identity, so it cannot be conditioned on
  `--shadow-contract` without changing non-shadow identity, violating non-goal 5
  and I8.

Therefore:

- `EVALUATION_ADAPTER_VERSION` stays `1`;
- no top-level identity field is added;
- no exact-key schema is edited;
- the boundary is sealed **inside the existing `sandbox_id` hash input**
  (`:4038`), which is already part of the attested identity and is validated
  only for shape (`require_sha256`, `skill-evaluation.py:10766`), leaving its
  content free.

Under `--shadow-contract` only, the `sandbox_id` input dictionary gains entries
describing the new confinement — projected-auth reads confined to the CLI
process path, keychains denied, no provider token in the trial environment. Its
nested `version` stays `1`: no existing consumer reads that nested value, and
the observable signal is the `sandbox_id` digest itself, which is compared
exactly by attestation. Bumping it would add an unread field.

The configured argv carries the concrete `--credential-root <account home>`
path, but the portable identity stays path-free: no host path enters
`sandbox_id`.

**Net identity effect.** Shadow-mode `sandbox_id` changes, and the adapter
executable digest changes with the adapter bytes. Every other identity field,
and every non-shadow identity value including the non-shadow `sandbox_id`, is
byte-identical.

### A5 — No token in the trial environment

`evaluation_environment` (`:4660`) keeps its fixed shape. Under
`--shadow-contract`, the run-time `GH_TOKEN` injection at `:5792` is **not**
performed: the trial authenticates from the projected files alone, exactly as
the measured probe did. `GH_TOKEN` and `GITHUB_TOKEN` are absent from the trial
environment.

## Reuse contract

| Reused component | File:line | Why it is reused rather than rebuilt |
|---|---|---|
| `--credential-root` argv contract | `build-evaluation-input-source.py:197`, `:289` | Already reviewed, already validates against the account home |
| `evaluation_credential_root` | `dreaming-vendor-adapter.py:4656` | Existing resolution point |
| `copy_auth_file` | `:1198` | Existing projection primitive |
| `copilot_auth_token` | `:1249` | Existing bounded usability probe; account-home bound, 10 s |
| `require-all` + `process-path` | `:4945`, `:4985` | Existing precedent in the same profile |
| `prove_boundary` | `:1497` | Existing private-data refusal proof |
| `_require_role_argv` | `dreaming-core.py:9533` | Existing argv enforcement for evaluator entries |

Nothing new is trusted. The trusted set shrinks in shadow mode: keychain reads
are removed and projected-file reads narrow from "any process in the sandbox"
to "the CLI process path only".

## Source-to-runtime data flow

```
operator config  <state>/adapters.json  (or $DREAMING_ADAPTER_CONFIG; dreaming-core.py:5596)
                 evaluators.<name>.argv  ──►  must contain --credential-root <account home>
                                              (enforced: dreaming-core.py:9533)
        │
        ▼
dreaming-core.configured_adapters (strict, :9597)  ──► RuntimeFailure on the whole
        │                                               config if the flag is absent
        ▼
shadow_execution_authority (:9554)  ──► stage available only if an evaluator entry exists
        │
        ▼
shadow evaluation stage  ──► harness  ──► harness_environment (:675) synthetic HOME
        │                                  (no parent env, no token)
        ▼
adapter subprocess, argv verbatim:  … --credential-root <account home> --shadow-contract
        │
        ├─ A1 validate raw path: symlink? dir? == pw_dir?        (refuse before anything)
        ├─ A2 project files into synthetic HOME, assert completeness
        ├─ A2b bounded usability probe (boolean only; secret discarded)
        ├─ A3 emit sandbox profile: deny subpaths + process-path allow
        ├─ A4 sandbox_id input records the confinement (shadow only)
        └─ A5 launch CLI under synthetic HOME, no GH_TOKEN
                │
                ▼
        Copilot reads .config/gh/* and .copilot/config.json  ── permitted: process-path matches
        model code / tools read the same paths               ── denied:   process-path differs
```

## Threat and failure model

| # | Threat / failure | Mechanism | Mitigation | Proof |
|---|---|---|---|---|
| F1 | Trial reads credential bytes and echoes them to the model | Sandbox allows the trial process to read projected files | A3 process-path confinement | CHK-A3, CHK-A16 |
| F2 | Credential material lands in a record | Token injected into env, then serialized | A5 no injection; A2b discards the probe result | CHK-A5, CHK-A18 |
| F3 | Projection silently incomplete → trials fail as `infrastructure_error` | `copy_auth_file` no-ops silently | A2 completeness assertions | CHK-A6 |
| F3b | Projection complete but the account is signed out → the same silent degradation | Existence proves nothing about validity | A2b bounded usability probe before model launch | CHK-A17 |
| F4 | Credential root points somewhere else | Operator misconfiguration | A1 account-home equality | CHK-A4 |
| F4b | Credential root is a symlink to the account home | `resolve()`-then-`is_symlink()` is vacuous | A1 raw-path symlink check before resolution | CHK-A4 |
| F5 | Non-shadow identity drifts | A global version or unconditional identity change | A4: version stays `1`; identity change is shadow-conditional | CHK-A9 |
| F5b | Identity change is rejected at runtime by an exact schema | Extra key or bumped version | A4 adds no key and no version bump | CHK-A8 |
| F6 | Real `HOME` reachable from the trial | Missing deny | Existing deny roots + `prove_boundary` | CHK-A7 |
| F7 | Keychain reachable from the trial | Existing broad grants | A3 omits them under shadow | CHK-A7 |
| F8 | Confinement defeated by an earlier unconditional allow | `:4989` literal read allows | A3 suppresses them under shadow | CHK-A3 |
| F9 | Fixture adapters break | Shared code path | Fixtures already pass `--credential-root`, but a fixture root can never be the account home; their shadow command invocations run through a test-local account-home launcher rather than any production relaxation | CHK-A10 |
| F10 | Installed host silently becomes authenticated by default | Generated config | `install.sh:702` does not generate `evaluators`; absent entry → unavailable | CHK-A12 |
| F11 | Process leak or timeout regression | New subprocess in `prepare` | Bounded 10 s; existing cleanup asserts | CHK-A13 |
| F12 | Removing the authority does not restore fail-closed behavior | Partial rollback | Rollback deletes the evaluator entry | CHK-A12 |

## Hard invariants

- **I1** — `skill-evaluation-harness.py` is byte-unchanged.
- **I2** — The parent environment is never forwarded to a trial.
- **I3** — The trial never receives `GH_TOKEN` or `GITHUB_TOKEN`.
- **I4** — The trial sandbox is denied both keychain roots.
- **I5** — No credential byte reaches stdout, `prepared.json`, the sandbox
  profile, any log, any artifact, or any receipt.
- **I6** — Projected credential files are readable **only** by the resolved CLI
  executable's process path.
- **I7** — Candidate blindness and executor content permissions are unchanged.
- **I8** — Non-shadow identity except the adapter executable digest,
  non-shadow sandbox profiles, and fixture behavior are byte-identical.
- **I9** — Every refusal happens before the model is launched.
- **I10** *(R2)* — `EVALUATION_ADAPTER_VERSION` remains `1` and no exact-key
  identity schema is modified.

## Acceptance criteria

| ID | Criterion | Checks |
|---|---|---|
| AC-T1 | A real authenticated Copilot trial completes under the synthetic `HOME` and returns a real verdict, not `authentication-required` | CHK-A1 |
| AC-T2 | Absent `--credential-root` in the adapter argv → refusal before any model use | CHK-A2, CHK-A19 |
| AC-T3 | Credential root not equal to the account home → refusal before any model use | CHK-A4 |
| AC-T4 | Raw credential-root symlink is refused **before** `expanduser`/`resolve` | CHK-A4 |
| AC-T5 | Incomplete projection (missing, wrong mode, empty, symlinked destination) → `shadow-credential-projection-incomplete` before model use | CHK-A6 |
| AC-T5b | Signed-out or unusable credential → `shadow-credential-unusable` from both `doctor` and `prepare`, with no CLI subprocess launched | CHK-A17 |
| AC-T6 | The trial process cannot read the projected credential files | CHK-A3, CHK-A16 |
| AC-T7 | The trial cannot read the real `HOME`, `~/.copilot` session content, `~/.github` beyond the exact projection, keychains, or the parent environment | CHK-A7 |
| AC-T8 | No provider token appears in the trial environment | CHK-A5 |
| AC-T9 | Identity is exactly the existing key set, `adapter_version` is `1`, and the shadow `sandbox_id` equals the expected shadow digest and differs from the non-shadow digest | CHK-A8 |
| AC-T10 | Non-shadow identity (apart from the adapter's own digest), non-shadow profile bytes, and non-shadow fixture behavior unchanged | CHK-A9, CHK-A10 |
| AC-T11 | An evaluator entry lacking the flag is refused by strict config validation | CHK-A19 |
| AC-T12 | A config with no evaluator entry is valid and leaves the stage unavailable | CHK-A12 |
| AC-T13 | Process cleanup and output/time bounds unchanged | CHK-A13 |
| AC-T14 | No credential material survives in any retained artifact | CHK-A14, CHK-A18 |
| AC-T15 | `skill-evaluation-harness.py` byte-unchanged | CHK-A15 |
| AC-T16 | Deleting the evaluator entry restores current fail-closed behavior: the config still validates, the stage reports unavailable, and zero durable writes occur | CHK-A12 |
| AC-T17 | Removing only the flag from a retained evaluator entry fails the **entire** config load — recorded as a negative fail-closed test, explicitly **not** a rollback and **not** "today's behavior" | CHK-A19 |
| AC-T18 | The usability probe's returned token never appears in stdout, `prepared.json`, the emitted profile, any environment, or any log | CHK-A18 |
| AC-T19 | The sealed `prepared` record's shape is unchanged and the prepared-drift equality still holds across a prepare/run pair | CHK-A11 |

## Rollback and fail-closed evidence

**Primary rollback — delete the evaluator entry.** Remove the entire
`evaluators.<name>` entry from the adapter config. The config remains valid
(`ROLE_CONFIG_KEYS` tolerates an absent or empty role map),
`shadow_execution_authority` (`dreaming-core.py:9554`) reports the stage
unavailable, and no trial runs. This is a config-only action, requires no
deploy, and produces zero durable writes.

**Secondary rollback — revert the code successor.** Restores the previous
adapter and `SHADOW_EXECUTOR_REQUIRED_ARGV`, at which point a retained evaluator
entry that still carries `--credential-root` remains valid, because the flag is
already an accepted adapter argument.

**Not a rollback — removing the flag (D6).** Deleting `--credential-root` from
a retained evaluator entry causes `_require_role_argv` (`:9533`) to raise
`RuntimeFailure` inside the **strict** `configured_adapters` (`:9597`), which
`validate_adapter_config` (`:9838`) uses. That aborts the **whole** config load,
not just the evaluator role, so unrelated source, executor, and publisher roles
stop resolving too. It is retained only as a negative fail-closed test
(AC-T17/CHK-A19) and must never be recommended to an operator as a recovery
step, nor described as "returning to today's behavior".

**Installed-host default.** `scripts/install.sh:702` generates `sources`,
`executors`, and `publishers` only. An installed host that never adds an
evaluator entry is already fail-closed by construction; nothing here makes the
stage default-open.

## Check contract

Every check is executed. At-boundary and past-boundary cases are paired at each
enforcing layer.

### Layer separation in the executed checks

Two enforcement layers are tested separately, because a single test that
crossed both would either prove neither cleanly or would tempt a production
relaxation to stay reachable.

- **Profile policy (CHK-A3, CHK-A16).** Exercised by loading the production
  adapter script and calling its own `evaluation_environment` and
  `evaluation_sandbox_profile` with a synthetic namespace, trial, and dummy
  projected files. The policy under test is therefore the production-generated
  bytes, and `sandbox-exec` runs against exactly those bytes. This deliberately
  does not go through the public command surface. No production flag,
  environment exception, or runtime seam exists or may be added to make it
  reachable, and the `DREAMING_EXECUTOR_TEST_ALLOW_ROOT`/`_ROOTS` escape is
  stripped so the generated policy cannot be vacuously permissive.
- **Command authority (CHK-A4, CHK-A19).** Owns the credential-root
  account-home refusal and the configured-argv requirement, exercised through
  the public adapter command.

This separation requires that the account-home authority of A1 live in argument
validation at the command boundary, **not** inside
`evaluation_credential_root` or the profile generator. The A1 invariant is
unchanged and is not weakened: it simply has one owner, so profile construction
remains independently testable with synthetic inputs after A1 lands.

The complete post-implementation campaign additionally runs real `prepare` and
`run` under valid account authority (CHK-A1, CHK-A11), so the two layers are
also proved composed, not only in isolation.

| ID | Layer | Check | Boundary |
|---|---|---|---|
| CHK-A1 | End to end | Real Copilot trial under synthetic `HOME` completes with a real verdict; no `authentication-required`, no `infrastructure_error` | at |
| CHK-A2 | Adapter argv | Shadow invocation without `--credential-root` refuses with `shadow-credential-root-missing`, launching no CLI | past |
| CHK-A3 | Profile policy, executed | Generate the profile directly from the production generator with synthetic inputs and dummy projected files, then under `sandbox-exec`: the exact configured reader path reads each of the three projected files, a byte-identical reader at a different path is denied on all three, and a workspace control read still succeeds. The platform primitive is already proved (reframe probe 3); this check proves the generated policy | at + past |
| CHK-A4 | Command authority | Account home accepted; a different existing directory, a raw symlink to the account home, a non-existent path, and a file are each refused with the specific code, before projection. This check solely owns the account-home authority that CHK-A3 bypasses | at + past |
| CHK-A5 | Executor env | The emitted trial environment contains neither `GH_TOKEN` nor `GITHUB_TOKEN`, and its key set is otherwise unchanged from today | at |
| CHK-A6 | Projection completeness | Each of: missing file, mode `0644`, zero size, symlinked destination raises `shadow-credential-projection-incomplete`; a complete projection passes | at + past |
| CHK-A7 | Sandbox, executed | `prove_boundary` still refuses protected `HOME`, config, and content reads; keychain roots are denied under shadow | past |
| CHK-A8 | Identity | Shadow attestation response key set is **exactly** `EXECUTOR_IDENTITY_KEYS ∪ {real_backend, real_backend_source}`; `adapter_version == 1`; `sandbox_id` equals the expected shadow digest and differs from the non-shadow digest; `normalize_shadow_executors` accepts it | at |
| CHK-A9 | Identity, non-shadow | Without `--shadow-contract`, every identity field except `adapter_executable_sha256`, including `sandbox_id`, and the emitted profile bytes, are identical to the base adapter loaded from the reviewed integration commit; `adapter_executable_sha256` equals the current file digest and necessarily differs | at |
| CHK-A10 | Fixtures | The fixture vendor-adapter tests pass with unchanged non-shadow behavior. Their **shadow Copilot command** invocations now require account authority, so they run the unmodified adapter through a test-local launcher that reports the fixture credential root as the account home. Every other production check still executes; the account identity itself is owned by CHK-A4 | at |
| CHK-A11 | Prepare | `prepared` record shape is unchanged and the drift equality at `:5784` still holds across a prepare/run pair | at |
| CHK-A12 | Config | With the evaluator entry deleted: config validates, `shadow_execution_authority` reports unavailable, zero durable writes | at |
| CHK-A13 | Process | Timeout and cleanup asserts unchanged; the added `prepare` probe is bounded and leaves no orphan | at |
| CHK-A14 | Artifacts | Grep every retained artifact from a real trial for the projected credential's distinguishing markers; zero hits | past |
| CHK-A15 | Harness | `skill-evaluation-harness.py` digest equals the reviewed integration base, corroborated against an independent copy of the base bytes so the pin cannot be satisfied by the file under test alone | at |
| CHK-A16 | Sandbox, past boundary | Under a real trial profile, a process whose path is not the resolved CLI attempts each projected path in the synthetic home and each corresponding path in the real account home; all denied, while a workspace control read succeeds | past |
| CHK-A17 | Usability probe | With the projection complete but the account unusable, `doctor` and `prepare` each raise `shadow-credential-unusable` and launch no CLI; with a usable account both pass | at + past |
| CHK-A18 | Secret non-serialization | The probe's returned token value appears in no stdout, `prepared.json`, profile, environment dump, or log produced by a full prepare/run pair | past |
| CHK-A19 | Strict config | An evaluator entry lacking `--credential-root` causes `validate_adapter_config` to fail the whole config, and unrelated roles stop resolving — asserted as the negative fail-closed behavior, not as a rollback | past |

## Digest, regeneration, and consumer closure

Because no schema and no version change, closure is limited to the adapter's
**byte digest** and the artifacts that pin it.

**Consumers that must continue accepting `adapter_version: 1` with a changed
shadow `sandbox_id`:**

| Consumer | File:line | Why it still accepts |
|---|---|---|
| Harness attestation | `skill-evaluation-harness.py:682` | Compares the key set exactly; the key set is unchanged |
| Harness shadow attest | `skill-evaluation-harness.py:1888`, `:1939` | Same key set plus the two shadow keys |
| Shadow executors normalizer | `skill-evaluation.py:10734`, `:10742` | Exact keys unchanged; `:10766` validates `sandbox_id` shape only (`require_sha256`), not its value; `:10770` requires version `1`, which holds |
| Policy identity validation | `skill-evaluation.py:719`, `:814` | Shape-level; no pinned `sandbox_id` |
| Core identity validation | `dreaming-core.py:521` | Shape-level |

**No stored `sandbox_id` needs updating.** The shadow executors document is
derived per pass from a live `version` probe and re-attested in the same pass,
so the expectation and the observation come from the same adapter bytes. There
is no persisted `sandbox_id` pin to regenerate.

**Byte pins that must be regenerated together, in this order:**

1. Compute the new adapter `sha256` after the code change.
2. Update `TRUSTED_AUTHORING_ADAPTER_SHA256`
   (`skill-evaluation.py:88`), whose consumers are `:439`, `:5114`, `:5278`.
3. Update the builder ratchet test expectation
   (`test-evaluation-input-source-builder.sh:160`).
4. Regenerate the evaluation input source bundles that pin adapter identity —
   `authoring/` (`skill-evaluation.py:3235`), `reviews/` (`:3468`), and
   `repair/` (`:4477`) — via `scripts/build-evaluation-input-source.py`.
5. Diff the regenerated bundles. The **only** expected change is the adapter
   executable digest. Any other delta is a finding.
6. Re-run the generator and confirm the second run is a no-op.

**Comparator (D6/#6 correction).** There is **no** comparator role group in the
adapter config — `ROLE_CONFIG_KEYS` covers `sources`, `executors`,
`publishers`, and `evaluators` only. The comparator identity
(`dreaming-vendor-adapter.py:4066`) appears solely inside the regenerated source
bundles, and it changes for exactly one reason: the shared adapter byte digest.
Its argv and every other identity field are unchanged. Revision 1's claim that
a comparator config group must be regenerated is withdrawn.

## Measured implementation outcome

The implementation slice was proved with a real foreground tracer, not with
fixtures alone. Retained redacted evidence:
`~/.copilot/session-state/c7947aa7-3025-4b4e-977d-294626e8e949/files/task-opportunity-profile-funnel-proof/shadow-trial-auth-live/`.

| Observation | Result |
|---|---|
| `doctor` under the real account home with the projection complete | healthy |
| Real Copilot under the adapter-generated profile, environment, and synthetic `HOME`, with no `GH_TOKEN`, no `GITHUB_TOKEN`, and no keychain grant | exit 0, 78 native events including `assistant.message` carrying the candidate-skill marker |
| Authentication error in the run output | none |
| Non-CLI process path reading each of the three projected files in the synthetic home | denied, all three |
| Non-CLI process path reading the same three paths in the real account home | denied, all three |
| Workspace control read | allowed |
| Projected credential copies after the run | removed |
| Credential-marker scan of every retained artifact after removing the ephemeral CLI package cache | zero hits |

Two measured corrections to the specified policy are recorded above: the
`.copilot` subpath denial was replaced by an exact literal denial after it was
observed to break the CLI's own session-state append, and CHK-A9 excludes
`adapter_executable_sha256`, which necessarily changes with the adapter bytes.

**Resolved dependency.** The separate native-event compatibility slice admits
the exact observed Copilot vocabulary, including
`session.mcp_server_removed`. The complete adapter `prepare`, `run`,
`normalize`, and `collect` path now succeeds without bypassing native
validation; the exact current receipt is the acceptance authority.

## Round 1 and Round 2 finding resolution

| ID | Reviewer | Finding | Revised section | Disposition |
|---|---|---|---|---|
| D1 | Terra | `adapter_version = 2` and a top-level `credential_authority` field are rejected by exact schemas while the design freezes the harness | A4; Non-goal 4; L15; I10; CHK-A8 | **Accepted.** Version stays `1`; no top-level field. The boundary is sealed in the existing `sandbox_id` hash input. Verified: `skill-evaluation.py:10770` hard-refuses `adapter_version != 1`. |
| D2 | Terra | Global `EVALUATION_ADAPTER_VERSION` cannot be gated by `--shadow-contract` and would change non-shadow identity, violating the non-goal and I8 | A4; L15; F5; CHK-A9 | **Accepted.** No version gating is attempted. Only the shadow-mode `sandbox_id` input is conditional. |
| D3 | Opus | Unconditional projected-file literal read allows at `:4989` are emitted after the trial-root grant and would defeat process-path confinement | A3; L13a; F8; CHK-A3 | **Accepted.** Under shadow mode those literal allows are suppressed and replaced by deny-subpath + `require-all(subpath, process-path)`, and the confinement is proved behaviorally rather than by precedence text. |
| D4 | Opus | Existence/mode/size proves projection completeness, not credential usability; doctor checks only `is_file` | A2 (renamed *completeness*); A2b; L20; F3b; AC-T5b; CHK-A17 | **Accepted.** A bounded adapter-owned `gh auth token` usability probe is added. `doctor`, `prepare`, and `run` each perform one probe at their own pre-launch boundary; `run` replaces today's token call rather than adding another one. |
| D5 | Opus | Raw symlink refusal must occur before `expanduser`/`resolve` and is stricter than the cited builder predicate | A1; AC-T4; F4b; CHK-A4 | **Accepted.** Ordering is specified explicitly and the divergence from `build-evaluation-input-source.py:289` (which resolves first, making its `is_symlink()` vacuous) is documented as a deliberate tightening. |
| D6 | Opus | Removing `--credential-root` makes strict `configured_adapters`/`validate_adapter_config` fail globally, so it is not a valid rollback | Rollback section; AC-T16; AC-T17; L23a; CHK-A19 | **Accepted.** Primary rollback is deleting the evaluator entry. The missing flag is retained only as a negative fail-closed test and is explicitly not a rollback and not "today's behavior". |
| T1 | Terra | Executor identity is validated against exact key sets in both the harness and the evaluator, so any added identity key is rejected at runtime | A4; L3; L5; L25; I10; CHK-A8 | **Accepted.** No key is added. CHK-A8 asserts the exact existing key set plus the two shadow keys, and asserts the expected `sandbox_id`. |
| R1 | Opus (Round 2) | Revision 2's A1 falsely claimed `_require_role_argv` already requires `--binary` and `--deny-root`; `SHADOW_EXECUTOR_REQUIRED_ARGV` is today exactly `("--shadow-contract", "--model")` and `dreaming-core.py` references neither other flag | A1 ("Exact current fact" and "`--binary` stays adapter-owned"); the challenged-rule paragraph; L8; L22 | **Accepted.** The false clause is withdrawn. `--credential-root` becomes the **third** required configured flag alongside `--shadow-contract` and `--model`. `--binary` selection stays adapter-owned and is attested through `cli_executable_sha256` and the A3 `process-path` confinement; no new configured requirement is added for `--binary` or `--deny-root`, because the existing design does not need one. |

## Definition of Done: shadow trial authentication boundary

This work order is done when **all** of the following hold. Each item is
distinct from any other work order's DoD in this repository.

1. `SHADOW_EXECUTOR_REQUIRED_ARGV` requires `--credential-root`, and a
   configured shadow evaluator entry lacking it is refused by strict validation
   (CHK-A19).
2. The adapter validates the credential root raw-symlink-first, then existence,
   then account-home equality, refusing before any projection or model launch
   (CHK-A4).
3. Projection completeness is asserted fail-closed and named as completeness
   only (CHK-A6).
4. A bounded adapter-owned usability probe runs before every model launch, its
   returned secret is discarded immediately, and an unusable credential refuses
   from both `doctor` and `prepare` (CHK-A17, CHK-A18).
5. The emitted shadow sandbox profile suppresses the unconditional projected-file
   read allows, denies the two projected subpaths, and permits reads only from
   the resolved CLI process path — proved by executed reads, not by rule text
   (CHK-A3, CHK-A16).
6. Keychain grants are absent from the shadow profile and `prove_boundary` still
   refuses protected reads (CHK-A7).
7. No provider token appears in the trial environment (CHK-A5).
8. `EVALUATION_ADAPTER_VERSION` is still `1`, no identity key is added, and the
   only changed identity value is the shadow `sandbox_id` (CHK-A8, CHK-A9).
9. `skill-evaluation-harness.py` is byte-unchanged (CHK-A15).
10. Fixture and unauthenticated adapter tests pass unchanged (CHK-A10).
11. The `prepared` record shape and its drift equality are unchanged (CHK-A11).
12. Byte-pin closure is complete: adapter digest, `TRUSTED_AUTHORING_ADAPTER_SHA256`,
    the ratchet test, and the three regenerated bundles, with an explained diff
    and an idempotent second regeneration.
13. Deleting the evaluator entry leaves a valid config and an unavailable stage
    with zero durable writes (CHK-A12).
14. A real end-to-end shadow trial returns a real verdict, unblocking PG-7
    (CHK-A1).
15. No retained artifact contains credential material (CHK-A14, CHK-A18).
