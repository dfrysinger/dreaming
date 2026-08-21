# Remote evaluation subject bridge

## Objective

Let the Mac mini's existing Dreaming owner safely author and run evaluations
for skills inventoried on the MacBook without reading a same-path Mac mini
skill, changing either machine's installed skill roots, or creating another
scheduler, worker, or durable queue.

## Non-goals

- Do not move the Dreaming schedule, writer lease, halt switch, claim ledger,
  evaluation registry, or mutation authority off the Mac mini.
- Do not install a Dreaming daemon, launch agent, filesystem mount, or
  long-lived helper on the MacBook.
- Do not run the trusted evaluator or model process over SSH.
- Do not synchronize evaluation authority back into the MacBook's Dreaming
  state or alter the remote census receipt after collection.
- Do not transport transcripts, session files, credentials, home-directory
  state, plugin metadata outside the selected skill, or an entire estate in
  anticipation of later work.
- Do not fall back to a Mac mini path merely because its text matches the
  MacBook path.
- Do not generalize this protocol to arbitrary remote files or hosts. The only
  supported caller is the installation-pinned MacBook estate receiver already
  used by the Mac mini owner.

## Lane

This is a **critical** change. It moves skill content across a host boundary
and changes the identity used by evaluation readiness and authority.

The fail-closed state is remote-subject support disabled. A non-local census
row then remains visible with `remote_candidate_unavailable`, no claim is
reserved, and no model process starts.

Rollback disables remote-subject support in the installation-owned adapter
configuration. Existing candidate snapshots and evaluation records remain
read-only history. Rollback does not delete evidence, change either machine's
skill roots, add a scheduler, or make a remote row eligible through a local
path.

## Existing pieces this builds on

The bridge extends existing owners rather than introducing parallel ones:

- `scripts/ssh-estate-census.py` already pins the remote receiver and estate
  collector bytes, opens the bounded SSH connection, validates one canonical
  response, and binds the result to a receiver identity.
- `dreaming-estate.py` already discovers declared skill roots without following
  symbolic links and records every skill file digest.
- `dreaming-core.py` already owns the four-hour run, writer lease, halt checks,
  same-run census and usage receipts, derived in-memory queue, one-claim run
  limit, and content-root validation.
- `skill-evaluation.py` already owns candidate inventory, immutable input
  manifests, readiness transitions, claim accounting, evaluation receipts, and
  current authority.
- The sealed evaluation-input content root already proves that test inputs are
  separate from installed skill roots and cannot become executable authority.

The missing part is a subject boundary. The current code treats an absolute
path as both the location to read and the identity to govern. That is valid
only when collection and evaluation happen on the same host. A remote subject
needs separate content and identity fields.

The existing census command also relies on the user's normal SSH host-key
configuration. Remote candidate transport adds a dedicated installation-owned
known-hosts file containing exactly the approved MacBook host public key. Its
SSH command uses `StrictHostKeyChecking=yes`,
`UserKnownHostsFile=<installed-file>`, the fixed remote account and address,
and the existing pinned receiver-code identity. A response proves nothing
unless both the SSH host key and the receiver identities match.

## Subject identity

Every evaluation command consumes a validated subject:

```json
{
  "schema_version": 1,
  "origin_host_id": "receiver identity",
  "origin_root_id": "declared estate root identity",
  "origin_relative_path": "path beneath the declared root",
  "origin_path": "/absolute/path/on/origin",
  "canonical_capability_id": "sha256:...",
  "origin_inventory_sha256": "sha256:...",
  "candidate_id": "sha256:... or null before fetch",
  "content_root": "/local/read-only/snapshot"
}
```

`content_root` says where trusted code reads bytes. It is never used as a
portfolio identity.

