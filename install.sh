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

# ── Find a terminal to read from ─────────────────────────────────────────
# Under `curl | bash` this script's stdin IS the pipe carrying the script, and it
# is already spent. Every prompt then reads EOF: `read` returns empty, and the
# wizard's Confirm.ask spins on "Please enter Y or N" until it aborts. Anything
# interactive has to talk to /dev/tty instead of stdin.
if [ -t 0 ]; then
    HAS_TTY=1; TTY_DEV="/dev/stdin"
elif [ -e /dev/tty ] && (exec </dev/tty) 2>/dev/null; then
    HAS_TTY=1; TTY_DEV="/dev/tty"
else
    # Piped with no controlling terminal (CI, docker build, nohup).
    HAS_TTY=0; TTY_DEV=""
fi

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
    if [ -d "$CLONE_DIR/.git" ]; then
        # Update in place. The old path asked "Overwrite? (y/N)" and rm -rf'd on yes —
        # which also destroys .env, data/ and logs/. Under `curl | bash` that prompt
        # read EOF and took the silent "no", so re-running the installer never picked
        # up new code at all. A pull does the intended thing and keeps local state.
        info "Existing installation found at $CLONE_DIR — updating"
        if git -C "$CLONE_DIR" pull --ff-only 2>&1 | sed 's/^/  /'; then
            ok "Updated to latest"
        else
            warn "Could not fast-forward (local commits or a dirty tree)"
            info "Resolve manually: cd $CLONE_DIR && git status"
        fi
        PROJECT_ROOT="$CLONE_DIR"
    elif [ -d "$CLONE_DIR" ]; then
        fail "$CLONE_DIR exists but is not a git checkout"
        info "Move it aside and re-run: mv $CLONE_DIR ${CLONE_DIR}.bak"
        exit 1
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

# Re-running the installer should not spend three minutes reinstalling what is
# already there. `pip check` verifies the installed set is complete and consistent,
# and importing the entry point proves this project itself is installed — together
# that is the question "do I need to do anything?".
NEEDS_DEPS=1
if python -c "import src.cli.__main__" 2>/dev/null && python -m pip check >/dev/null 2>&1; then
    NEEDS_DEPS=0
fi

if [ "$NEEDS_DEPS" = "0" ] && [ "${FORCE_DEPS:-}" != "1" ]; then
    ok "Dependencies already satisfied — skipping"
    info "Reinstall anyway with: FORCE_DEPS=1 ./install.sh"
else
    info "Installing AnkEdo and all dependencies (this may take 2-3 minutes)..."
    if python -m pip install -e . --quiet 2>&1; then
        ok "All dependencies installed"
    else
        fail "Dependency installation failed"
        info "Try manually: pip install -e ."
        exit 1
    fi
fi

# ── Step 4: Install Playwright Browsers ───────────────────────────────────
step "Checking browser engine (Playwright)..."
# `playwright install` is a no-op when the browser is present, but it still contacts
# the download registry. Ask the doctor first — it launches a browser, which is the
# only thing that actually answers whether one works.
if python -m src.cli doctor 2>/dev/null | grep -q "Browser launches"; then
    ok "Browser already installed"
elif python -m playwright install chromium 2>/dev/null; then
    ok "Chromium browser installed"
else
    # Playwright refuses to download for a distro its build registry does not know
    # yet — Ubuntu 26.04 hits this. Collection needs a browser, so say what to try
    # rather than leaving a bare "failed".
    warn "Browser install failed"
    info "Everything else is installed; collection needs a browser to run."
    info "Try, in order:"
    echo -e "  ${DIM}  pip install -U playwright && playwright install chromium${NC}"
    echo -e "  ${DIM}  sudo apt install chromium-browser   # then point Playwright at it${NC}"
    info "Classification and the dashboard work without it."
fi

# ── Step 5: Create Data Directories ──────────────────────────────────────
step "Creating data directories..."
for dir in data evidence logs screenshots; do
    mkdir -p "$PROJECT_ROOT/$dir"
done
ok "Data directories ready"

# ── Step 5b: Put `ankedo` on PATH ─────────────────────────────────────────
# pip installs the console script into .venv/bin, and this script's `activate`
# dies with the script — so a fresh shell has never heard of `ankedo`. Symlink it
# somewhere already on PATH, and have the link point at the venv's interpreter so
# it works without activation.
step "Linking the ankedo command..."

SHIM_DIR=""
for candidate in "$HOME/.local/bin" "/usr/local/bin"; do
    if [ -d "$candidate" ] && [ -w "$candidate" ]; then
        SHIM_DIR="$candidate"
        break
    fi
done
# ~/.local/bin is the standard user-level location and is on PATH by default on
# most distros; create it rather than falling back to sudo.
if [ -z "$SHIM_DIR" ]; then
    mkdir -p "$HOME/.local/bin" && SHIM_DIR="$HOME/.local/bin"
fi

if [ -n "$SHIM_DIR" ] && [ -x "$VENV_DIR/bin/ankedo" ]; then
    ln -sf "$VENV_DIR/bin/ankedo" "$SHIM_DIR/ankedo"
    ok "Linked $SHIM_DIR/ankedo"

    case ":$PATH:" in
        *":$SHIM_DIR:"*) ;;
        *)
            warn "$SHIM_DIR is not on your PATH"
            # Name the file rather than appending to it — editing a user's shell rc
            # behind their back is worse than one line of copy-paste.
            rc="$HOME/.bashrc"
            [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ] && rc="$HOME/.zshrc"
            echo ""
            echo -e "  ${YELLOW}Add this line to $rc, then reopen your terminal:${NC}"
            echo -e "  ${DIM}  export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            echo ""
            ;;
    esac
else
    warn "Could not link the ankedo command — use $VENV_DIR/bin/ankedo directly"
fi

# ── Step 6: Run Setup Wizard ──────────────────────────────────────────────
step "Launching configuration wizard..."
echo ""

# `|| true` throughout: set -e must not abandon the install half-done just because
# the operator quit the wizard — the code is installed either way, and `ankedo setup`
# can be re-run.
if [ "${NON_INTERACTIVE:-}" = "1" ] || [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    python -m src.cli setup --non-interactive || true
elif [ "$HAS_TTY" = "1" ]; then
    # Hand the wizard the terminal, not this script's spent stdin.
    python -m src.cli setup < "$TTY_DEV" || true
else
    warn "No terminal available — skipping the interactive wizard"
    echo ""
    info "Finish setup with either:"
    echo -e "  ${DIM}  ankedo setup${NC}                                  # interactive"
    echo -e "  ${DIM}  GEMINI_API_KEY=AIza... ankedo setup --non-interactive${NC}"
    echo -e "  ${DIM}  LLM_PROVIDER=openai OPENAI_API_KEY=sk-... ankedo setup --non-interactive${NC}"
    echo ""
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
echo -e "  ${DIM}  ankedo doctor    # Verify installation${NC}"
echo -e "  ${DIM}  ankedo start     # Launch agent + dashboard${NC}"
echo ""
echo -e "  ${CYAN}Other Commands:${NC}"
echo -e "  ${DIM}  ankedo setup             # Re-run configuration wizard${NC}"
echo -e "  ${DIM}  ankedo configure models  # Show model per agent role${NC}"
echo -e "  ${DIM}  ankedo configure set K=V # Change a setting from the shell${NC}"
echo -e "  ${DIM}  ankedo update            # Update from GitHub${NC}"
echo -e "  ${DIM}  ankedo --help            # See all commands${NC}"
echo ""
