
## Required regression evidence

The new transferred-closure historical Skia asset test fails before the fix because the closure-supplied digest is accepted:
`python3 -m unittest tools.mirror.test_mirror_package.FrameworkPortsPlanTest.test_a_transferred_closure_cannot_record_its_own_historical_skia_asset`
returned `AssertionError: PlanError not raised`.

The control was run after restoring only the pre-fix versions of the mirror planner, extraction verifier, and renderer lock, then restored from the fix commit. Both tests failed:
- mirror regression: `AssertionError: PlanError not raised`
- builder exception regression: `skia_builder_ref (312f05e2... != 1b97010d...)`

After adding the recorded-digest acceptance test, the same pre-fix control also failed that test with `AttributeError: module 'mirror_package' has no attribute 'recorded_historical_skia_assets'`; the unrecorded test again reported `AssertionError: PlanError not raised`. The fix files were restored from commit `256469c8c`.
