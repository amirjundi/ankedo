<# 
.SYNOPSIS
    AnkEdo — One-Command Windows Installer
.DESCRIPTION
    Downloads, installs, and configures the AnkEdo hate speech monitoring agent.
    Run this script from PowerShell:
    
      irm https://raw.githubusercontent.com/amirjundi/ankedo/master/install.ps1 | iex
    
    Or if you already cloned the repo:
    
      .\install.ps1
#>

param (
    [switch]$NonInteractive,
    [switch]$SkipPython,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ── Colors & Helpers ──────────────────────────────────────────────────────

function Write-Step($msg) { Write-Host "`n► $msg" -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }

# ── Banner ────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║  🔺 AnkEdo — Hate Speech Monitor         ║" -ForegroundColor Cyan
Write-Host "  ║  One-Command Installer for Windows        ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Detect project root ──────────────────────────────────────────────────

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) {
    $ProjectRoot = Get-Location
}

# If we're running from a pipe (irm | iex), clone the repo first
if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
    Write-Step "Cloning AnkEdo repository..."
    
    $CloneDir = Join-Path $HOME "AnkEdo"
    if (Test-Path $CloneDir) {
        Write-Info "Existing installation found at $CloneDir"
        $choice = Read-Host "  Overwrite? (y/N)"
        if ($choice -ne "y" -and $choice -ne "Y") {
            Write-Host "  Using existing directory." -ForegroundColor DarkGray
            $ProjectRoot = $CloneDir
        } else {
            Remove-Item -Recurse -Force $CloneDir
            git clone https://github.com/amirjundi/ankedo.git $CloneDir 2>&1 | Out-Null
            $ProjectRoot = $CloneDir
        }
    } else {
        git clone https://github.com/amirjundi/ankedo.git $CloneDir 2>&1 | Out-Null
        $ProjectRoot = $CloneDir
    }
    
    if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
        Write-Fail "Clone failed. Check your internet connection and try again."
        Write-Info "Manual: git clone https://github.com/amirjundi/ankedo.git"
        exit 1
    }
    Write-OK "Repository cloned to $ProjectRoot"
}

Set-Location $ProjectRoot

# ── Step 1: Check Python ─────────────────────────────────────────────────

Write-Step "Checking Python installation..."

$PythonCmd = $null
foreach ($cmd in @("python3", "python", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonCmd = $cmd
                Write-OK "Found $ver"
                break
            } elseif ($major -ge 3 -and $minor -ge 10) {
                $PythonCmd = $cmd
                Write-Warn "Found $ver (3.11+ recommended, but 3.10 may work)"
                break
            }
        }
    } catch { }
}

if (-not $PythonCmd -and -not $SkipPython) {
    Write-Warn "Python 3.11+ not found."
    
    # Try winget
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        Write-Info "Installing Python via winget..."
        winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        
        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
        
        foreach ($cmd in @("python3", "python", "py")) {
            try {
                $ver = & $cmd --version 2>&1
                if ($ver -match "Python 3") { $PythonCmd = $cmd; break }
            } catch { }
        }
        
        if ($PythonCmd) {
            Write-OK "Python installed successfully"
        } else {
            Write-Warn "Python installed but not in PATH. Restart your terminal and try again."
        }
    } else {
        Write-Fail "Cannot auto-install Python (winget not available)."
        Write-Host ""
        Write-Host "  Please install Python 3.11+ manually:" -ForegroundColor Yellow
        Write-Host "  1. Download from https://www.python.org/downloads/" -ForegroundColor DarkGray
        Write-Host '  2. IMPORTANT: Check "Add Python to PATH" during install' -ForegroundColor DarkGray
        Write-Host "  3. Restart PowerShell and run this installer again" -ForegroundColor DarkGray
        Write-Host ""
        exit 1
    }
}

if (-not $PythonCmd) {
    Write-Fail "Python 3.10+ is required. Install it and try again."
    exit 1
}

# ── Step 2: Create Virtual Environment ────────────────────────────────────

Write-Step "Setting up virtual environment..."

$VenvDir = Join-Path $ProjectRoot ".venv"
if (Test-Path $VenvDir) {
    Write-OK "Virtual environment already exists"
} else {
    & $PythonCmd -m venv $VenvDir
    Write-OK "Created virtual environment at .venv"
}

# Activate venv
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    . $ActivateScript
    Write-OK "Activated virtual environment"
} else {
    Write-Fail "Could not activate virtual environment"
    Write-Info "Try manually: .venv\Scripts\Activate.ps1"
    exit 1
}

# ── Step 3: Install Dependencies ──────────────────────────────────────────

Write-Step "Installing dependencies..."

# Upgrade pip first
Write-Info "Upgrading pip..."
& python -m pip install --upgrade pip --quiet 2>&1 | Out-Null

# Install project + all dependencies
Write-Info "Installing AnkEdo and all dependencies (this may take 2-3 minutes)..."
& python -m pip install -e . --quiet 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "All dependencies installed"
} else {
    Write-Fail "Dependency installation failed"
    Write-Info "Try manually: pip install -e ."
    Write-Info "If you see build errors, make sure you have Visual C++ Build Tools:"
    Write-Info "  https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    exit 1
}

# ── Step 4: Install Playwright Browsers ───────────────────────────────────

if (-not $SkipBrowser) {
    Write-Step "Installing browser engine (Playwright)..."
    try {
        & python -m playwright install chromium 2>&1 | Out-Null
        Write-OK "Chromium browser installed"
    } catch {
        Write-Warn "Browser install failed (you can do this later: playwright install chromium)"
    }
}

# ── Step 5: Create Data Directories ──────────────────────────────────────

Write-Step "Creating data directories..."
foreach ($dir in @("data", "evidence", "logs", "screenshots")) {
    $path = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}
Write-OK "Data directories ready"

# ── Step 6: Run Setup Wizard ──────────────────────────────────────────────

Write-Step "Launching configuration wizard..."
Write-Host ""

if ($NonInteractive) {
    & python -m src.cli setup --non-interactive
} else {
    & python -m src.cli setup
}

# ── Step 7: Install Frontend Dependencies ─────────────────────────────────

$FrontendDir = Join-Path $ProjectRoot "frontend"
if (Test-Path (Join-Path $FrontendDir "package.json")) {
    Write-Step "Installing frontend dependencies..."
    $hasNode = Get-Command node -ErrorAction SilentlyContinue
    if ($hasNode) {
        Set-Location $FrontendDir
        & npm install --silent 2>&1 | Out-Null
        Set-Location $ProjectRoot
        Write-OK "Frontend dependencies installed"
    } else {
        Write-Warn "Node.js not found — frontend dev server won't work"
        Write-Info "Install Node.js from https://nodejs.org if you want to modify the frontend"
    }
}

# ── Done! ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║  ✓ AnkEdo installed successfully!         ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick Start:" -ForegroundColor Cyan
Write-Host "    cd $ProjectRoot" -ForegroundColor DarkGray
Write-Host "    .venv\Scripts\Activate.ps1" -ForegroundColor DarkGray
Write-Host "    ankedo doctor    # Verify installation" -ForegroundColor DarkGray
Write-Host "    ankedo start     # Launch agent + dashboard" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Other Commands:" -ForegroundColor Cyan
Write-Host "    ankedo setup     # Re-run configuration wizard" -ForegroundColor DarkGray
Write-Host "    ankedo update    # Update from GitHub" -ForegroundColor DarkGray
Write-Host "    ankedo --help    # See all commands" -ForegroundColor DarkGray
Write-Host ""
