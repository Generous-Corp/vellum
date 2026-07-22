#!/bin/sh
# Local-development and verified-archive installer for the Vellum CLI skeleton.
set -eu

install_prefix=${VELLUM_INSTALL_DIR:-"$HOME/.local"}
local_root=
archive=
checksums=

usage() {
  printf '%s\n' \
    'Usage:' \
    '  scripts/install.sh --local PATH [--install-dir PREFIX]' \
    '  scripts/install.sh --archive FILE --checksums SHA256SUMS [--install-dir PREFIX]' \
    '' \
    'No public release is published yet. --local is an explicitly unverified' \
    'development install. --archive verifies SHA-256 before extracting.'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --local) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; local_root=$2; shift 2 ;;
    --archive) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; archive=$2; shift 2 ;;
    --checksums) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; checksums=$2; shift 2 ;;
    --install-dir) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; install_prefix=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$local_root" ] && { [ -n "$archive" ] || [ -n "$checksums" ]; }; then
  printf '%s\n' '--local cannot be combined with --archive or --checksums.' >&2
  exit 2
fi
case "$install_prefix" in
  ''|'/'|'.') printf 'Refusing unsafe install prefix: %s\n' "$install_prefix" >&2; exit 2 ;;
esac

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    printf '%s\n' 'Neither shasum nor sha256sum is available.' >&2
    exit 1
  fi
}

copy_payload() {
  payload=$1
  library="$install_prefix/lib/vellum"
  bindir="$install_prefix/bin"
  mkdir -p "$library" "$bindir"
  cp "$payload/vellum_cli.py" "$library/vellum_cli.py"
  rm -rf "$library/templates"
  cp -R "$payload/templates" "$library/templates"
  if [ -f "$payload/bin/vellum-backend" ]; then
    mkdir -p "$library/bin"
    cp "$payload/bin/vellum-backend" "$library/bin/vellum-backend"
    chmod 755 "$library/bin/vellum-backend"
  fi
  {
    printf '%s\n' '#!/bin/sh' 'set -eu'
    # These expressions are intentionally written literally into the launcher.
    # shellcheck disable=SC2016
    printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
    # shellcheck disable=SC2016
    printf '%s\n' 'prefix=$(CDPATH="" cd -- "$bindir/.." && pwd)'
    # shellcheck disable=SC2016
    printf '%s\n' 'export VELLUM_SDK_ROOT="$prefix/lib/vellum"'
    # shellcheck disable=SC2016
    printf '%s\n' 'exec python3 "$VELLUM_SDK_ROOT/vellum_cli.py" "$@"'
  } > "$bindir/vellum"
  chmod 755 "$bindir/vellum"
  "$bindir/vellum" --version
  printf 'Installed Vellum CLI to %s\n' "$bindir/vellum"
  printf 'Add %s to PATH if needed.\n' "$bindir"
}

if [ -n "$local_root" ]; then
  [ -f "$local_root/cli/vellum_cli.py" ] && [ -d "$local_root/templates/basic" ] || {
    printf '%s\n' 'Local root must contain cli/vellum_cli.py and templates/basic.' >&2
    exit 1
  }
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-local.XXXXXX")
  trap 'rm -rf "$temporary"' EXIT HUP INT TERM
  cp "$local_root/cli/vellum_cli.py" "$temporary/vellum_cli.py"
  cp -R "$local_root/templates" "$temporary/templates"
  printf '%s\n' 'LOCAL DEVELOPMENT INSTALL: source bytes are not release-verified.'
  printf '%s\n' 'No native runtime/backend is included unless present separately.'
  copy_payload "$temporary"
  exit 0
fi

if [ -z "$archive" ] || [ -z "$checksums" ]; then
  printf '%s\n' 'No Vellum release is published. Choose --local, or provide both a verified archive and SHA256SUMS.' >&2
  usage >&2
  exit 2
fi
[ -f "$archive" ] || { printf 'Archive not found: %s\n' "$archive" >&2; exit 1; }
[ -f "$checksums" ] || { printf 'Checksum manifest not found: %s\n' "$checksums" >&2; exit 1; }

archive_name=$(basename "$archive")
matches=$(awk -v file="$archive_name" '$2 == file || $2 == "*" file { print $1 }' "$checksums")
match_count=$(printf '%s\n' "$matches" | awk 'NF { count++ } END { print count + 0 }')
[ "$match_count" -eq 1 ] || {
  printf 'Expected exactly one checksum for %s; found %s.\n' "$archive_name" "$match_count" >&2
  exit 1
}
expected=$(printf '%s\n' "$matches" | awk 'NF { print; exit }')
case "$expected" in *[!0-9A-Fa-f]*|'') printf '%s\n' 'Checksum must be 64 hexadecimal characters.' >&2; exit 1 ;; esac
[ "${#expected}" -eq 64 ] || { printf '%s\n' 'Checksum must be 64 hexadecimal characters.' >&2; exit 1; }
actual=$(sha256_file "$archive")
[ "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" = "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" ] || {
  printf 'SHA-256 mismatch for %s. Refusing to extract.\n' "$archive_name" >&2
  exit 1
}
printf 'Verified SHA-256: %s\n' "$actual"

temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-install.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
python3 - "$archive" "$temporary" <<'PY'
from pathlib import PurePosixPath
import sys
import tarfile

archive, destination = sys.argv[1:]
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    if len(members) > 10_000 or sum(member.size for member in members) > 2 * 1024**3:
        raise SystemExit("archive exceeds installer safety limits")
    for member in members:
        path = PurePosixPath(member.name)
        if (not path.parts or path.parts[0] not in {"vellum_cli.py", "templates", "bin"} or
                path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in path.parts[0] or
                member.issym() or member.islnk() or not (member.isfile() or member.isdir())):
            raise SystemExit(f"unsafe archive member: {member.name}")
    handle.extractall(destination)
PY
[ -f "$temporary/vellum_cli.py" ] && [ -d "$temporary/templates/basic" ] || {
  printf '%s\n' 'Verified archive does not contain the Vellum CLI artifact layout.' >&2
  exit 1
}
copy_payload "$temporary"
