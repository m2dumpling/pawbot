param(
    [switch]$Dev,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$Package = "pawbot-ai"
$InstallTarget = $Package
$InstallSource = "PyPI"
$script:PawbotRunner = $null
$script:PawbotPython = $null
$script:LastInstallSucceeded = $false

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Fail {
    param([string]$Message)
    throw "Error: $Message"
}

function Show-InstallFailureHint {
    [Console]::Error.WriteLine("Error: could not install pawbot from $InstallSource.")
    [Console]::Error.WriteLine("If pip mentioned externally-managed-environment, use uv, pipx, or a virtual environment instead of system pip.")
    [Console]::Error.WriteLine("You can also run manually:")
    [Console]::Error.WriteLine("  uv tool install --force --upgrade $InstallTarget")
    [Console]::Error.WriteLine("  $Python -m venv `$HOME\.pawbot\venv")
    [Console]::Error.WriteLine("  `$HOME\.pawbot\venv\Scripts\python.exe -m pip install --upgrade $InstallTarget")
    [Console]::Error.WriteLine("Then open the WebUI with:")
    [Console]::Error.WriteLine("  pawbot")
    throw "could not install pawbot from $InstallSource"
}

function Show-Usage {
    Write-Host "Usage: install.ps1 [-DryRun|--dry-run]"
    Write-Host ""
    Write-Host "By default this installs or upgrades pawbot-ai from PyPI."
    Write-Host "Use --dry-run to print what would happen without installing or starting setup."
    Write-Host ""
    Write-Host "For current main, clone the repository and run 'python -m pip install -e .'."
}

function Test-Python {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-Python {
    if ($env:PYTHON) {
        if (Get-Command $env:PYTHON -ErrorAction SilentlyContinue) {
            if (Test-Python $env:PYTHON) {
                return $env:PYTHON
            }
            Fail "PYTHON=$env:PYTHON is not Python 3.11 or newer."
        }
        Fail "PYTHON=$env:PYTHON was not found."
    }

    foreach ($Candidate in @("python", "py")) {
        if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
            if (Test-Python $Candidate) {
                return $Candidate
            }
        }
    }

    Fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
}

function Test-VirtualEnv {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-Pip {
    param([string]$Command)

    try {
        & $Command -m pip --version *> $null
    } catch {}

    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Info "pip was not found for $Command. Trying ensurepip..."
    & $Command -m ensurepip --upgrade *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "pip is not available. Install pip for $Command, then rerun this command."
    }
}

function Invoke-Pawbot {
    param([string[]]$PawbotArgs)

    switch ($script:PawbotRunner) {
        "uv" {
            & uv tool run --from $InstallTarget pawbot @PawbotArgs
        }
        "pipx" {
            & pipx run --spec $InstallTarget pawbot @PawbotArgs
        }
        "python" {
            & $script:PawbotPython -m pawbot @PawbotArgs
        }
        default {
            Fail "pawbot was installed, but no runner was configured."
        }
    }
}

function Get-PawbotCommand {
    switch ($script:PawbotRunner) {
        "uv" { return "uv tool run --from $InstallTarget pawbot" }
        "pipx" { return "pipx run --spec $InstallTarget pawbot" }
        "python" { return "$script:PawbotPython -m pawbot" }
        default { return "pawbot" }
    }
}

function Test-FreshPawbotInstall {
    $HomeDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not $HomeDir) {
        return $false
    }
    return -not (Test-Path -LiteralPath (Join-Path $HomeDir ".pawbot\config.json"))
}

function Test-BrowserSession {
    if ($env:SSH_CONNECTION -or $env:SSH_TTY -or -not [Environment]::UserInteractive) {
        return $false
    }

    $CurrentSessionId = (Get-Process -Id $PID).SessionId
    return @(
        Get-Process -Name explorer -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $CurrentSessionId }
    ).Count -gt 0
}

function Install-WithActivePython {
    Write-Info "Detected an active virtual environment. Installing into it..."
    Ensure-Pip $Python
    & $Python -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }
    $script:PawbotRunner = "python"
    $script:PawbotPython = $Python
}

function Install-WithUv {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing or upgrading pawbot from $InstallSource with uv tool..."
    & uv tool install --python $Python --force --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $script:PawbotRunner = "uv"
    $script:LastInstallSucceeded = $true
}

function Install-WithPipx {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing or upgrading pawbot from $InstallSource with pipx..."
    & pipx install --python $Python --force $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $script:PawbotRunner = "pipx"
    $script:LastInstallSucceeded = $true
}