The subject key is the canonical digest of `origin_host_id`, `origin_root_id`,
and `origin_relative_path`. These fields identify one stable physical subject
without depending on its bytes. `canonical_capability_id`,
`origin_inventory_sha256`, and `candidate_id` are version identities. The
candidate ID is the existing evaluator digest of the exact ordered path,
SHA-256, and size inventory reconstructed beneath `content_root`.

Readiness pointers, manifests, claims, evaluation receipts, and authority are
keyed by the subject key and candidate ID. Two hosts with the same absolute
path cannot share state. Two physical instances with identical bytes cannot
share state unless their complete subject identity is also identical.

For personal and project roots, the existing canonical capability ID includes
the inventory digest and therefore changes when content changes. It must never
be used as the stable pointer key. When a later census retains the same stable
subject fields but changes any version identity, the overlay marks prior
readiness and evaluation as stale for that subject. A later claim supersedes
the prior candidate version under the same subject key; it does not create an
unrelated subject or leave old authority current.

Local subjects use the same shape. Their origin host is the locally collected
host, their origin path and content root name the same real directory, and no
transport occurs. This keeps one evaluator path for local and remote work.

## One-subject transport

The census response remains unchanged and content-addressed. Before any remote
snapshot exists, the local overlay contains a bootstrap row derived only from
the retained census:

```json
{
  "subject_key": "sha256:...",
  "canonical_capability_id": "sha256:...",
  "origin_inventory_sha256": "sha256:...",
  "candidate_id": null,
  "state": "remote_candidate_not_fetched"
}
```

This is complete overlay coverage but grants no readiness or evaluation
authority. It lets queue derivation select the first otherwise-authorable
remote row. After the owner derives the queue, it may request only that row. It
sends the already retained census snapshot digest, receiver identity, origin
host, root ID, relative path, canonical capability ID, absolute path, and
expected inventory digest to the installation-pinned receiver.

The receiver rebuilds the declared root configuration, resolves exactly one
physical instance, and requires every requested identity to match a fresh
inventory. It then opens that skill root without following symbolic links and
returns one canonical response containing:

- the complete ordered origin inventory, including excluded sidecars;
- the ordered transported candidate inventory;
- the exact byte length and SHA-256 of every file;
- base64-encoded file bytes;
- the evaluator-compatible candidate ID;
- the census snapshot and receiver identities supplied by the owner; and
- a canonical transport receipt digest.

The protocol is bounded to one skill, 512 regular files, 8 MiB per file,
32 MiB total decoded content, and 48 MiB encoded output. A limit breach,
identity drift, changed-during-read file, undeclared path, symbolic link,
special file, duplicate path, malformed encoding, or receiver mismatch
returns one refusal and no candidate bytes are published.

The receiver never transports the evaluator sidecars `.agent-created`,
`.agent-created.json`, `.promotion-reviewed.json`,
`.skill-evaluation-cases.json`, `.skill-evaluation-policy.json`, or `.pinned`.
It records their path, size, and digest as excluded origin evidence so the
complete origin inventory can still match the census. The candidate ID uses
the transported inventory, matching the evaluator's existing sidecar
exclusion. Cases and policy come only from the separately sealed
evaluation-input root.

Before encoding any transported byte, the receiver applies the versioned
remote-source content policy to every included file. Included files must be
valid UTF-8 text. The policy rejects private-key blocks, bearer values,
credential or token assignments with non-placeholder values, session-state
payloads, known provider credential shapes, and unsupported content classes.
It does not reject ordinary documentation merely for naming a home path,
transcript, credential concept, or environment variable. A binary file or
rejected value refuses the whole subject as
`remote_candidate_content_unsafe`; content is never redacted into a different
candidate. The refusal returns only bounded path and reason metadata and emits
no candidate file bytes.

The receiver-side scan is an early bandwidth-saving rejection, not the trust
boundary. The transport receipt records its policy version and digest. After
decoding into private staging, the Mac mini applies its installation-sealed
copy of the same policy to every candidate file and refuses publication on any
match. The mini's policy may be newer than the receiver's and is authoritative.
A stale or bypassed remote scan therefore cannot publish unsafe bytes. Policy
version disagreement is visible in the refusal or retained receipt and never
causes the mini to use the weaker policy.

