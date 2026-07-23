param(
    [string]$LocalRoot,
    [string]$Archive,
    [string]$Checksums,
    [string]$Version,
    [string]$ReleaseBaseUrl = $(if ($env:VELLUM_RELEASE_BASE_URL) { $env:VELLUM_RELEASE_BASE_URL } else { "https://github.com/Generous-Corp/vellum/releases/download" }),
    [string]$Target,
    [string]$InstallDir = $(if ($env:VELLUM_INSTALL_DIR) { $env:VELLUM_INSTALL_DIR } else { Join-Path $HOME ".local" })
)

$ErrorActionPreference = "Stop"
if (!$InstallDir -or $InstallDir -eq "/" -or $InstallDir -eq ".") { throw "Refusing unsafe install prefix: $InstallDir" }

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Assert-Node20 {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (!$node) { throw "Node.js 20+ is required for import/reimport." }
    $rendered = (& node --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $rendered -notmatch '^v?(\d+)(?:\.\d+){0,2}$' -or [int]$Matches[1] -lt 20) {
        throw "Node.js 20+ is required for import/reimport; found $rendered."
    }
}

function Install-Payload([string]$Payload) {
    $library = Join-Path $InstallDir "lib\vellum"
    $bin = Join-Path $InstallDir "bin"
    New-Item -ItemType Directory -Force -Path $library, $bin | Out-Null
    Copy-Item (Join-Path $Payload "vellum_cli.py") (Join-Path $library "vellum_cli.py") -Force
    Copy-Item (Join-Path $Payload "vellum_backend.py") (Join-Path $library "vellum_backend.py") -Force
    Copy-Item (Join-Path $Payload "metadata.json") (Join-Path $library "metadata.json") -Force
    Copy-Item (Join-Path $Payload "install-manifest.json") (Join-Path $library "install-manifest.json") -Force
    $templateDestination = Join-Path $library "templates"
    Remove-Item $templateDestination -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Payload "templates") $templateDestination -Recurse
    $sdkDestination = Join-Path $library "sdk"
    if (Test-Path (Join-Path $Payload "sdk")) {
        Remove-Item $sdkDestination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $Payload "sdk") $sdkDestination -Recurse
    } else {
        Remove-Item $sdkDestination -Recurse -Force -ErrorAction SilentlyContinue
    }
    $uiDestination = Join-Path $library "ui"
    Remove-Item $uiDestination -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path (Join-Path $Payload "ui")) {
        Copy-Item (Join-Path $Payload "ui") $uiDestination -Recurse
    }
    $nativeBackendSource = Join-Path $Payload "vellum_native_backend.py"
    $nativeBackendDestination = Join-Path $library "vellum_native_backend.py"
    Remove-Item $nativeBackendDestination -Force -ErrorAction SilentlyContinue
    if (Test-Path $nativeBackendSource) {
        Copy-Item $nativeBackendSource $nativeBackendDestination -Force
    }
    Remove-Item (Join-Path $library "bin") -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $library "bin") | Out-Null
    $designIr = Join-Path $Payload "design-ir"
    if (Test-Path $designIr) {
        Assert-Node20
        $designIrDestination = Join-Path $library "design-ir"
        Remove-Item $designIrDestination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $designIr $designIrDestination -Recurse
        $importLauncher = @'
@echo off
node "%~dp0..\design-ir\bin\vellum-backend.js" %*
'@
        $importLauncher = $importLauncher.Replace("`n", "`r`n")
        [IO.File]::WriteAllText((Join-Path $library "bin\vellum-import-backend.cmd"), $importLauncher)
    }
    if (Test-Path $nativeBackendSource) {
        $nativeLauncher = @'
@echo off
python "%~dp0..\vellum_native_backend.py" %*
'@
        $nativeLauncher = $nativeLauncher.Replace("`n", "`r`n")
        [IO.File]::WriteAllText((Join-Path $library "bin\vellum-native-backend.cmd"), $nativeLauncher)
    }
    $backendLauncher = @'
