#!/bin/sh
# Local-development and verified-artifact installer for the Vellum SDK.
set -eu

install_prefix=${VELLUM_INSTALL_DIR:-"$HOME/.local"}
script_dir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)
local_root=
archive=
checksums=
version=
official_release_base=https://github.com/danielraffel/vellum/releases/download
release_base=${VELLUM_RELEASE_BASE_URL:-$official_release_base}
release_target=
uninstall=false
verify_installed=false

usage() {
  printf '%s\n' \
    'Usage:' \
    '  scripts/install.sh --local PATH [--install-dir PREFIX]' \
    '  scripts/install.sh --archive FILE --checksums SHA256SUMS [--install-dir PREFIX]' \
    '  scripts/install.sh --version VERSION [--release-base-url URL] [--target TARGET]' \
    '  scripts/install.sh --verify-installed [--install-dir PREFIX]' \
    '  scripts/install.sh --uninstall [--install-dir PREFIX]' \
    '' \
    'Private exact-version installs require an authenticated GitHub CLI.' \
    '--local is an explicitly unverified development install. Artifact and' \
    'exact-version release modes verify release digests and SHA-256 before' \
    'extracting.'
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
    --verify-installed) verify_installed=true; shift ;;
    --uninstall) uninstall=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  printf '%s\n' \
    'Python 3.9+ is required. On macOS, run `xcode-select --install` or install Python 3.9+ from python.org.' >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' || {
  python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || printf unknown)
  printf 'Python 3.9+ is required; found %s. Install current Xcode Command Line Tools or Python from python.org.\n' \
    "$python_version" >&2
  exit 1
}
install_prefix=$(python3 -c \
  'from pathlib import Path; import sys
p = Path(sys.argv[1]).expanduser().resolve()
if p == Path("/") or p == Path.home().resolve() or len(p.parts) < 3:
    raise SystemExit(2)
print(p)' \
  "$install_prefix") || {
    printf '%s\n' 'Refusing an unsafe or overly broad install prefix.' >&2
    exit 2
  }

if [ -n "$local_root" ] && { [ -n "$archive" ] || [ -n "$checksums" ] || [ -n "$version" ]; }; then
  printf '%s\n' '--local cannot be combined with artifact or release options.' >&2
  exit 2
fi
if [ -n "$version" ] && { [ -n "$archive" ] || [ -n "$checksums" ]; }; then
  printf '%s\n' '--version cannot be combined with --archive or --checksums.' >&2
  exit 2
fi
if [ "$uninstall" = true ] || [ "$verify_installed" = true ]; then
  [ "$uninstall" != "$verify_installed" ] || {
    printf '%s\n' '--uninstall and --verify-installed cannot be combined.' >&2
    exit 2
  }
  [ -z "$local_root" ] && [ -z "$archive" ] && [ -z "$checksums" ] && [ -z "$version" ] || {
    printf '%s\n' 'Installer lifecycle actions cannot be combined with install inputs.' >&2
    exit 2
  }
  [ -f "$script_dir/install_core.py" ] || {
    printf 'Transactional installer core is missing: %s\n' "$script_dir/install_core.py" >&2
    exit 1
  }
  if [ "$uninstall" = true ]; then
    exec python3 "$script_dir/install_core.py" uninstall --prefix "$install_prefix"
  fi
  exec python3 "$script_dir/install_core.py" verify-installed --prefix "$install_prefix"
fi

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

require_node_20() {
  command -v node >/dev/null 2>&1 || {
    printf '%s\n' 'Node.js 20+ is required for import/reimport.' >&2
    exit 1
  }
  node_version=$(node --version 2>/dev/null || true)
  node_major=$(printf '%s\n' "$node_version" | sed -n 's/^v\{0,1\}\([0-9][0-9]*\)\(\..*\)\{0,1\}$/\1/p')
  [ -n "$node_major" ] && [ "$node_major" -ge 20 ] || {
    printf 'Node.js 20+ is required for import/reimport; found %s.\n' "${node_version:-unknown}" >&2
    exit 1
  }
}

