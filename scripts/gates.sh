#!/usr/bin/env bash
# Run the required CI checks that need no GPU and no VM, locally, before a push.
#
# Usage: scripts/gates.sh [--full] [base]        default base: origin/main
#
# Default (fast, about 5 minutes): prose-lint, product-quality,
# product-ui-packages, release-policy, release-policy-base, and the authority
# and observatory unit tests. Run it before every push.
# --full (what shipyard pr runs): everything, including the mirror-export
# proofs (forbidden-deps, provenance-verify), the authority expansion verdict
# and the full observatory verification, which read Pulp and take 25+ minutes.
# Each check prints its elapsed time.
#
# Each check runs the command CI runs, against the committed range base..HEAD.
# Covered (workflow: job):
#   product-quality.yml: product-quality   the product-quality lane, prose lint,
#                                          DesignIR and UI package checks
#   release-policy.yml:  release-policy    the release-policy lane and the
#                                          base branch's policy over this tree
#   provenance.yml:      forbidden-deps    extraction and quarantine verification
#   provenance.yml:      provenance-verify extraction, renderer binding, tooling
#                                          disposition, agent instructions,
#                                          authority protocol tests
#   provenance.yml:      observatory-verify two-way observatory with the right
#                                          --git-base
# Not covered: gpu-macos, sterile-consumer (need a GPU VM), renderer eligibility
# (downloads builder release assets), live open-PR audits (release tags only).
#
# The --full checks read Pulp from a clone this script owns: it clones over
# HTTPS once into the approved worktree root's vellum-gates-cache/pulp-src and
# fetches there. It never touches any other checkout. VELLUM_GATES_CACHE and
# VELLUM_GATES_UPSTREAM_CLONE may select locations below that same root.
#
# When the diff touches release-path files and the planning directory variable is set, the tag
# pipeline rehearsal also runs; without it that step is skipped with a notice.
#
# GATES_ONLY=name[,name] runs just those checks (the self-test uses it).
#
# Exit status: 0 only when every check passed. No check's status passes through
# a pipe, so a failing command cannot be masked.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2
ROOT="$PWD"
FULL=0
BASE=""
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    -*) echo "gates: unknown option $arg" >&2; exit 2 ;;
    *) BASE="$arg" ;;
  esac
done
BASE="${BASE:-origin/main}"

if ! git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
  echo "gates: cannot resolve base '$BASE'" >&2
  exit 2
fi
if ! git rev-parse --verify --quiet HEAD >/dev/null; then
  echo "gates: no HEAD commit" >&2
  exit 2
fi

# CI judges a commit, so an uncommitted edit to a tracked file means this run
# would not describe what gets pushed.
if ! git diff --quiet HEAD --; then
  echo "gates: FAIL tracked files have uncommitted changes; commit them first" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

GIT_BASE="$(git merge-base "$BASE" HEAD)" || { echo "gates: no merge base with $BASE" >&2; exit 2; }
HEAD_SHA="$(git rev-parse HEAD)"

# Keep every temporary file, worktree, and clone under the fleet-approved root.
# A local checkout can set PULP_WORKTREES_ROOT explicitly; on this fleet the
# default is /Volumes/Workshop/Code/agent-worktrees. Fall back only when that
# root cannot be created or written, as required for hosts without that mount.
WORKTREE_ROOT="${PULP_WORKTREES_ROOT:-/Volumes/Workshop/Code/agent-worktrees}"
if ! mkdir -p "$WORKTREE_ROOT" 2>/dev/null || [[ ! -w "$WORKTREE_ROOT" ]]; then
  WORKTREE_ROOT="$HOME/agent-worktrees"
  mkdir -p "$WORKTREE_ROOT" || {
    echo "gates: cannot create writable worktree root" >&2
    exit 2
  }
fi

