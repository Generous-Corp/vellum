---
name: pulp-vellum-change-routing
description: Route fixes and features discovered in Pulp or Vellum to the authoritative repository, determine counterpart reproduction and observatory or backport requirements, and prevent duplicated or authority-bypassing implementations. Use when work may affect transferred Pulp slices, shared importer, schema, rendering, runtime, or platform behavior, Pulp-only integrations, emergency fixes, or any decision about where a cross-repository change should land.
---

# Pulp/Vellum change routing

Route before editing. Discovery location is not ownership, and similar names are
not proof that two implementations share a defect.

## Classify

1. Resolve clean Vellum and Pulp checkouts at the intended immutable heads.
2. Run the read-only router with every proposed path and an explicit intent:

   ```sh
   python3 .agents/skills/pulp-vellum-change-routing/scripts/route_change.py \
     --vellum-repo /path/to/vellum \
     --pulp-repo /path/to/pulp \
     --source-repo pulp \
     --intent generic \
     --path core/canvas/src/text_layout.cpp
   ```

3. Treat exit `0` as a deterministic route, `2` as a required ownership or
   intent decision, and `3` as invalid/conflicting authority evidence. Do not
   edit while the result is `decision_required` or `invalid_authority`.
4. Reproduce separately in each applicable repository. Pass
   `--counterpart-result affected` or `not-affected` only after doing so.
5. For a planned Pulp framework backport, use
   `--operation framework-backport --framework-commit <40-hex>`. The commit must
   resolve locally in the supplied Vellum checkout.

The router reads Vellum's active ownership/extraction records, Pulp's exact
ownership projection, and Vellum's broad observatory counterpart map. Exact Pulp
projection rows win over broad counterpart prefixes. A new Pulp path under a
broadly mapped shared area is not silently transferred; it requires a decision.

## Apply the route

- **Vellum-owned generic behavior:** fix and test Vellum first. Publish an
  immutable commit/release as appropriate. If Pulp still needs an adaptation,
  record a Pulp `framework-backport` naming that exact Vellum commit and run the
  destination tests.
- **Pulp-only behavior:** keep audio, plug-in, host, legacy, and explicitly
  Pulp-owned integration work in Pulp. A transferred path may use a Pulp
  `pulp-only` event only when the change is genuinely Pulp-specific.
- **Emergency in a transferred Pulp path:** require an accountable `@owner`, a
  GitHub follow-up issue, an explicit creation date, and an unexpired date no
  more than 14 days after that creation date. Repair Vellum in parallel and
  reconcile before expiry. Do not extend the window by rerunning the router.
- **Excluded or untransferred Pulp behavior:** Pulp owns the current
  implementation. Check an analogous Vellum contract independently; do not call
  the result a framework backport unless the protocol actually applies.
- **Pulp fix that Vellum also needs:** classify the observatory event
  `port-required`, implement the equivalent fix in Vellum, then resolve it
  `ported` with linked immutable commits and contract tests.
- **Future Pulp SDK adoption:** verify a current adoption contract/lock first.
  Independently reproduce an applicable Pulp counterpart as affected. Then fix
  and release Vellum and update Pulp's pin plus integration tests, rather than
  duplicating source. Route with
  `--operation sdk-adoption --adoption-contract <Pulp-relative-json-path>`.
  The contract must use `pulp.vellum.sdk-adoption.v1`, match the active
  authority coordinates, name an accountable recorder and timestamp, and pin
  an exact SDK version, locally resolvable Vellum source commit, and artifact
  SHA-256. Until Pulp lands that reviewed contract, SDK adoption remains
  `decision_required`. Vellum-only paths without an applicable Pulp counterpart
  are not SDK-adoption work.

Pulp change-event `pulp-only` and observatory `Pulp-only` are different,
case-sensitive vocabularies. Observatory discovery starts as `pending`; it is
not a port decision.

## Stop conditions

Refuse or flag:

- disagreeing activation states, authority commits, record paths, events, or
  Pulp activation commits;
- undeclared intent, mixed owners in one proposed change, or a new unmapped Pulp
  path inside a broad shared counterpart;
- a floating, malformed, or locally unresolved framework backport commit;
- expired or incomplete emergency metadata;
- automatic copying, patch application, or blind cherry-picking between repos;
- treating `excluded` as Vellum-owned, treating discovery as a decision, or
  silently reversing authority.

Split mixed generic-framework and Pulp-product work when practical. Never use a
new file to evade an existing transferred boundary. Authority reversal requires
an explicit reviewed protocol in both repositories.

The adversarial scenario corpus is
`references/routing-scenarios.v1.json`. Validate changes with:

```sh
python3 .agents/skills/pulp-vellum-change-routing/scripts/test_route_change.py
```
