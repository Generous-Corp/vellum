# Windows and Linux PluginView mirror packaging

This lane carries `d1d102f26` and merges the required closure records and host base branches. `linux-x64` is present on Vellum main through PR 692. `windows-x64` is present from `lane/w-closure-windows` through PR 700. `origin/lane/w-rmlui-hosts-3` is merged as the PR 686 base.

## Evidence

- `python3 tools/mirror/test_platform_cmake.py` passes 9 tests.
- `python3 tools/mirror/test_mirror_package.py` passes 46 tests with 1 expected skip.
- `python3 tools/mirror/test_framework_ports.py` passes 46 tests.
- The platform CMake control against the pre-packaging `origin/main` module fails Linux and Windows with the expected `mirror packaging ... is not implemented yet` errors. The current module passes both cases.
- `scripts/gates.sh origin/main` passes prose-lint, product-quality, product-ui-packages, release-policy, release-policy-base, authority-tests, and observatory-tests. The observatory-events check was stopped after its upstream Pulp clone did not complete within the session deadline. No reconcile command was emitted and no provenance files were changed by that check.

Linux and Windows CMake configure, compile, and native RmlUi CTest execution remain CI-only from this Mac. GPU-backed surface behavior, installed-consumer loading, and DAW/editor embedding need the platform runners and a GPU-capable lead run.