function Install-WithManagedVenv {
    $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $null }
    if (-not $HomeDir) {
        Fail "HOME is not set; cannot create a managed virtual environment."
    }

    $VenvDir = if ($env:PAWBOT_VENV) { $env:PAWBOT_VENV } else { Join-Path $HomeDir ".pawbot\venv" }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (-not (Test-Path $VenvPython)) {
        Write-Info "Creating a dedicated virtual environment at $VenvDir..."
        $Parent = Split-Path -Parent $VenvDir
        if ($Parent) {
            New-Item -ItemType Directory -Force -Path $Parent *> $null
        }
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Show-InstallFailureHint
        }
    }

    if (-not (Test-Python $VenvPython)) {
        Fail "The managed venv uses Python older than 3.11. Remove it or set PAWBOT_VENV to a new path."
    }

    Write-Info "Installing or upgrading pawbot from $InstallSource in $VenvDir..."
    Ensure-Pip $VenvPython
    & $VenvPython -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }

    $script:PawbotRunner = "python"
    $script:PawbotPython = $VenvPython
}

foreach ($Arg in $RemainingArgs) {
    switch ($Arg) {
        "--dev" {
            $Dev = $true
        }
        "--dry-run" {
            $DryRun = $true
        }
        "-h" {
            Show-Usage
            return
        }
        "--help" {
            Show-Usage
            return
        }
        default {
            Fail "Unknown option: $Arg"
        }
    }
}

if ($Dev) {
    Fail "--dev installed an untracked main snapshot and is no longer supported; clone the repository and run 'python -m pip install -e .' instead."
}

$Python = Find-Python
$Version = & $Python --version
Write-Info "Using Python: $Version"

if ($DryRun) {
    Write-Info "Dry run: would install or upgrade pawbot from $InstallSource."
    if (Test-VirtualEnv $Python) {
        Write-Info "Dry run: active virtual environment detected; would run: $Python -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would run pawbot as: $Python -m pawbot"
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: uv tool install --python $Python --force --upgrade $InstallTarget"
        Write-Info "Dry run: would run pawbot as: uv tool run --from $InstallTarget pawbot"
    } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: pipx install --python $Python --force $InstallTarget"
        Write-Info "Dry run: would run pawbot as: pipx run --spec $InstallTarget pawbot"
    } else {
        $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { "~" }
        $VenvDir = if ($env:PAWBOT_VENV) { $env:PAWBOT_VENV } else { Join-Path $HomeDir ".pawbot\venv" }
        Write-Info "Dry run: would create or reuse a dedicated virtual environment: $VenvDir"
        Write-Info "Dry run: would run: $VenvDir\Scripts\python.exe -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would run pawbot as: $VenvDir\Scripts\python.exe -m pawbot"
    }
    if ($env:PAWBOT_SKIP_WIZARD -eq "1") {
        Write-Info "Dry run: would skip automatic setup because PAWBOT_SKIP_WIZARD=1."
    } elseif ((Test-FreshPawbotInstall) -and (Test-BrowserSession)) {
        Write-Info "Dry run: would start the WebUI for this fresh desktop install."
        Write-Info "Dry run: would fall back to the setup wizard for older releases."
    } else {
        Write-Info "Dry run: would run the setup wizard."
    }
    Write-Info "Dry run: no changes made."
    return
}

if (Test-VirtualEnv $Python) {
    Install-WithActivePython
} else {
    $Installed = $false

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Install-WithUv
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "uv tool install failed. Trying the next isolated install method..."
        }
    }

    if (-not $Installed -and (Get-Command pipx -ErrorAction SilentlyContinue)) {
        Install-WithPipx
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "pipx install failed. Trying the managed virtual environment..."
        }
    }

    if (-not $Installed) {
        Write-Info "Using a dedicated virtual environment to avoid system pip."
        Install-WithManagedVenv
    }
}

Write-Info "Installed pawbot:"
Invoke-Pawbot @("--version")
if ($LASTEXITCODE -ne 0) {
    Fail "pawbot was installed, but the command could not be started."
}

if ($env:PAWBOT_SKIP_WIZARD -eq "1") {
    Write-Info "Skipping automatic setup because PAWBOT_SKIP_WIZARD=1."
    Write-Info "Run this later: $(Get-PawbotCommand) webui"
    return
}

if ((Test-FreshPawbotInstall) -and (Test-BrowserSession)) {
    Invoke-Pawbot @("webui", "--help") *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Starting pawbot WebUI..."
        Write-Info "Configure your first provider and model in Settings > Models."
        Write-Info "Run this later: $(Get-PawbotCommand) webui"
        Invoke-Pawbot @("webui", "--yes")
        if ($LASTEXITCODE -ne 0) {
            Fail "WebUI did not start."
        }
        return
    }
    Write-Info "The installed release does not support pawbot webui yet."
    Write-Info "Falling back to the setup wizard..."
}

Write-Info "Starting setup wizard..."
Invoke-Pawbot @("onboard", "--wizard")
if ($LASTEXITCODE -ne 0) {
    Fail "Setup wizard did not complete."
}

Write-Info "Done. Open the WebUI with: $(Get-PawbotCommand)"
Write-Info "For the terminal/TUI client, run: $(Get-PawbotCommand) agent"