@echo off
python "%~dp0..\vellum_backend.py" %*
'@
    $backendLauncher = $backendLauncher.Replace("`n", "`r`n")
    [IO.File]::WriteAllText((Join-Path $library "bin\vellum-backend.cmd"), $backendLauncher)
    $launcher = @'
@echo off
set VELLUM_SDK_ROOT=%~dp0..\lib\vellum
python "%VELLUM_SDK_ROOT%\vellum_cli.py" %*
'@
    $launcher = $launcher.Replace("`n", "`r`n")
    [IO.File]::WriteAllText((Join-Path $bin "vellum.cmd"), $launcher)
    & (Join-Path $bin "vellum.cmd") --version
    Write-Host "Installed Vellum CLI to $(Join-Path $bin 'vellum.cmd')"
    Write-Host "Add $bin to PATH if needed."
}

if ($LocalRoot) {
    if ($Archive -or $Checksums -or $Version) { throw "-LocalRoot cannot be combined with artifact or release options." }
    $cli = Join-Path $LocalRoot "cli\vellum_cli.py"
    $dispatcher = Join-Path $LocalRoot "cli\vellum_backend.py"
    $templates = Join-Path $LocalRoot "templates\basic"
    $designIrPackage = Join-Path $LocalRoot "packages\vellum-design-ir"
    if (!(Test-Path $cli) -or !(Test-Path $dispatcher) -or !(Test-Path $templates) -or !(Test-Path $designIrPackage)) {
        throw "Local root lacks the CLI, dispatcher, templates, or DesignIR package."
    }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("vellum-local-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Copy-Item $cli (Join-Path $temporary "vellum_cli.py")
        Copy-Item (Join-Path $LocalRoot "cli\vellum_backend.py") (Join-Path $temporary "vellum_backend.py")
        Copy-Item (Join-Path $LocalRoot "templates") (Join-Path $temporary "templates") -Recurse
        $localMetadata = @'
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
    "gpu_renderer": false,
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
'@
        Write-Utf8NoBom (Join-Path $temporary "metadata.json") $localMetadata
        $localInstallManifest = @'
{
  "schema": "vellum.sdk-install.v1",
  "verified": false,
  "artifact": null,
  "artifact_sha256": null,
  "framework_version": "0.1.0",
  "target": "local-development",
  "source_commit": null
}
'@
        Write-Utf8NoBom (Join-Path $temporary "install-manifest.json") $localInstallManifest
        Copy-Item (Join-Path $LocalRoot "packages\vellum-design-ir") (Join-Path $temporary "design-ir") -Recurse
        Write-Warning "LOCAL DEVELOPMENT INSTALL: source bytes are not release-verified; import/reimport is included but no native backend is included by default."
        Install-Payload $temporary
    } finally { Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

if ($Version) {
    if ($Archive -or $Checksums) { throw "-Version cannot be combined with -Archive or -Checksums." }
    $Version = $Version.TrimStart("v")
    if (!$Version -or $Version -eq "latest" -or $Version -notmatch '^[0-9A-Za-z._-]+$') {
        throw "Release installs require a safe exact version, not latest."
    }
    if (!$Target) {
        if ($IsMacOS) { $os = "darwin" }
        elseif ($IsLinux) { $os = "linux" }
        else { $os = "windows" }
        $architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
        if ($architecture -eq "arm64") { $arch = "arm64" }
        elseif ($architecture -eq "x64") { $arch = "x86_64" }
        else { throw "Unsupported release architecture: $architecture" }
        $Target = "$os-$arch"
    }
    if ($Target -notmatch '^[0-9A-Za-z._-]+$') { throw "Release target contains unsafe characters." }
    $download = Join-Path ([IO.Path]::GetTempPath()) ("vellum-release-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $download | Out-Null
    $archiveName = "vellum-sdk-$Version-$Target.tar.gz"
    $releaseUrl = "$($ReleaseBaseUrl.TrimEnd('/'))/v$Version"
    $Archive = Join-Path $download $archiveName
    $Checksums = Join-Path $download "SHA256SUMS"
    try {
        Invoke-WebRequest "$releaseUrl/$archiveName" -OutFile $Archive
        Invoke-WebRequest "$releaseUrl/SHA256SUMS" -OutFile $Checksums
    } catch {
        Remove-Item $download -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
}

if (!$Archive -or !$Checksums) {
    throw "No Vellum release is published. Use -LocalRoot, provide -Archive plus -Checksums, or request an exact version once published."
}
if (!(Test-Path $Archive) -or !(Test-Path $Checksums)) { throw "Archive or checksum manifest not found." }

$archiveName = Split-Path $Archive -Leaf
$matching = Get-Content $Checksums | ForEach-Object {
    if ($_ -match '^([0-9A-Fa-f]{64})\s+\*?(.+)$' -and $Matches[2] -ceq $archiveName) { $Matches[1].ToLowerInvariant() }
} | Where-Object { $_ }
if (@($matching).Count -ne 1) { throw "Expected exactly one checksum for $archiveName; found $(@($matching).Count)." }
$actual = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
if ($actual -cne $matching[0]) { throw "SHA-256 mismatch for $archiveName. Refusing to extract." }
Write-Host "Verified SHA-256: $actual"

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("vellum-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $extractor = @'
from pathlib import PurePosixPath
import json
import sys
import tarfile

archive, destination = sys.argv[1:]
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    if len(members) > 20_000 or sum(member.size for member in members) > 4 * 1024**3:
        raise SystemExit("archive exceeds installer safety limits")
    if len({member.name for member in members}) != len(members):
        raise SystemExit("archive contains duplicate member names")
    for member in members:
        path = PurePosixPath(member.name)
        if (not path.parts or path.parts[0] not in {"vellum_cli.py", "vellum_backend.py", "vellum_native_backend.py", "templates", "sdk", "bin", "design-ir", "ui", "metadata.json"} or
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
            not isinstance(metadata.get("framework_version"), str) or not metadata["framework_version"] or
            metadata.get("cli_api") != 1 or
            not isinstance(metadata.get("target"), str) or not metadata["target"] or
            not isinstance(metadata.get("source_commit"), str) or
            len(metadata["source_commit"]) != 40 or
            any(character not in "0123456789abcdef" for character in metadata["source_commit"])):
        raise SystemExit("incompatible SDK artifact metadata")
    handle.extractall(destination)
'@
    $extractor | python - $Archive $temporary
    if ($LASTEXITCODE -ne 0) { throw "Archive extraction validation failed." }
    if (!(Test-Path (Join-Path $temporary "vellum_cli.py")) -or
        !(Test-Path (Join-Path $temporary "vellum_backend.py")) -or
        !(Test-Path (Join-Path $temporary "templates\basic")) -or
        !(Test-Path (Join-Path $temporary "design-ir")) -or
        !(Test-Path (Join-Path $temporary "metadata.json")) -or
        !(Test-Path (Join-Path $temporary "sdk"))) {
        throw "Verified archive does not contain the Vellum SDK artifact layout."
    }
    $metadata = Get-Content (Join-Path $temporary "metadata.json") -Raw | ConvertFrom-Json
    $installManifest = [ordered]@{
        schema = "vellum.sdk-install.v1"
        verified = $true
        artifact = $archiveName
        artifact_sha256 = $actual
        framework_version = $metadata.framework_version
        target = $metadata.target
        source_commit = $metadata.source_commit
    }
    Write-Utf8NoBom (Join-Path $temporary "install-manifest.json") ($installManifest | ConvertTo-Json)
    Install-Payload $temporary
} finally {
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
    if ($download) { Remove-Item $download -Recurse -Force -ErrorAction SilentlyContinue }
}
