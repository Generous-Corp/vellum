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

function Install-Payload([string]$Payload) {
    $library = Join-Path $InstallDir "lib\vellum"
    $bin = Join-Path $InstallDir "bin"
    New-Item -ItemType Directory -Force -Path $library, $bin | Out-Null
    Copy-Item (Join-Path $Payload "vellum_cli.py") (Join-Path $library "vellum_cli.py") -Force
    $templateDestination = Join-Path $library "templates"
    Remove-Item $templateDestination -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Payload "templates") $templateDestination -Recurse
    $sdkDestination = Join-Path $library "sdk"
    if (Test-Path (Join-Path $Payload "sdk")) {
        Copy-Item (Join-Path $Payload "metadata.json") (Join-Path $library "metadata.json") -Force
        Remove-Item $sdkDestination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $Payload "sdk") $sdkDestination -Recurse
        Remove-Item (Join-Path $library "bin") -Recurse -Force -ErrorAction SilentlyContinue
    } elseif (!(Test-Path (Join-Path $library "metadata.json"))) {
        Copy-Item (Join-Path $Payload "metadata.json") (Join-Path $library "metadata.json") -Force
    }
    $backend = Join-Path $Payload "bin\vellum-backend.exe"
    if (Test-Path $backend) {
        New-Item -ItemType Directory -Force -Path (Join-Path $library "bin") | Out-Null
        Copy-Item $backend (Join-Path $library "bin\vellum-backend.exe") -Force
    }
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
    $templates = Join-Path $LocalRoot "templates\basic"
    if (!(Test-Path $cli) -or !(Test-Path $templates)) { throw "Local root lacks cli/vellum_cli.py or templates/basic." }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("vellum-local-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Copy-Item $cli (Join-Path $temporary "vellum_cli.py")
        Copy-Item (Join-Path $LocalRoot "templates") (Join-Path $temporary "templates") -Recurse
        @'
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
'@ | Set-Content (Join-Path $temporary "metadata.json") -Encoding UTF8
        Write-Warning "LOCAL DEVELOPMENT INSTALL: source bytes are not release-verified; no native backend is included by default."
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
    if len(members) > 10_000 or sum(member.size for member in members) > 2 * 1024**3:
        raise SystemExit("archive exceeds installer safety limits")
    if len({member.name for member in members}) != len(members):
        raise SystemExit("archive contains duplicate member names")
    for member in members:
        path = PurePosixPath(member.name)
        if (not path.parts or path.parts[0] not in {"vellum_cli.py", "templates", "sdk", "bin", "metadata.json"} or
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
'@
    $extractor | python - $Archive $temporary
    if ($LASTEXITCODE -ne 0) { throw "Archive extraction validation failed." }
    if (!(Test-Path (Join-Path $temporary "vellum_cli.py")) -or
        !(Test-Path (Join-Path $temporary "templates\basic")) -or
        !(Test-Path (Join-Path $temporary "metadata.json")) -or
        !(Test-Path (Join-Path $temporary "sdk"))) {
        throw "Verified archive does not contain the Vellum SDK artifact layout."
    }
    Install-Payload $temporary
} finally {
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
    if ($download) { Remove-Item $download -Recurse -Force -ErrorAction SilentlyContinue }
}
