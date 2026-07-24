#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# AnkEdo — One-Command Linux/Mac Installer
# ═══════════════════════════════════════════════════════════════════════════
#
# Usage (from GitHub):
#   curl -fsSL https://raw.githubusercontent.com/amirjundi/ankedo/master/install.sh | bash
#
# Or if you already cloned the repo:
#   chmod +x install.sh && ./install.sh
# ═══════════════════════════════════════════════════════════════════════════

set -e

# ── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[0;90m'
BOLD='\033[1m'
NC='\033[0m' # No Color

step()  { echo -e "\n${CYAN}► $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠ $1${NC}"; }
fail()  { echo -e "  ${RED}✗ $1${NC}"; }
info()  { echo -e "  ${DIM}$1${NC}"; }

# ── Banner ────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║  🔺 AnkEdo — Hate Speech Monitor         ║${NC}"
echo -e "${CYAN}  ║  One-Command Installer (Linux/Mac)        ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Detect project root ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
    step "Cloning AnkEdo repository..."
    
    CLONE_DIR="$HOME/AnkEdo"
    if [ -d "$CLONE_DIR" ]; then
        info "Existing installation found at $CLONE_DIR"
        read -p "  Overwrite? (y/N) " choice
        if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
            rm -rf "$CLONE_DIR"
            git clone https://github.com/amirjundi/ankedo.git "$CLONE_DIR"
        fi
        PROJECT_ROOT="$CLONE_DIR"
    else
        git clone https://github.com/amirjundi/ankedo.git "$CLONE_DIR"
        PROJECT_ROOT="$CLONE_DIR"
    fi
    
    if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
        fail "Clone failed. Check your internet connection."
        info "Manual: git clone https://github.com/amirjundi/ankedo.git"
        exit 1
    fi
    ok "Repository cloned to $PROJECT_ROOT"
fi

cd "$PROJECT_ROOT"

# ── Step 1: Check Python ─────────────────────────────────────────────────
step "Checking Python installation..."

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            ok "Found Python $ver"
            break
        elif [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_CMD="$cmd"
            warn "Found Python $ver (3.11+ recommended)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    warn "Python 3.11+ not found. Attempting to install..."
    
    if command -v apt-get &>/dev/null; then
        info "Installing via apt (may require sudo)..."
        sudo apt-get update -qq
        sudo apt-get install -y python3.12 python3.12-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y python3.11 python3.11-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y python3 python3-venv python3-pip
        PYTHON_CMD="python3"
    elif command -v brew &>/dev/null; then
        info "Installing via Homebrew..."
        brew install python@3.12 2>/dev/null || brew install python@3.11
        PYTHON_CMD="python3"
    elif command -v dnf &>/dev/null; then
        info "Installing via dnf..."
        sudo dnf install -y python3.12 python3-pip 2>/dev/null || \
        sudo dnf install -y python3.11 python3-pip
        PYTHON_CMD="python3"
    else
        fail "Cannot auto-install Python."
        echo ""
        echo -e "  ${YELLOW}Please install Python 3.11+ manually:${NC}"
        echo -e "  ${DIM}  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv${NC}"
        echo -e "  ${DIM}  macOS:         brew install python@3.12${NC}"
        echo -e "  ${DIM}  Fedora:        sudo dnf install python3.12${NC}"
        echo -e "  ${DIM}  Other:         https://www.python.org/downloads/${NC}"
        echo ""
        exit 1
    fi
    
    if [ -n "$PYTHON_CMD" ]; then
        ok "Python installed"
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    fail "Python 3.10+ is required. Install it and try again."
    exit 1
fi

# ── Step 2: Create Virtual Environment ────────────────────────────────────
step "Setting up virtual environment..."

VENV_DIR="$PROJECT_ROOT/.venv"
if [ -d "$VENV_DIR" ]; then
    ok "Virtual environment already exists"
else
    $PYTHON_CMD -m venv "$VENV_DIR"
    ok "Created virtual environment at .venv"
fi

# Activate venv
source "$VENV_DIR/bin/activate"
ok "Activated virtual environment"

# ── Step 3: Install Dependencies ──────────────────────────────────────────
step "Installing dependencies..."

info "Upgrading pip..."
python -m pip install --upgrade pip --quiet 2>&1

info "Installing AnkEdo and all dependencies (this may take 2-3 minutes)..."
python -m pip install -e . --quiet 2>&1
if [ $? -eq 0 ]; then
    ok "All dependencies installed"
else
    fail "Dependency installation failed"
    info "Try manually: pip install -e ."
    exit 1
fi

# ── Step 4: Install Playwright Browsers ───────────────────────────────────
step "Installing browser engine (Playwright)..."
python -m playwright install chromium 2>/dev/null && ok "Chromium browser installed" || \
    warn "Browser install failed (you can do this later: playwright install chromium)"

# ── Step 5: Create Data Directories ──────────────────────────────────────
step "Creating data directories..."
for dir in data evidence logs screenshots; do
    mkdir -p "$PROJECT_ROOT/$dir"
done
ok "Data directories ready"

# ── Step 6: Run Setup Wizard ──────────────────────────────────────────────
step "Launching configuration wizard..."
echo ""

if [ "${NON_INTERACTIVE:-}" = "1" ]; then
    python -m src.cli setup --non-interactive
else
    python -m src.cli setup
fi

# ── Step 7: Install Frontend Dependencies ─────────────────────────────────
if [ -f "$PROJECT_ROOT/frontend/package.json" ]; then
    step "Installing frontend dependencies..."
    if command -v node &>/dev/null; then
        cd "$PROJECT_ROOT/frontend"
        npm install --silent 2>/dev/null
        cd "$PROJECT_ROOT"
        ok "Frontend dependencies installed"
    else
        warn "Node.js not found — frontend dev server won't work"
        info "Install Node.js from https://nodejs.org if you want to modify the frontend"
    fi
fi

# ── Done! ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║  ✓ AnkEdo installed successfully!         ║${NC}"
echo -e "${GREEN}  ╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Quick Start:${NC}"
echo -e "  ${DIM}  cd $PROJECT_ROOT${NC}"
echo -e "  ${DIM}  source .venv/bin/activate${NC}"
echo -e "  ${DIM}  ankedo doctor    # Verify installation${NC}"
echo -e "  ${DIM}  ankedo start     # Launch agent + dashboard${NC}"
echo ""
echo -e "  ${CYAN}Other Commands:${NC}"
echo -e "  ${DIM}  ankedo setup     # Re-run configuration wizard${NC}"
echo -e "  ${DIM}  ankedo update    # Update from GitHub${NC}"
echo -e "  ${DIM}  ankedo --help    # See all commands${NC}"
echo ""
