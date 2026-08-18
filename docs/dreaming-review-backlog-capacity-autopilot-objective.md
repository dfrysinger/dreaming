Implement the reviewed work order in
`docs/dreaming-review-backlog-capacity-design.md` from
`/Users/dfrysinger/code/dreaming`.

Make the Mac mini's existing single Dreaming owner run the bounded standalone
core often enough to exceed measured review arrivals and reduce the retained
backlog. Keep the per-run limit at 25, preserve sequential execution, the
writer lock, halt switch, queue supersession, transaction recovery and all
review/mutation policies. Add the reviewed read-only Overview capacity
projection so queue age, recent arrivals, recent completions, observed net
throughput, recovery-required sessions and burn-down state are presented
honestly.

Follow
`docs/dreaming-review-backlog-capacity-autopilot-charter.md`. Invoke
`development-loop` as the governing process and invoke `unattended-run` to arm
exactly one hourly reminder that re-reads that charter. Use the charter's
execution skills only in the phases they own. Keep the plan baton and proof
references current across compaction.

Do not add a worker, daemon, queue, autoscaler, concurrent reviewer, extra
coalescing layer or unbounded review loop. Do not raise
`max_reviews_per_run` above 25 or redesign review policy, model routing,
transcript indexing, learned-skill usage, watchdog presentation, roll or
prune. Never push.

Finish only when every item under
`## Definition of Done: Dreaming review backlog capacity` is verifiably met on
the reviewed tree, including deterministic checks, installed self-test,
canary and natural-interval evidence, truthful browser proof, rollback
preservation and paired implementation review with no unresolved in-scope
must-fix finding.