require_github_cli_release_verification() {
  command -v gh >/dev/null 2>&1 || {
    printf '%s\n' \
      'GitHub CLI 2.75.0+ is required to authenticate and verify the private Vellum release.' >&2
    exit 1
  }
  gh_version=$(gh --version 2>/dev/null | sed -n '1s/^gh version \([0-9][0-9.]*\).*$/\1/p')
  python3 -c \
    'import re, sys
match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.[0-9]+)?", sys.argv[1])
raise SystemExit(0 if match and tuple(map(int, match.groups()[:2])) >= (2, 75) else 1)' \
    "$gh_version" &&
    gh release verify-asset --help >/dev/null 2>&1 || {
      printf 'GitHub CLI 2.75.0+ with release verify-asset support is required; found %s.\n' \
        "${gh_version:-unknown}" >&2
      exit 1
    }
}

copy_payload() {
  payload=$1
  library="$install_prefix/lib/vellum"
  bindir="$install_prefix/bin"
  mkdir -p "$library" "$bindir"
  cp "$payload/vellum_cli.py" "$library/vellum_cli.py"
  cp "$payload/vellum_dev.py" "$library/vellum_dev.py"
  cp "$payload/vellum_backend.py" "$library/vellum_backend.py"
  cp "$payload/vellum_manifest.py" "$library/vellum_manifest.py"
  cp "$payload/vellum_png.py" "$library/vellum_png.py"
  cp "$payload/vellum_image_compare.py" "$library/vellum_image_compare.py"
  cp "$payload/metadata.json" "$library/metadata.json"
  cp "$payload/install-manifest.json" "$library/install-manifest.json"
  rm -rf "$library/.agents"
  cp -R "$payload/.agents" "$library/.agents"
  rm -rf "$library/templates"
  cp -R "$payload/templates" "$library/templates"
  if [ -d "$payload/sdk" ]; then
    rm -rf "$library/sdk"
    cp -R "$payload/sdk" "$library/sdk"
  else
    rm -rf "$library/sdk"
  fi
  rm -rf "$library/ui"
  if [ -d "$payload/ui" ]; then
    cp -R "$payload/ui" "$library/ui"
  fi
  rm -rf "$library/node"
  if [ -x "$payload/node/bin/node" ]; then
    cp -R "$payload/node" "$library/node"
  fi
  rm -rf "$library/web"
  if [ -d "$payload/web" ]; then
    cp -R "$payload/web" "$library/web"
  fi
  rm -f "$library/vellum_native_backend.py"
  if [ -f "$payload/vellum_native_backend.py" ]; then
    cp "$payload/vellum_native_backend.py" "$library/vellum_native_backend.py"
  fi
  rm -f "$library/vellum_web_backend.py"
  if [ -f "$payload/vellum_web_backend.py" ]; then
    cp "$payload/vellum_web_backend.py" "$library/vellum_web_backend.py"
  fi
  rm -rf "${library:?}/bin"
  mkdir -p "$library/bin"
  if [ -d "$payload/design-ir" ]; then
    if [ -x "$library/node/bin/node" ]; then
      node_version=$("$library/node/bin/node" --version 2>/dev/null || true)
    else
      require_node_20
    fi
    rm -rf "$library/design-ir"
    cp -R "$payload/design-ir" "$library/design-ir"
    {
      printf '%s\n' '#!/bin/sh' 'set -eu'
      # shellcheck disable=SC2016
      printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
      # shellcheck disable=SC2016
      printf '%s\n' 'if [ -x "$bindir/../node/bin/node" ]; then exec "$bindir/../node/bin/node" "$bindir/../design-ir/bin/vellum-backend.js" "$@"; fi'
      printf '%s\n' 'exec node "$bindir/../design-ir/bin/vellum-backend.js" "$@"'
    } > "$library/bin/vellum-import-backend"
    chmod 755 "$library/bin/vellum-import-backend"
  fi
  if [ -f "$payload/vellum_native_backend.py" ]; then
    {
      printf '%s\n' '#!/bin/sh' 'set -eu'
      # shellcheck disable=SC2016
      printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
      printf '%s\n' 'export PYTHONDONTWRITEBYTECODE=1'
      # shellcheck disable=SC2016
      printf '%s\n' 'exec python3 "$bindir/../vellum_native_backend.py" "$@"'
    } > "$library/bin/vellum-native-backend"
    chmod 755 "$library/bin/vellum-native-backend"
  fi
  if [ -f "$payload/vellum_web_backend.py" ]; then
    {
      printf '%s\n' '#!/bin/sh' 'set -eu'
      printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
      printf '%s\n' 'export PYTHONDONTWRITEBYTECODE=1'
      printf '%s\n' 'exec python3 "$bindir/../vellum_web_backend.py" "$@"'
    } > "$library/bin/vellum-web-backend"
    chmod 755 "$library/bin/vellum-web-backend"
  fi
  {
    printf '%s\n' '#!/bin/sh' 'set -eu'
    # shellcheck disable=SC2016
    printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
    printf '%s\n' 'export PYTHONDONTWRITEBYTECODE=1'
    # shellcheck disable=SC2016
    printf '%s\n' 'exec python3 "$bindir/../vellum_backend.py" "$@"'
  } > "$library/bin/vellum-backend"
  chmod 755 "$library/bin/vellum-backend"
  {
    printf '%s\n' '#!/bin/sh' 'set -eu'
    # These expressions are intentionally written literally into the launcher.
    # shellcheck disable=SC2016
    printf '%s\n' 'bindir=$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)'
    # shellcheck disable=SC2016
    printf '%s\n' 'prefix=$(CDPATH="" cd -- "$bindir/.." && pwd)'
    # shellcheck disable=SC2016
    printf '%s\n' 'export VELLUM_SDK_ROOT="$prefix/lib/vellum"'
    printf '%s\n' 'export PYTHONDONTWRITEBYTECODE=1'
    # shellcheck disable=SC2016
    printf '%s\n' 'exec python3 "$VELLUM_SDK_ROOT/vellum_cli.py" "$@"'
  } > "$bindir/vellum"
  chmod 755 "$bindir/vellum"
  "$bindir/vellum" --version
  printf 'Installed Vellum CLI to %s\n' "$bindir/vellum"
  printf 'Add %s to PATH if needed.\n' "$bindir"
}