The receiver reads each file relative to retained directory descriptors with
no-follow behavior. It compares device, inode, size, modification time, and
change time before and after the bounded read. The inventory digest and
candidate ID are computed from the bytes that are returned, not from an
earlier path scan.

## Candidate snapshot publication

The Mac mini decodes the response into a private same-filesystem staging
directory beneath the existing evaluation state root. It rejects absolute,
empty, parent-traversing, duplicate, reserved, or non-canonical relative paths.
It checks every decoded size and digest, reconstructs both inventory
identities, requires them to match the request and receipt, and applies the
authoritative local remote-source content policy before publication.

The complete snapshot and its receipt are published together through an
atomic create-only rename at:

```text
evaluations/v2/remote-subjects/
  <subject-key>/<candidate-id>/
    candidate/
    transport-receipt.json
```

Files and directories become owner-read-only before publication. The published
root is outside every installed skill root, plugin root, project skill root,
publisher root, and Copilot built-in root. A collision is accepted only when
the complete retained receipt and inventory are byte-identical.

The snapshot store has a 1 GiB installation-sealed aggregate ceiling. Before
staging, the owner requires space for twice the maximum decoded subject plus a
256 MiB reserve for the claim ledger and registry. Crossing either boundary
records `remote_candidate_store_full`, publishes nothing, reserves no claim,
and starts no model process. Snapshots are reconstructible evidence, but they
are not silently deleted by normal runs. Cleanup below the retained ceiling is
a separate evidence-retention policy and is outside this change.

## Queue and state overlay

The remote census receipt remains the authority for origin inventory,
enablement, dependencies, and usage. The Mac mini registry remains the
authority for input readiness, claims, evaluations, and current authority.
Neither source is rewritten to look like the other.

The owner builds a local evaluation overlay for every enabled census row. A
never-fetched row gets the non-authoritative bootstrap shape above. A fetched
row resolves its subject key and exact candidate ID from the retained transport
receipt, then resolves readiness and evaluation from the mini registry. The
overlay is canonical, content-addressed, and binds:

- the census and usage receipt digests;
- receiver identity and origin host;
- every capability, subject key, candidate ID, and local evaluation state; and
- the evaluator and registry schema identities.

Queue derivation requires the raw census, raw usage, and local overlay to cover
the same enabled capability set. The queue uses remote census facts for usage
priority and dependency safety, then uses the local overlay for readiness and
evaluation state. Missing overlay coverage refuses the lane. A remote
collector's path-local evaluation field is retained for audit but cannot grant
or remove Mac mini authority.

When a census version identity differs from the latest fetched version for the
same subject key, the overlay records `remote_candidate_changed` and identifies
the prior candidate as superseded. The queue may fetch the new version once,
but cannot treat prior readiness or evaluation as current.

The dashboard displays the origin host and distinguishes:

- `Remote candidate not fetched`
- `Remote candidate changed before fetch`
- `Remote candidate too large`
- `Remote candidate snapshot ready`
- the existing readiness and evaluation states from the local overlay

It never labels a local same-path directory as the remote candidate.

## Owner flow

For one four-hour run:

1. The existing owner acquires the writer lease and checks halt.
2. It receives and records the exact remote census and usage receipts.
3. It builds the local evaluation overlay and derives the in-memory queue.
4. It selects at most the first otherwise-authorable row.
5. For a remote row without an exact snapshot, it rechecks halt and lease,
   performs the one-subject bounded fetch, and publishes the verified snapshot.
6. It rebuilds the subject from the retained receipt and re-derives the row.
7. It rechecks halt and lease, then reserves the existing daily claim.
8. `skill-evaluation.py` verifies the subject, candidate ID, sealed input pack,
   writer fence, and model budgets before the first model call.