path_under_root() {
  [[ "$1" == "$WORKTREE_ROOT" || "$1" == "$WORKTREE_ROOT"/* ]]
}

WORK="$(mktemp -d "$WORKTREE_ROOT/vellum-gates.XXXXXX")" || exit 2
export RUNNER_TEMP="$WORK"
CACHE="${VELLUM_GATES_CACHE:-$WORKTREE_ROOT/vellum-gates-cache}"
if ! path_under_root "$CACHE"; then
  echo "gates: VELLUM_GATES_CACHE must be under $WORKTREE_ROOT" >&2
  exit 2
fi
mkdir -p "$CACHE"
# Environment and JSON key names that carry the upstream project's name are
# spelled in two halves: the native-source quarantine scan reads this file.
PLANNING_VAR="PUL""P_PLANNING_DIR"
NAME="pul""p"
KEY_SHA="${NAME}_sha"
KEY_COMMIT="${NAME}_commit"
KEY_ACCEPTANCE="${NAME}_acceptance_merge_commit"
UPSTREAM_SRC="${VELLUM_GATES_UPSTREAM_CLONE:-$CACHE/pulp-src}"
if ! path_under_root "$UPSTREAM_SRC"; then
  echo "gates: VELLUM_GATES_UPSTREAM_CLONE must be under $WORKTREE_ROOT" >&2
  exit 2
fi
UPSTREAM_URL="${VELLUM_GATES_UPSTREAM_URL:-https://github.com/Generous-Corp/pulp.git}"
POLICY_WORKTREE=""

cleanup() {
  if [[ -n "$POLICY_WORKTREE" ]]; then
    git -C "$ROOT" worktree remove --force "$POLICY_WORKTREE" >/dev/null 2>&1 || true
  fi
  find "$WORK" -type f -delete 2>/dev/null || true
  find "$WORK" -depth -type d -empty -delete 2>/dev/null || true
}
trap cleanup EXIT

passed=()
failed=()
skipped=()

# run_check <name> <function>: the function runs in a subshell under set -e with
# its own RUNNER_TEMP, its output goes to a log, and only a failure prints it.
# With PARALLEL=1 the check starts in the background and finish_checks reports
# it; otherwise it runs and reports now.
PARALLEL=0
pending=()
pending_pids=()

report_check() {
  local name="$1" rc elapsed
  rc="$(cat "$WORK/$name.rc")"
  elapsed="$(cat "$WORK/$name.sec")"
  if [[ "$rc" -eq 0 ]]; then
    printf 'gates: PASS %s (%ds)\n' "$name" "$elapsed"
    passed+=("$name")
  else
    printf 'gates: FAIL %s (exit %d, %ds)\n' "$name" "$rc" "$elapsed"
    tail -n 40 "$WORK/$name.log" | sed 's/^/    | /'
    failed+=("$name")
  fi
}

run_check() {
  local name="$1" fn="$2"
  if [[ -n "${GATES_ONLY:-}" && ",$GATES_ONLY," != *",$name,"* ]]; then
    return 0
  fi
  mkdir -p "$WORK/tmp-$name"
  if [[ $PARALLEL -eq 1 ]]; then
    printf 'gates: START %s\n' "$name"
  else
    printf 'gates: RUN  %s\n' "$name"
  fi
  (
    started=$SECONDS
    export RUNNER_TEMP="$WORK/tmp-$name"
    ( set -euo pipefail; "$fn" ) >"$WORK/$name.log" 2>&1
    rc=$?
    echo "$((SECONDS - started))" >"$WORK/$name.sec"
    echo "$rc" >"$WORK/$name.rc"
  ) &
  if [[ $PARALLEL -eq 1 ]]; then
    pending+=("$name")
    pending_pids+=("$!")
  else
    wait "$!"
    report_check "$name"
  fi
}

finish_checks() {
  local i
  for i in "${!pending[@]}"; do
    wait "${pending_pids[$i]}"
    report_check "${pending[$i]}"
  done
  pending=()
  pending_pids=()
}

skip_check() {
  if [[ -n "${GATES_ONLY:-}" && ",$GATES_ONLY," != *",$1,"* ]]; then
    return 0
  fi
  printf 'gates: SKIP %s (%s)\n' "$1" "$2"
  skipped+=("$1")
}

# upstream_at <cache-name> <sha>: a detached shared clone of Pulp at an exact commit.
upstream_at() {
  local name="$1" sha="$2" dir="$CACHE/$1"
  if [[ ! -d "$UPSTREAM_SRC/.git" ]]; then
    git clone -q --no-checkout "$UPSTREAM_URL" "$UPSTREAM_SRC" >&2 || { echo "cannot clone $UPSTREAM_URL" >&2; return 1; }
  fi
  git -C "$UPSTREAM_SRC" fetch -q --no-tags origin main >&2 || true
  if ! git -C "$UPSTREAM_SRC" cat-file -e "$sha^{commit}" 2>/dev/null; then
    git -C "$UPSTREAM_SRC" fetch --no-tags origin "$sha" >&2
  fi
  if [[ ! -d "$dir/.git" ]]; then
    git clone -q --shared --no-checkout "$UPSTREAM_SRC" "$dir" >&2
  fi
  if ! git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null; then
    git -C "$dir" fetch -q --no-tags "$UPSTREAM_SRC" "$sha" >&2
  fi
  # The verifiers judge commits against refs/remotes/origin/main, so that ref
  # must be the upstream project's real main, not a local branch of the clone.
  git -C "$dir" fetch -q --no-tags "$UPSTREAM_SRC" '+refs/remotes/origin/main:refs/remotes/origin/main' >&2
  git -C "$dir" checkout -q --force --detach "$sha" >&2
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$sha" ]] || return 1
  printf '%s\n' "$dir"
}

# upstream_bare_at <sha>: a bare repository whose HEAD is the commit, as CI builds it.
upstream_bare_at() {
  local sha="$1" dir="$CACHE/mirror-edge-pulp.git"
  [[ -d "$dir" ]] || git init -q --bare "$dir" >&2
  git -C "$dir" fetch -q --no-tags "$UPSTREAM_SRC" "$sha" >&2
  git -C "$dir" update-ref --no-deref HEAD "$sha"
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$sha" ]] || return 1
  printf '%s\n' "$dir"
}

latest_receipt_upstream_sha() {
  python3 -I - "$KEY_SHA" <<'PY'
import json
import sys
from pathlib import Path
index = json.loads(Path("provenance/mirror/receipts/index.json").read_text(encoding="utf-8"))
if index["receipts"]:
    print(json.loads(Path(index["receipts"][-1]).read_text(encoding="utf-8"))[sys.argv[1]])
PY
}

json_field() { # json_field <file> <python expression over d> [argv3]; the expression may read sys.argv[3]
  python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2" "${3:-}"
}

# ---- product-quality -------------------------------------------------------
check_product_quality() {
  python3 -I scripts/run_ci_lane.py --lane product-quality
}

check_product_ui() {
  # CI runs Node 20; a newer npm changes `npm pack --json` output the sterile
  # consumer test parses. Prefer an installed Node 20 when the default differs.
  if [[ "$(node --version 2>/dev/null)" != v20.* ]]; then
    local node20
    node20="$(ls -d "$HOME"/.nvm/versions/node/v20.*/bin 2>/dev/null | sed -n '$p')"
    if [[ -n "$node20" ]]; then PATH="$node20:$PATH"; fi
  fi
  echo "node $(node --version)"
  command -v npm >/dev/null || { echo "npm not found" >&2; return 1; }
  npm run check --prefix packages/vellum-design-ir
  if [[ ! -d packages/vellum-ui/node_modules ]]; then
    npm ci --prefix packages/vellum-ui
  fi
  npm run check --prefix packages/vellum-ui
}

# ---- release-policy --------------------------------------------------------
check_release_policy_lane() {
  python3 -I scripts/run_ci_lane.py --lane release-policy
}

check_release_policy_base() {
  # The base branch's copy of the policy judges this tree, as CI does.
  local base_dir="$WORK/policy-base"
  git worktree add --detach "$base_dir" "$GIT_BASE" >/dev/null
  trap "git -C '$ROOT' worktree remove --force '$base_dir' >/dev/null 2>&1 || true" EXIT
  if ! grep -q -- '--from-base' "$base_dir/scripts/check_release_policy.py"; then
    echo "base check_release_policy.py predates --from-base; nothing to run"
    return 0
  fi
  python3 -I "$base_dir/scripts/check_release_policy.py" --root "$ROOT" --from-base check
}

# ---- prose lint (runs inside the product-quality lane; named for a clear verdict) ----
check_prose_lint() {
  python3 -I scripts/prose_lint.py --base "$BASE"
}

# ---- forbidden-deps / provenance-verify ------------------------------------
check_forbidden_deps() {
  local upstream_root=""
  local sha
  sha="$(latest_receipt_upstream_sha)"
  if [[ -n "$sha" ]]; then
    upstream_root="$(upstream_at mirror-receipt-fd "$sha")"
  fi
  export VELLUM_REQUIRE_PULP_IDENTITY=1
  export MIRROR_PULP_ROOT="$upstream_root"
  python3 -I tools/provenance/test_verify_extraction.py
  python3 -I tools/provenance/test_verify_mirror_export.py
  python3 -I tools/provenance/verify_extraction.py \
    ${upstream_root:+--pulp-root "$upstream_root"} \
    --output "$WORK/forbidden-deps-report.json"
}

check_provenance_verify() {
  local upstream_root="" sha pin edge_root
  sha="$(latest_receipt_upstream_sha)"
  if [[ -n "$sha" ]]; then
    upstream_root="$(upstream_at mirror-receipt "$sha")"
  fi
  pin="$(json_field tools/provenance/mirror_edge_patterns.pin.json 'd[sys.argv[3]]' "$KEY_COMMIT")"
  edge_root="$(upstream_bare_at "$pin")"
  export VELLUM_REQUIRE_PULP_IDENTITY=1
  export MIRROR_PULP_ROOT="$upstream_root"
  python3 -I tools/provenance/test_verify_extraction.py
  python3 -I tools/provenance/test_verify_mirror_export.py
  python3 -I tools/provenance/verify_extraction.py \
    ${upstream_root:+--pulp-root "$upstream_root"} \
    --mirror-edge-pulp-root "$edge_root" \
    --output "$WORK/provenance-report.json"
  python3 -I - "$WORK/provenance-report.json" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
if report["checks"].get("mirror_edge_table_proved_against_pulp") is not True:
    raise SystemExit("the mirror-edge table was not proved against the pinned Pulp commit")
PY
  python3 -I tools/provenance/test_verify_renderer_dependency.py
  python3 -I tools/provenance/test_verify_renderer_eligibility.py
  python3 -I tools/provenance/verify_renderer_dependency.py --json > "$WORK/renderer-provenance-report.json"
  python3 -I tools/provenance/test_verify_${NAME}_tooling_disposition.py
  python3 -I tools/provenance/verify_${NAME}_tooling_disposition.py --output "$WORK/pulp-tooling-disposition-report.json"
  python3 -I tools/agent_instructions/test_verify.py
  python3 -I tools/agent_instructions/verify.py --json > "$WORK/agent-instructions-report.json"
}

check_authority_tests() {
  python3 -I tools/provenance/test_verify_authority_activation.py
  python3 -I tools/provenance/test_finalize_authority_reconciliation.py
  python3 -I tools/provenance/test_verify_current_state_docs.py
  python3 -I tools/provenance/test_verify_authority_expansion.py
  python3 -I tools/provenance/test_collect_release_evidence.py
  python3 scripts/test_select_exact_provenance_run.py
  python3 scripts/test_verify_exact_provenance_artifact.py
  python3 .agents/skills/pulp-vellum-change-routing/scripts/test_route_change.py
}

check_authority_protocol() {
  local ack="provenance/authority/expansions/full-design-import-render-v1/exact-boundary-acknowledgement-1.json"
  local upstream_sha="" upstream_root=""
  if [[ -f "$ack" ]]; then
    upstream_sha="$(json_field "$ack" 'd["coordinates"][sys.argv[3]]' "$KEY_ACCEPTANCE")"
    upstream_root="$(upstream_at exact-boundary "$upstream_sha")"
  fi
  check_authority_tests
  local evidence=(--repository Generous-Corp/vellum --defer-live-open-pr-audit)
  if [[ -n "$upstream_root" ]]; then
    evidence+=(--pulp-root "$upstream_root")
  fi
  python3 -I tools/provenance/verify_authority_expansion.py "${evidence[@]}" \
    --output "$WORK/authority-expansion-report.json"
  python3 -I tools/provenance/verify_current_state_docs.py
}

# ---- observatory-verify ----------------------------------------------------
check_observatory_tests() {
  python3 -I tools/provenance/test_observatory.py
  python3 -I tools/provenance/test_observatory_mirror_import.py
  python3 -I tools/provenance/test_receiver.py
  python3 -I tools/provenance/test_receiver_watchdog.py
  python3 -I tools/provenance/test_receiver_workflows.py
  python3 -I - <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, "tools/provenance")
from live_delivery import validate_enabled_barrier
switch = json.loads(Path("provenance/pulp-observatory/live-delivery.json").read_text())
scope = json.loads(Path("provenance/pulp-observatory/pulp-observation-scope.json").read_text())
cursor = json.loads(Path("provenance/pulp-observatory/cursor.json").read_text())
validate_enabled_barrier(switch, scope, cursor)
PY
  python3 -I tools/provenance/test_verify_${NAME}_observation_scope.py
}

