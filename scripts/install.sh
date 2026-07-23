#!/bin/sh
# Local-development and verified-artifact installer for the Vellum SDK.
set -eu

install_prefix=${VELLUM_INSTALL_DIR:-"$HOME/.local"}
local_root=
archive=
checksums=
version=
release_base=${VELLUM_RELEASE_BASE_URL:-https://github.com/Generous-Corp/vellum/releases/download}
release_target=

usage() {
  printf '%s\n' \
    'Usage:' \
    '  scripts/install.sh --local PATH [--install-dir PREFIX]' \
    '  scripts/install.sh --archive FILE --checksums SHA256SUMS [--install-dir PREFIX]' \
    '  scripts/install.sh --version VERSION [--release-base-url URL] [--target TARGET]' \
    '' \
    'No public release is published yet. --local is an explicitly unverified' \
    'development install. Artifact and exact-version release modes verify' \
    'SHA-256 before extracting.'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --local) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; local_root=$2; shift 2 ;;
    --archive) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; archive=$2; shift 2 ;;
    --checksums) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; checksums=$2; shift 2 ;;
    --version) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; version=$2; shift 2 ;;
    --release-base-url) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; release_base=$2; shift 2 ;;
    --target) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; release_target=$2; shift 2 ;;
    --install-dir) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; install_prefix=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$local_root" ] && { [ -n "$archive" ] || [ -n "$checksums" ] || [ -n "$version" ]; }; then
  printf '%s\n' '--local cannot be combined with artifact or release options.' >&2
  exit 2
fi
if [ -n "$version" ] && { [ -n "$archive" ] || [ -n "$checksums" ]; }; then
  printf '%s\n' '--version cannot be combined with --archive or --checksums.' >&2
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
  if [ -d "$payload/sdk" ]; then
    cp "$payload/metadata.json" "$library/metadata.json"
    rm -rf "$library/sdk"
    cp -R "$payload/sdk" "$library/sdk"
    rm -rf "${library:?}/bin"
  elif [ ! -f "$library/metadata.json" ]; then
    cp "$payload/metadata.json" "$library/metadata.json"
  fi
  if [ -d "$payload/design-ir" ]; then
    command -v node >/dev/null 2>&1 || {
      printf '%s\n' 'Node.js 20+ is required for the import/reimport backend.' >&2
      exit 1
    }
    rm -rf "$library/design-ir"
    cp -R "$payload/design-ir" "$library/design-ir"
    mkdir -p "$library/bin"
    {
      printf '%s\n' '#!/bin/sh' 'set -eu'
      # shellcheck disable=SC2016
      printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
      # shellcheck disable=SC2016
      printf '%s\n' 'exec node "$bindir/../design-ir/bin/vellum-backend.js" "$@"'
    } > "$library/bin/vellum-backend"
    chmod 755 "$library/bin/vellum-backend"
  elif [ -f "$payload/bin/vellum-backend" ]; then
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
  cat > "$temporary/metadata.json" <<'JSON'
{
  "schema": "vellum.sdk-artifact.v1",
  "framework_version": "0.1.0",
  "cli_version": "0.1.0-dev",
  "cli_api": 1,
  "source_commit": null,
  "target": "local-development",
  "capabilities": {
    "cmake_sdk": false,
    "authoring_cli": true,
    "native_backend": false,
    "gpu_renderer": false
  },
  "files": []
}
JSON
  cp -R "$local_root/packages/vellum-design-ir" "$temporary/design-ir"
  printf '%s\n' 'LOCAL DEVELOPMENT INSTALL: source bytes are not release-verified.'
  printf '%s\n' 'The DesignIR import/reimport backend is included; native build/runtime remains separate.'
  copy_payload "$temporary"
  exit 0
fi

release_temporary=
if [ -n "$version" ]; then
  case "$version" in
    latest|vlatest|'') printf '%s\n' 'Release installs require an exact version, not latest.' >&2; exit 2 ;;
    v*) version=${version#v} ;;
  esac
  case "$version" in *[!0-9A-Za-z._-]*) printf '%s\n' 'Release version contains unsafe characters.' >&2; exit 2 ;; esac
  if [ -z "$release_target" ]; then
    case "$(uname -s)" in
      Darwin) release_os=darwin ;;
      Linux) release_os=linux ;;
      *) printf 'Unsupported release operating system: %s\n' "$(uname -s)" >&2; exit 1 ;;
    esac
    case "$(uname -m)" in
      arm64|aarch64) release_arch=arm64 ;;
      x86_64|amd64) release_arch=x86_64 ;;
      *) printf 'Unsupported release architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
    esac
    release_target="$release_os-$release_arch"
  fi
  case "$release_target" in *[!0-9A-Za-z._-]*|'') printf '%s\n' 'Release target contains unsafe characters.' >&2; exit 2 ;; esac
  command -v curl >/dev/null 2>&1 || { printf '%s\n' 'curl is required for exact-version release installs.' >&2; exit 1; }
  release_temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-release.XXXXXX")
  trap 'rm -rf "$release_temporary"' EXIT HUP INT TERM
  archive_name="vellum-sdk-$version-$release_target.tar.gz"
  release_url="${release_base%/}/v$version"
  archive="$release_temporary/$archive_name"
  checksums="$release_temporary/SHA256SUMS"
  curl -fsSL "$release_url/$archive_name" -o "$archive"
  curl -fsSL "$release_url/SHA256SUMS" -o "$checksums"
fi

if [ -z "$archive" ] || [ -z "$checksums" ]; then
  printf '%s\n' 'No Vellum release is published. Choose --local, provide a verified archive plus SHA256SUMS, or request an exact version once published.' >&2
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

if [ -n "$release_temporary" ]; then
  temporary="$release_temporary/extracted"
  mkdir "$temporary"
else
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-install.XXXXXX")
  trap 'rm -rf "$temporary"' EXIT HUP INT TERM
fi
python3 - "$archive" "$temporary" <<'PY'
from pathlib import PurePosixPath
import json
import sys
import tarfile

archive, destination = sys.argv[1:]
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    if len(members) > 10_000 or sum(member.size for member in members) > 2 * 1024**3:
        raise SystemExit("archive exceeds installer safety limits")
    if len({member.name for member in members}) != len(members):
        raise SystemExit("archive contains duplicate member names")
    for member in members:
        path = PurePosixPath(member.name)
        if (not path.parts or path.parts[0] not in {"vellum_cli.py", "templates", "sdk", "bin", "design-ir", "metadata.json"} or
                path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in path.parts[0] or
                member.issym() or member.islnk() or not (member.isfile() or member.isdir())):
            raise SystemExit(f"unsafe archive member: {member.name}")
    try:
        metadata_member = handle.getmember("metadata.json")
        metadata_file = handle.extractfile(metadata_member)
        metadata = json.load(metadata_file) if metadata_file else None
    except (KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid SDK artifact metadata: {error}")
    if (not isinstance(metadata, dict) or metadata.get("schema") != "vellum.sdk-artifact.v1" or
            not isinstance(metadata.get("framework_version"), str) or metadata.get("cli_api") != 1):
        raise SystemExit("incompatible SDK artifact metadata")
    handle.extractall(destination)
PY
[ -f "$temporary/vellum_cli.py" ] && [ -d "$temporary/templates/basic" ] && \
  [ -f "$temporary/metadata.json" ] && [ -d "$temporary/sdk" ] || {
  printf '%s\n' 'Verified archive does not contain the Vellum SDK artifact layout.' >&2
  exit 1
}
copy_payload "$temporary"
