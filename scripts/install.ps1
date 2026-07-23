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
    Copy-Item (Join-Path $Payload "vellum_manifest.py") (Join-Path $library "vellum_manifest.py") -Force
    Copy-Item (Join-Path $Payload "vellum_png.py") (Join-Path $library "vellum_png.py") -Force
    Copy-Item (Join-Path $Payload "metadata.json") (Join-Path $library "metadata.json") -Force
    Copy-Item (Join-Path $Payload "install-manifest.json") (Join-Path $library "install-manifest.json") -Force
    $agentDestination = Join-Path $library ".agents"
    Remove-Item $agentDestination -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Payload ".agents") $agentDestination -Recurse
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
    foreach ($name in @("node", "web")) {
        $destination = Join-Path $library $name
        Remove-Item $destination -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path (Join-Path $Payload $name)) {
            Copy-Item (Join-Path $Payload $name) $destination -Recurse
        }
    }
    $nativeBackendSource = Join-Path $Payload "vellum_native_backend.py"
    $nativeBackendDestination = Join-Path $library "vellum_native_backend.py"
    Remove-Item $nativeBackendDestination -Force -ErrorAction SilentlyContinue
    if (Test-Path $nativeBackendSource) {
        Copy-Item $nativeBackendSource $nativeBackendDestination -Force
    }
    $webBackendSource = Join-Path $Payload "vellum_web_backend.py"
    $webBackendDestination = Join-Path $library "vellum_web_backend.py"
    Remove-Item $webBackendDestination -Force -ErrorAction SilentlyContinue
    if (Test-Path $webBackendSource) {
        Copy-Item $webBackendSource $webBackendDestination -Force
    }
    Remove-Item (Join-Path $library "bin") -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $library "bin") | Out-Null
    $designIr = Join-Path $Payload "design-ir"
    if (Test-Path $designIr) {
        $localNode = Join-Path $library "node\bin\node.exe"
        if (!(Test-Path $localNode)) { $localNode = Join-Path $library "node\bin\node" }
        if (!(Test-Path $localNode)) { Assert-Node20 }
        $designIrDestination = Join-Path $library "design-ir"
        Remove-Item $designIrDestination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $designIr $designIrDestination -Recurse
        $importLauncher = @'
@echo off
if exist "%~dp0..\node\bin\node.exe" "%~dp0..\node\bin\node.exe" "%~dp0..\design-ir\bin\vellum-backend.js" %* & exit /b %errorlevel%
if exist "%~dp0..\node\bin\node" "%~dp0..\node\bin\node" "%~dp0..\design-ir\bin\vellum-backend.js" %* & exit /b %errorlevel%
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
    if (Test-Path $webBackendSource) {
        $webLauncher = @'
@echo off
python "%~dp0..\vellum_web_backend.py" %*
'@
        $webLauncher = $webLauncher.Replace("`n", "`r`n")
        [IO.File]::WriteAllText((Join-Path $library "bin\vellum-web-backend.cmd"), $webLauncher)
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
    $manifestReader = Join-Path $LocalRoot "cli\vellum_manifest.py"
    $pngTools = Join-Path $LocalRoot "cli\vellum_png.py"
    $agentSkill = Join-Path $LocalRoot ".agents\skills\vellum-app-authoring\SKILL.md"
    $agentManifest = Join-Path $LocalRoot ".agents\skills\vellum-app-authoring\manifest.v1.json"
    $templates = Join-Path $LocalRoot "templates\basic"
    $designIrPackage = Join-Path $LocalRoot "packages\vellum-design-ir"
    if (!(Test-Path $cli) -or !(Test-Path $dispatcher) -or !(Test-Path $manifestReader) -or !(Test-Path $pngTools) -or !(Test-Path $agentSkill) -or
        !(Test-Path $agentManifest) -or !(Test-Path $templates) -or !(Test-Path $designIrPackage)) {
        throw "Local root lacks the CLI, dispatcher, agent instructions, templates, or DesignIR package."
    }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("vellum-local-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        Copy-Item $cli (Join-Path $temporary "vellum_cli.py")
        Copy-Item (Join-Path $LocalRoot "cli\vellum_backend.py") (Join-Path $temporary "vellum_backend.py")
        Copy-Item (Join-Path $LocalRoot "cli\vellum_manifest.py") (Join-Path $temporary "vellum_manifest.py")
        Copy-Item (Join-Path $LocalRoot "cli\vellum_png.py") (Join-Path $temporary "vellum_png.py")
        Copy-Item (Join-Path $LocalRoot ".agents") (Join-Path $temporary ".agents") -Recurse
        Copy-Item (Join-Path $LocalRoot "templates") (Join-Path $temporary "templates") -Recurse
        $localMetadata = @'
{
  "schema": "vellum.sdk-artifact.v1",
  "framework_version": "0.1.3",
  "cli_version": "0.1.3",
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
'@
        Write-Utf8NoBom (Join-Path $temporary "metadata.json") $localMetadata
        $localInstallManifest = @'
{
  "schema": "vellum.sdk-install.v1",
  "verified": false,
  "artifact": null,
  "artifact_sha256": null,
  "framework_version": "0.1.3",
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

throw @"
Verified archive and release installation are unavailable in PowerShell for
Vellum v0.1.3. The application SDK currently supports macOS 15.0+ arm64; use
scripts/install.sh there. That installer delegates archive verification,
extraction, immutable storage, and activation to the canonical install_core.py.
PowerShell currently supports only -LocalRoot development installs.
"@
