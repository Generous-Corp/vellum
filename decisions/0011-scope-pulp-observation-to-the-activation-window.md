# Decision 0011: scope Pulp observation to the activation window

- Date: 2026-10-10
- Status: accepted; Pulp-to-Vellum observation is frozen at Pulp
  `aa630815630ca259b743651dacdc335f3d94a39a`

The observatory's Pulp cursor last advanced to
`aa630815630ca259b743651dacdc335f3d94a39a` on 2026-08-14. Pulp has landed
thousands of commits since then. Advancing the cursor in one step would record
every mapped commit as a pending observation: a dry reconcile against Pulp
`32935e0fd8bb8a2a3d58592514460a92ad0bcbf8` produced 1,354 pending events,
including 38 security-class events with 24-hour deadlines. That exceeds the
`receiver-policy.json` pending limit of 20, so health fails and every Vellum
pull request is blocked until all of them are classified by hand.

Decision 0010 replaces per-commit porting with closure-manifest export from an
exact Pulp commit. Its export receipts and blob-origin records bind each
mirrored blob to the Pulp commit and path it came from. Per-commit Pulp
observation is therefore no longer how Vellum learns what to inherit:

- Pulp history through `aa630815` stays covered by the observatory's committed
  events and resolutions, which are unchanged.
- Pulp history after `aa630815` is covered by the commit- and tree-anchored
  inheritance ledger kept with the extraction program's planning records, and,
  for every exported unit, by Decision 0010's export receipts and blob-origin
  records.

## Rule

`provenance/pulp-observatory/pulp-observation-scope.json` records the frozen
Pulp commit. `tools/provenance/verify_pulp_observation_scope.py` requires that
the committed cursor's `pulp.last_scanned_commit` equals it, that the committed
report's health is `pass`, and that the observatory README states the scope. The
observatory's `health: pass` therefore makes no claim about Pulp history after
the frozen commit.

The Vellum side of the observatory is unchanged: `vellum.last_scanned_commit`
still advances with every non-evidence change, and the evidence-tail rule from
the operating plan still applies.

Advancing the Pulp cursor again requires a later decision that supersedes this
one, updates the scope record, and classifies every resulting observation in the
same pull request. The live receiver stays disabled while this scope is frozen; enabling it
requires a superseding decision and a covered barrier.

This decision changes no authority record, no ownership map, and no Pulp
change-event contract.
