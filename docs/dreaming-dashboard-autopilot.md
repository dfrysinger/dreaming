# Dreaming dashboard unattended charter

## Objective

Achieve the Definition of Done in `docs/dreaming-dashboard-design.md`, the
"Dreaming Dashboard Definition of Done" section: implement and locally land the
private Dreaming monitoring dashboard. Keep working through the design; finish
only once every item in the "Dreaming Dashboard Definition of Done" section is
verifiably met.

## Charter

Keep building against the work order at
`docs/dreaming-dashboard-design.md` in
`/Users/dfrysinger/code/dreaming` on `feature/multi-cli-dreaming`. Follow the
required process skills below. Use rubber-duck to resolve implementation
ambiguity or a blocked path. Keep this execution baton current so a future
agent can identify the active phase, landed work, remaining work, and live-proof
receipt. Use subagents for genuinely independent work that needs separate
context. Do not push. Keep all commits local for this run. Decide every
reversible question autonomously. Real local model calls, LaunchAgent canaries,
browser interaction, and screenshots are authorized within reason. Stay on this
course until the objective's Definition of Done, the "Dreaming Dashboard
Definition of Done" section, is met.

### Required process skills

- **Governing:** `/dfrysinger-skills:development-loop` owns phase order,
  critical-lane gates, live proof before implementation review, final systemic
  proof, and local landing. Invoke it at run start and after compaction when it
  is no longer active.
- **Execution:** `behavior-validation` owns the real application scenarios when
  the server and UI are runnable. `visual-proof` owns browser captures and
  visual evidence during live proof. Invoke each only when its phase begins.
- **Context:** `/dfrysinger-skills:self-compact` owns compaction at the governing
  workflow's compaction points or when context becomes noisy. Persist this baton
  first, invoke the skill as the final action, and do not compact during active
  live proof or only because the hourly reminder fired.

## Execution baton

- **Phase:** Complete.
- **Reviewed work order:** `docs/dreaming-dashboard-design.md`
- **Lane:** Critical.
- **Base design commit:** `e7e380a`
- **Prototype:** `docs/prototypes/dreaming-dashboard.html`
- **Push policy:** Local commits only; do not push.
- **Live-proof receipt:**
  `dashboard-live-proof-receipt.md` in the session evidence directory.
- **Landed implementation:** `771f6a8` (`Add private Dreaming monitoring
  dashboard`) on `feature/multi-cli-dreaming`.
- **Implemented candidate:** Data contracts, authenticated read-only server,
  five-section frontend, exact evidence and transcript views, evaluation
  portfolio aggregation, and installer lifecycle ownership.
- **Proof completed:** LaunchAgent health, every live browser route,
  large-list pagination, historical evidence labels, exact anchored evidence,
  transcript opening and Back restoration, negative authentication boundary,
  hostile-content containment, stale-authority invalidation, protected-token
  reinstall, visual captures, targeted closure suites, and the final
  before-and-after read-only manifest.
- **Activation:** Generation `20260809T060553Z-install-10475` passed the full
  installed self-test with zero failures, verified publication to Claude,
  Codex, and Copilot, and is enabled with one dashboard process listening only
  on `127.0.0.1:47673`.
- **Review:** Both reviewer families completed bounded closure with no
  unresolved in-scope finding.
- **Final systemic proof:** The installed reviewed tree served all five primary
  sections plus skill detail and evidence with zero console errors; exact
  evidence highlighting, transcript opening, and Back restoration are retained
  in the behavior-validation proof. Authenticated health returned the matching
  activation generation, unauthorized requests failed closed, and the 427-file
  before-and-after manifests matched.
- **Next action:** None. Schedule `#12` may be stopped.

## Planned phases

1. Implement critical boundary guards and versioned data contracts, including
   red/green proof for the guards required before frontend integration.
2. Implement the read-only aggregation layer and authenticated loopback API.
3. Implement the static frontend from the approved prototype.
4. Integrate installer, LaunchAgent, status, self-test, uninstall, and rollback.
5. Produce a runnable candidate with targeted tests.
6. Pass the complete live browser and negative-boundary proof.
7. Run remaining validation and dual implementation review.
8. Apply review fixes, rerun the critical systemic end-to-end proof, and commit
   the completed implementation locally.

The only completion authority is the "Dreaming Dashboard Definition of Done"
section in `docs/dreaming-dashboard-design.md`.