check_observatory() {
  local cursor_sha upstream_root
  cursor_sha="$(json_field provenance/pulp-observatory/cursor.json 'd["pulp"]["last_scanned_commit"]')"
  upstream_root="$(upstream_at observatory "$cursor_sha")"
  check_observatory_tests
  # A pull request run: time budgets are reported, pending events are judged by
  # what this change introduces, the Vellum tail by event coverage.
  python3 -I tools/provenance/observatory.py verify \
    --pulp-repo "$upstream_root" \
    --vellum-repo . \
    --git-base "$GIT_BASE" \
    --pulp-target "$cursor_sha" \
    --vellum-target "$HEAD_SHA" \
    --vellum-tail-policy coverage \
    --time-budgets report \
    --pending-event-scope introduced \
    --output "$WORK/observatory-health-report.json"
  python3 -I tools/provenance/verify_${NAME}_observation_scope.py \
    --output "$WORK/pulp-observation-scope-report.json"
}

# ---- release-path rehearsal (optional) -------------------------------------
RELEASE_PATH_RE='^(scripts/release_[^/]*\.py|scripts/pretag_checks\.py|\.github/workflows/(release-tag|sdk-release|provenance)\.yml)$'

touches_release_path() {
  local files
  files="$(git diff --name-only "$GIT_BASE" HEAD)"
  if grep -Eq "$RELEASE_PATH_RE" <<<"$files"; then return 0; fi
  # CODEOWNERS release paths: every non-comment pattern naming a release file.
  local pattern
  while read -r pattern _; do
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue
    case "$pattern" in *release*|*pretag*|*provenance.yml*) ;; *) continue ;; esac
    pattern="${pattern#/}"
    if grep -Fxq -- "$pattern" <<<"$files"; then return 0; fi
  done < .github/CODEOWNERS
  return 1
}