if [ -n "$local_root" ]; then
  for managed_path in \
    "$install_prefix/lib/vellum" \
    "$install_prefix/bin/vellum" \
    "$install_prefix/lib/vellum-installs" \
    "$install_prefix/lib/vellum-cache" \
    "$install_prefix/lib/vellum-installer-state.json" \
    "$install_prefix/.vellum-installer.lock"
  do
    if [ -e "$managed_path" ] || [ -L "$managed_path" ]; then
      printf 'Refusing unmanaged or transactional local-install path: %s\n' \
        "$managed_path" >&2
      exit 1
    fi
  done
  if [ -L "$install_prefix/lib" ] || [ -L "$install_prefix/bin" ]; then
    printf '%s\n' \
      'Refusing symlinked local-install storage. Use a separate --install-dir.' >&2
    exit 1
  fi
  [ -f "$local_root/cli/vellum_cli.py" ] && [ -f "$local_root/cli/vellum_dev.py" ] && \
    [ -f "$local_root/cli/vellum_backend.py" ] && \
    [ -f "$local_root/cli/vellum_manifest.py" ] && \
    [ -f "$local_root/cli/vellum_png.py" ] && \
    [ -f "$local_root/cli/vellum_image_compare.py" ] && \
    [ -f "$local_root/.agents/skills/vellum-app-authoring/SKILL.md" ] && \
    [ -f "$local_root/.agents/skills/vellum-app-authoring/manifest.v1.json" ] && \
    [ -d "$local_root/templates/basic" ] && [ -d "$local_root/packages/vellum-design-ir" ] || {
    printf '%s\n' 'Local root lacks the CLI, dispatcher, agent instructions, templates, or DesignIR package.' >&2
    exit 1
  }
  mkdir -p "$install_prefix"
  local_lock="$install_prefix/.vellum-local-installing"
  mkdir "$local_lock" 2>/dev/null || {
    printf '%s\n' 'Another Vellum local install is already using this prefix.' >&2
    exit 1
  }
  if [ -e "$install_prefix/.vellum-installer.lock" ] || \
     [ -L "$install_prefix/.vellum-installer.lock" ]; then
    rmdir "$local_lock"
    printf '%s\n' 'A transactional Vellum operation owns this prefix.' >&2
    exit 1
  fi
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-local.XXXXXX")
  trap 'rm -rf "$temporary"; rmdir "$local_lock" 2>/dev/null || true' EXIT HUP INT TERM
  cp "$local_root/cli/vellum_cli.py" "$temporary/vellum_cli.py"
  cp "$local_root/cli/vellum_dev.py" "$temporary/vellum_dev.py"
  cp "$local_root/cli/vellum_backend.py" "$temporary/vellum_backend.py"
  cp "$local_root/cli/vellum_manifest.py" "$temporary/vellum_manifest.py"
  cp "$local_root/cli/vellum_png.py" "$temporary/vellum_png.py"
  cp "$local_root/cli/vellum_image_compare.py" "$temporary/vellum_image_compare.py"
  mkdir -p "$temporary/.agents/skills"
  cp -R \
    "$local_root/.agents/skills/vellum-app-authoring" \
    "$temporary/.agents/skills/vellum-app-authoring"
  cp -R "$local_root/templates" "$temporary/templates"
  cat > "$temporary/metadata.json" <<'JSON'
{
  "schema": "vellum.sdk-artifact.v1",
  "framework_version": "0.1.7",
  "cli_version": "0.1.7",
  "cli_api": 1,
  "source_commit": null,
  "target": "local-development",
  "capabilities": {
    "cmake_sdk": false,
    "authoring_cli": true,
    "gpu_renderer": false,
    "custom_components": false,
    "commands": {
      "import": true,
      "reimport": true,
      "build": false,
      "run": false,
      "test": false,
      "capture": false,
      "package": false
    }
  },
  "files": []
}
JSON
  cat > "$temporary/install-manifest.json" <<'JSON'
{
  "schema": "vellum.sdk-install.v1",
  "verified": false,
  "artifact": null,
  "artifact_sha256": null,
  "framework_version": "0.1.7",
  "target": "local-development",
  "source_commit": null
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
  if [ "$release_base" = "$official_release_base" ]; then
    if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ] || \
       [ "$release_target" != "darwin-arm64" ]; then
      printf 'Vellum v%s application SDK releases currently support only macOS 15.0+ arm64; detected %s %s and target %s.\n' \
        "$version" "$(uname -s)" "$(uname -m)" "$release_target" >&2
      exit 1
    fi
    command -v sw_vers >/dev/null 2>&1 || {
      printf '%s\n' \
        'Vellum application SDK releases require macOS 15.0 or newer, but sw_vers is unavailable.' >&2
      exit 1
    }
    macos_version=$(sw_vers -productVersion 2>/dev/null || true)
    python3 -c \
      'import re, sys