9. Every manifest, review, readiness transition, evaluation receipt, and
   authority record retains the subject key and origin fields.
10. A later run reads the mini-owned overlay, so completed work does not return
    to `input_missing` merely because the MacBook has no local copy of the
    mini's registry.

Candidate transport happens before claim reservation and starts no model
operation. A failed transport therefore does not spend one of the four daily
model-backed claims. It does consume the run's one-subject transport allowance,
so one repeatedly failing row cannot trigger unbounded scanning or network
work.

## Failure model and hard invariants

### Same path, different host

A local directory may exist at the remote origin path. Non-local rows never
read it. Only a verified snapshot whose receipt binds the remote host,
capability, path, inventory, candidate ID, census snapshot, and receiver can
become the subject content root.

### Candidate changes after census

The targeted receiver rereads the skill and compares the fresh identity with
the retained census identity. Drift produces
`remote_candidate_changed_before_fetch`. It does not fall back to old bytes or
local bytes.

### Partial or tampered transport

Any missing file, extra file, changed byte, wrong size, malformed base64,
receipt mismatch, or publication collision refuses before claim reservation.

### Lease loss or halt

The owner checks halt and lease before transport, after transport, before
publication, before claim reservation, and before every existing authority
transition. Snapshot publication is one atomic create-only rename of immutable,
receipt-bound evidence and does not grant readiness, claim, evaluation, or
mutation authority. If lease loss races the rename, the snapshot is either
absent or complete; the losing owner performs no later write. A successor
revalidates the receipt, census binding, and its own lease before the snapshot
can be used. Halt or lease loss therefore permits no post-loss claim, pointer,
readiness, evaluation, or authority write. An unpublished staging directory
carries no authority.

### Origin-specific runtime dependency

The model and harness execute on the Mac mini. If a case requires an
origin-only application, credential, device, or local service, authoring must
record `insufficient_information` or execution must record an exact unavailable
result. The system cannot describe a Mac mini run as proof of MacBook runtime
behavior.

### State split

The raw remote census cannot grant evaluation authority, and the local overlay
cannot change remote enablement, usage, dependencies, or candidate identity.
Both exact receipts are required.

## Migration

There is no production remote-subject authority to migrate because the
portfolio remains report-only and every real capability is still
`input_missing`.

Local evaluation records retain their current path-derived keys through a
versioned local-subject adapter. New remote records use subject keys. The
adapter refuses a path-only record for a non-local host. Once all deterministic
fixtures use explicit local subjects, a later migration may rewrite local
records to subject keys, but that rewrite is not required for remote support.

Installation adds a disabled `remote_evaluation_subjects` object beside the
existing evaluation-input owner configuration. It pins the receiver command,
dedicated known-hosts file and host-key digest, remote host identity, content
limits, snapshot root, and protocol version.
Enablement requires self-test and a valid empty or replayable snapshot store.

Remote transport code is installed on the MacBook at a new immutable,
versioned receiver-bundle path. The existing census command continues to call
its existing pinned paths and hashes throughout rollout and rollback. The new
disabled configuration points only the transport command at the new bundle and
pins its receiver and collector hashes independently. Rollout order is:

1. publish and self-test the new immutable receiver bundle on the MacBook;
2. install the dedicated host-key pin and disabled transport configuration on
   the Mac mini;
3. prove the old census and usage command still succeeds unchanged;
4. self-test one refused and one valid transport without publication;
5. enable remote subjects only after the candidate store and owner self-test
   pass.

Rollback disables the transport object and may remove its config reference,
but does not replace the old census pins or paths. Removing the versioned
receiver bundle is optional cleanup only after no retained receipt references
it.

## Rollback

Rollback sets `remote_evaluation_subjects.enabled` to `false` while leaving the
existing owner in reconcile-only or report-only mode. The owner may replay a
pending terminal transition but cannot fetch a subject, reserve a new remote
claim, or start a model call for a remote row.