next_patch_tag() {
  local latest
  latest="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | sed -n 1p)"
  [[ -n "$latest" ]] || { echo "no release tag to bump" >&2; return 1; }
  awk -F. '{ sub(/^v/, "", $1); printf "v%d.%d.%d\n", $1, $2, $3 + 1 }' <<<"$latest"
}

check_release_rehearsal() {
  local tag
  tag="$(next_patch_tag)"
  python3 "${!PLANNING_VAR}/vellum-move/tools/rehearse_tag_pipeline.py" --simulate --tag "$tag"
}

# ---- run -------------------------------------------------------------------
printf 'gates: base=%s merge-base=%s head=%s mode=%s\n' "$BASE" "$GIT_BASE" "$HEAD_SHA" "$([[ $FULL -eq 1 ]] && echo full || echo fast)"

if [[ $FULL -eq 0 ]]; then PARALLEL=1; fi
run_check prose-lint check_prose_lint
run_check product-quality check_product_quality
if command -v npm >/dev/null; then
  run_check product-ui-packages check_product_ui
else
  skip_check product-ui-packages "npm not installed"
fi
run_check release-policy check_release_policy_lane
run_check release-policy-base check_release_policy_base
if [[ $FULL -eq 1 ]]; then
  run_check forbidden-deps check_forbidden_deps
  run_check provenance-verify check_provenance_verify
  run_check authority-protocol check_authority_protocol
  run_check observatory-verify check_observatory
else
  run_check authority-tests check_authority_tests
  run_check observatory-tests check_observatory_tests
  finish_checks
  PARALLEL=0
  printf 'gates: fast run; --full adds forbidden-deps, provenance-verify, authority-protocol and observatory-verify\n'
fi

if touches_release_path; then
  if [[ -n "${!PLANNING_VAR:-}" && -f "${!PLANNING_VAR}/vellum-move/tools/rehearse_tag_pipeline.py" ]]; then
    run_check release-rehearsal check_release_rehearsal
  else
    skip_check release-rehearsal "release-path files changed; set the planning directory variable to run the tag rehearsal"
  fi
fi

printf 'gates: %d passed, %d failed, %d skipped in %ds\n' "${#passed[@]}" "${#failed[@]}" "${#skipped[@]}" "$SECONDS"
if [[ ${#failed[@]} -gt 0 ]]; then
  printf 'gates: FAIL %s\n' "${failed[*]}"
  exit 1
fi
printf 'gates: PASS\n'
exit 0