value = sys.argv[1]
match = re.fullmatch(r"([0-9]+)(?:\.[0-9]+){0,2}", value)
raise SystemExit(0 if match and int(match.group(1)) >= 15 else 1)' \
      "$macos_version" || {
      printf 'Vellum application SDK releases require macOS 15.0 or newer; found %s.\n' \
        "${macos_version:-unknown}" >&2
      exit 1
    }
  fi
  release_temporary=$(mktemp -d "${TMPDIR:-/tmp}/vellum-release.XXXXXX")
  trap 'rm -rf "$release_temporary"' EXIT HUP INT TERM
  archive_name="vellum-sdk-$version-$release_target.tar.gz"
  release_url="${release_base%/}/v$version"
  archive="$release_temporary/$archive_name"
  checksums="$release_temporary/SHA256SUMS"
  release_core="$release_temporary/install_core.py"
  if [ "$release_base" = "$official_release_base" ]; then
    require_github_cli_release_verification
    gh release download "v$version" \
      --repo danielraffel/vellum \
      --pattern SHA256SUMS \
      --pattern install_core.py \
      --pattern "$archive_name" \
      --dir "$release_temporary" \
      --clobber || {
      printf '%s\n' 'Could not authenticate the private Vellum release. Set GH_TOKEN/GITHUB_TOKEN or run gh auth login.' >&2
      exit 1
    }
    for release_asset in "$checksums" "$release_core" "$archive"
    do
      gh release verify-asset "v$version" "$release_asset" \
        --repo danielraffel/vellum >/dev/null || {
        printf 'GitHub release digest verification failed for %s.\n' \
          "$(basename "$release_asset")" >&2
        exit 1
      }
    done
  else
    case "$release_base" in
      https://*|file://*) ;;
      *) printf '%s\n' 'Custom release URLs must use HTTPS or file://.' >&2; exit 2 ;;
    esac
    command -v curl >/dev/null 2>&1 || {
      printf '%s\n' 'curl is required for custom release URLs.' >&2
      exit 1
    }
    curl -q --proto '=https,file' --proto-redir '=https,file' \
      -fsSL "$release_url/SHA256SUMS" -o "$checksums" || {
      printf 'Could not download the Vellum release manifest from %s.\n' "$release_url" >&2
      exit 1
    }
    curl -q --proto '=https,file' --proto-redir '=https,file' \
      -fsSL "$release_url/install_core.py" -o "$release_core" || {
      printf '%s\n' 'Release is missing install_core.py.' >&2
      exit 1
    }
    curl -q --proto '=https,file' --proto-redir '=https,file' \
      -fsSL "$release_url/$archive_name" -o "$archive" || {
      printf 'Release is missing %s.\n' "$archive_name" >&2
      exit 1
    }
  fi
  [ -f "$checksums" ] || {
    printf '%s\n' 'Release is missing SHA256SUMS.' >&2
    exit 1
  }
  [ -f "$release_core" ] || {
    printf '%s\n' 'Release is missing install_core.py.' >&2
    exit 1
  }
  [ -f "$archive" ] || {
    printf 'Release is missing %s.\n' "$archive_name" >&2
    exit 1
  }
  core_matches=$(awk '$2 == "install_core.py" || $2 == "*install_core.py" { print $1 }' "$checksums")
  core_count=$(printf '%s\n' "$core_matches" | awk 'NF { count++ } END { print count + 0 }')
  [ "$core_count" -eq 1 ] || {
    printf 'Expected exactly one checksum for install_core.py; found %s.\n' "$core_count" >&2
    exit 1
  }
  core_expected=$(printf '%s\n' "$core_matches" | awk 'NF { print; exit }')
  core_actual=$(sha256_file "$release_core")
  [ "$core_expected" = "$core_actual" ] || {
    printf '%s\n' 'SHA-256 mismatch for install_core.py. Refusing to execute it.' >&2
    exit 1
  }
  script_dir=$release_temporary
fi

if [ -z "$archive" ] || [ -z "$checksums" ]; then
  printf '%s\n' 'Choose --local, provide an archive plus SHA256SUMS, or request an exact private release version.' >&2
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
[ -f "$script_dir/install_core.py" ] || {
  printf 'Transactional installer core is missing: %s\n' "$script_dir/install_core.py" >&2
  exit 1
}
if [ -n "$version" ]; then
  python3 "$script_dir/install_core.py" install \
    --archive "$archive" \
    --checksums "$checksums" \
    --prefix "$install_prefix" \
    --expected-version "$version" \
    --expected-target "$release_target"
else
  python3 "$script_dir/install_core.py" install \
    --archive "$archive" \
    --checksums "$checksums" \
    --prefix "$install_prefix"
fi