Proof of fail-closed rollback requires:

- the same remote row remains visible as `remote_candidate_unavailable`;
- a same-path local directory is not read;
- no new claim, model process, readiness transition, evaluation receipt, or
  authority record appears;
- all retained snapshots and prior receipts remain byte-identical and
  readable; and
- scheduler inventory, writer lease behavior, halt behavior, and installed
  skill-root inventories remain unchanged; and
- the original census and usage command still succeeds with its unchanged
  receiver paths and pins.

## Deterministic check contract

### REMOTE-SUBJECT-CHK-01: Same-path substitution refusal

- **Protects:** A Mac mini directory cannot impersonate a MacBook skill.
- **Setup:** A remote census row and a different local skill use the same
  absolute path. Remote support is disabled, unavailable, malformed, and then
  valid in separate cases.
- **Pass:** The first three cases reserve no claim and read no local candidate
  file. The valid case reads only the verified snapshot and binds its candidate
  ID.
- **Failure:** Any remote result uses the local directory or becomes runnable
  without the exact transport receipt.
- **Why:** It directly exercises the topology that exposed the defect.

### REMOTE-SUBJECT-CHK-02: Host-separated authority

- **Protects:** Identical paths on different hosts cannot share readiness or
  evaluation authority.
- **Setup:** Create two subjects with the same path and content but different
  origin hosts. Then change one personal subject's content while retaining its
  host, root, and relative path.
- **Pass:** Subject keys, pointers, claims, receipts, and authority paths are
  distinct across hosts. The edited personal skill retains its subject key,
  gains a new version, and supersedes rather than orphans prior authority.
- **Failure:** A transition for either subject changes the other's current
  state, or a content edit creates an unrelated subject or leaves stale
  authority current.
- **Why:** It proves host identity is part of authority rather than display
  metadata.

### REMOTE-SUBJECT-CHK-03: Bounded exact transport

- **Protects:** SSH transport cannot become an arbitrary or unbounded file
  copy.
- **Setup:** Exercise a valid skill and each path, file-count, per-file,
  total-byte, encoded-output, aggregate-store, free-space, symbolic-link,
  special-file, changed-during-read, inventory-drift, receiver-identity,
  SSH-host-key substitution, credential-bearing, binary, and
  evaluator-sidecar boundary. Include a response that passed a stale remote
  scanner but fails the mini's policy, plus benign documentation that mentions
  a home path, transcript, credential concept, and environment variable.
- **Pass:** Only the valid exact inventory is returned and published.
- **Failure:** An invalid byte reaches the snapshot store or one refusal leaves
  a complete-looking root.
- **Why:** It proves the privacy, resource, and candidate-integrity boundary.

### REMOTE-SUBJECT-CHK-04: Raw receipt and local overlay separation

- **Protects:** Remote inventory and local evaluation authority cannot replace
  each other.
- **Setup:** Remove, alter, truncate, duplicate, or cross-bind census, usage,
  overlay, registry, and candidate identities. Include a never-fetched remote
  row with a bootstrap overlay.
- **Pass:** Queue derivation refuses incomplete coverage and never treats the
  remote path-local evaluation field as local authority. The bootstrap row can
  be selected for one fetch but cannot become ready or current.
- **Failure:** A row becomes runnable or current from only one side.
- **Why:** It proves that cross-host state remains explicit rather than
  silently merged.

### REMOTE-SUBJECT-CHK-05: One-run and lease bounds

- **Protects:** Candidate fetch does not bypass the existing owner limits.
- **Setup:** Seed multiple remote rows, transport failures, halt and lease loss
  before and during read, after decode, before publication, and before claim.
- **Pass:** At most one subject is requested per run, at most one model-backed
  claim is reserved, a publication race leaves only an absent or complete
  immutable snapshot, no post-lease authority write occurs, and every
  unfinished row remains visible with an exact reason.
