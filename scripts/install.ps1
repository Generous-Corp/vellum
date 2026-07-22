param(
    [string]$LocalRoot,
    [string]$Archive,
    [string]$Checksums,
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
    if ($Archive -or $Checksums) { throw "-LocalRoot cannot be combined with -Archive or -Checksums." }
    $cli = Join-Path $LocalRoot "cli\vellum_cli.py"
    $templates = Join-Path $LocalRoot "templates\basic"
    if (!(Test-Path $cli) -or !(Test-Path $templates)) { throw "Local root lacks cli/vellum_cli.py or templates/basic." }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("vellum-local-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Copy-Item $cli (Join-Path $temporary "vellum_cli.py")
        Copy-Item (Join-Path $LocalRoot "templates") (Join-Path $temporary "templates") -Recurse
        Write-Warning "LOCAL DEVELOPMENT INSTALL: source bytes are not release-verified; no native backend is included by default."
        Install-Payload $temporary
    } finally { Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

if (!$Archive -or !$Checksums) {
    throw "No Vellum release is published. Use -LocalRoot, or provide both -Archive and -Checksums."
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
'@
    $extractor | python - $Archive $temporary
    if ($LASTEXITCODE -ne 0) { throw "Archive extraction validation failed." }
    if (!(Test-Path (Join-Path $temporary "vellum_cli.py")) -or !(Test-Path (Join-Path $temporary "templates\basic"))) {
        throw "Verified archive does not contain the Vellum CLI artifact layout."
    }
    Install-Payload $temporary
} finally { Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue }
