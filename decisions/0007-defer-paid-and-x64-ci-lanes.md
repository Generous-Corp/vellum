# Decision 0007: defer paid and x64 CI lanes

- Date: 2026-07-24
- Status: superseded by Decision 0009 for the billing-restored local-first
  fallback posture; its ARM-only baseline and x64 validation warning remain
  historical context

Decision 0009 supersedes the prohibition on configuring a hosted fallback.
It does not authorize hosted capacity as the preferred lane or waive the
remaining x64 validation gap.

Every Vellum workflow runs on the self-hosted ARM64 fleet, so continuous
integration costs no GitHub compute minutes. No GitHub-hosted or otherwise
paid runner is configured, and none may be enabled before the next billing
cycle.

The cost of that choice is honest and worth naming: Vellum has **no automated
x86_64 coverage**. Nothing in CI proves the CLI, the installer, the web
bundler, or the native host behaves on an Intel Mac or an x64 Linux host.
Treat x64 as unvalidated rather than supported until this decision is
revisited.

## What holds the configuration in place

Three layers agree, so a lane cannot drift back to a paid runner by accident:

1. Each workflow selects its runner through a repository variable with an
   ARM64 self-hosted fallback baked into the workflow file:

   | Variable | Fallback labels | Workflows |
   |---|---|---|
   | `VELLUM_LINUX_RUNS_ON_JSON` | `["self-hosted","Linux","ARM64","vellum-build-linux"]` | `product-quality.yml`, `provenance.yml`, `merge-on-green.yml` |
   | `VELLUM_MACOS_RUNS_ON_JSON` | `["self-hosted","macOS","ARM64","vellum-build-macos"]` | `gpu-macos.yml`, `readme-quick-start.yml` |
   | `VELLUM_AUTHORITY_RUNS_ON_JSON` | `["self-hosted","Linux","ARM64","vellum-authority-linux"]` | `authority-activation.yml`, `authority-release.yml` |

2. `scripts/test_ci_runner_policy.py` asserts the exact variable and fallback
   for every workflow, and rejects the GitHub-hosted labels outright through
   its `HOSTED_LABELS` list. A workflow added without an entry in
   `EXPECTED_RUNNERS` fails the same test.

3. `product-quality.yml` runs that policy test, so the check is a merge gate
   rather than a convention.

Setting one of the repository variables alone is therefore not enough to move
a lane: the variable overrides the fallback at dispatch time, but the policy
test still reads the fallback from the workflow file.

## Opting in later

To add an x64 lane once the budget allows, in one change:

1. Decide whether x64 is an additional job or a replacement. Prefer an
   additional job so ARM stays the required gate and x64 starts advisory.
2. Add the new job's `runs-on` selector, keeping the
   `vars.<VARIABLE> || '<fallback>'` shape so the lane stays overridable.
3. Extend `EXPECTED_RUNNERS` in `scripts/test_ci_runner_policy.py` with the new
   workflow or variable, and remove from `HOSTED_LABELS` only the specific
   hosted image being adopted — do not empty the list.
4. Set the repository variable for the lane if the label set differs from the
   fallback, for example
   `VELLUM_LINUX_RUNS_ON_JSON=["ubuntu-24.04"]`.
5. Confirm the intended spend before merging, and record the new cost posture
   by superseding this decision.

Adding a self-hosted x64 machine to the fleet is the cheaper alternative and
needs only steps 1 through 3, since it consumes no GitHub minutes.