- **Failure:** Scanning fetches multiple skills, a failed fetch starts a model,
  or a losing owner publishes claim, readiness, evaluation, or authority
  state.
- **Why:** It preserves the reviewed scheduling and recovery boundary.

### REMOTE-SUBJECT-CHK-06: Origin-runtime honesty

- **Protects:** A Mac mini model run is not reported as proof of unavailable
  MacBook behavior.
- **Setup:** A safe contract-only case and a case requiring an origin-only
  application or credential.
- **Pass:** The first may progress. The second becomes explicit
  `insufficient_information` or unavailable evidence with origin and execution
  hosts shown.
- **Failure:** Missing origin runtime becomes a passing evaluation.
- **Why:** It keeps evaluation evidence narrower than the environment that
  produced it.

### REMOTE-SUBJECT-CHK-07: Rollback preservation

- **Protects:** Remote evaluation can be stopped without deleting evidence or
  changing runtime roots.
- **Setup:** Disable the feature with retained snapshots, one completed claim,
  one open claim, a same-path local skill, and independently pinned old census
  and new transport receiver bundles.
- **Pass:** Reconcile-only recovery completes allowed pending state, starts no
  fetch or model call, preserves all evidence, leaves both machines' installed
  roots and the scheduler unchanged, and the old census command still succeeds
  through its unchanged path and pins.
- **Failure:** Rollback deletes history, reads the local alias, starts work, or
  changes a runtime root.
- **Why:** It proves the critical boundary fails closed.

## Acceptance criteria

- A non-local census row is never validated or evaluated through its origin
  path on the Mac mini.
- One targeted, pinned, bounded receiver call transports at most one exact
  skill per owner run.
- Published snapshots are content-verified, read-only, outside runtime roots,
  contain no evaluator sidecars or unsafe content, and are bound to the raw
  census, SSH host-key, and receiver receipts.
- Evaluator state uses host-qualified subject identity and exact candidate
  identity; stable subject identity does not include content-derived capability
  identity, and local storage paths grant no authority.
- Queue readiness and evaluation state come from a complete mini-owned overlay,
  including a non-authoritative first-fetch bootstrap state, while usage,
  dependencies, enablement, and origin identity remain bound to the raw remote
  receipts.
- Halt, lease, one-per-run, four-per-day, timeout, token, output, and operation
  limits remain fail closed.
- The dashboard explains remote fetch, drift, size, readiness, origin host, and
  execution host in ordinary language.
- Disabling the feature starts no new remote work and preserves all evidence,
  scheduler state, and installed skill roots.

## Definition of Done: Remote evaluation subject bridge

- [x] REMOTE-SUBJECT-CHK-01 through REMOTE-SUBJECT-CHK-07 pass.
- [x] The real 19-skill settled-zero cohort resolves to 19 host-qualified
      subjects without reading same-path Mac mini skills; each subject either
      progresses or records an exact bounded inability.
- [x] At least one real remote subject completes input authoring and bounded
      evaluation, or records an independently reviewed safe inability, under
      the unchanged Mac mini scheduler.
- [x] Desktop and 390-pixel browser proof shows origin host, execution host,
      remote snapshot state, readiness, evaluation state, and report-only
      controls without overflow or browser errors.
- [x] Installed proof shows the unchanged single four-hour scheduler, writer
      lease, halt switch, one-subject-per-run, one-claim-per-run, and
      four-claims-per-day limits.
- [x] Rollback proof preserves snapshots, registry and evaluation history,
      scheduler inventory, and both machines' installed skill-root inventories.
- [x] The complete design and implementation have no unresolved in-scope
      must-fix review finding.
- [x] Design, implementation, real receipts, browser proof, installed proof,
      rollback proof, and durable baton references are committed locally;
      nothing is pushed.
