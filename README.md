# AnkEdo — AI-Powered Hate Speech Monitoring Agent

> An autonomous AI agent that continuously monitors Arabic and Kurdish social media for hate speech targeting minority communities in Iraq — built for human-in-the-loop verification, platform reporting, and long-term research.

---

## What It Does

AnkEdo is a locally-hosted, multi-agent Python system that:

- **Monitors** Facebook, TikTok, and Instagram 24/7 for hate speech in Arabic (MSA + Iraqi dialect) and Kurdish (Sorani + Kurmanji)
- **Classifies** content using a committee of specialized AI sub-agents (Triage → Linguistic Specialist → Critic)
- **Learns** from expert reviewer feedback — building a growing trope dictionary of coded hate speech specific to Iraq
- **Packages evidence** (screenshots, HTML snapshots, audit trail) for human-initiated platform reports
- **Communicates** with the admin via Telegram, WhatsApp, and a local web dashboard
- **Discovers autonomously** — follows leads, expands monitoring, and asks for help when needed

No content is ever auto-submitted to platforms. Every report requires explicit human initiation.

---

## Key Features

| Feature | Description |
|---------|-------------|
| 🔍 **Dual-mode monitoring** | Continuous watch list + incident cases triggered by events |
| 🤖 **Multi-agent classification** | Triage (fast/cheap) → Specialist (deep Arabic/Kurdish NLP) → Critic (anti-hallucination) |
| 👁️ **Human-in-the-loop** | Reviewer confirms/rejects every flagged item before any action |
| 📸 **Visual evidence** | Screenshots show post + author profile for platform reports |
| 🧠 **Self-improving** | Learning loop grows lexicons and trope dictionaries from reviewer decisions |
| 💬 **Chat interface** | Talk to the agent via Telegram, WhatsApp, or the web dashboard |
| 🔌 **MCP tool support** | Agent can use external MCP servers (web search, memory) during conversations |
| 🛡️ **Anti-detection** | Camoufox browser with human-like pacing, fingerprint management, residential proxies |

---

## Architecture Overview

```
Admin / Reviewer
      │
      ├── Web Dashboard (localhost:8000)
      ├── Telegram Bot
      └── WhatsApp Business API
            │
    ┌───────▼────────┐
    │  Orchestration  │  ← Main agent loop
    │     Agent       │
    └──┬──────────┬──┘
       │          │
  ┌────▼───┐  ┌──▼──────────────────────────┐
  │Collector│  │  Classification Committee   │
  │Workers  │  │  Triage → Specialist → Critic│
  │(Camoufox│  └─────────────────────────────┘
  │Browser) │          │
  └────┬────┘    ┌─────▼──────┐
       │         │   Review    │
  Social Media   │   Queue     │
  Platforms      └─────┬───────┘
                       │
               ┌───────▼───────┐
               │    SQLite DB   │
               │ Cases│Posts│   │
               │ Evidence│Audit │
               └───────────────┘
```

---

## Installation

> **One-command setup** — works on Windows and Linux.

**Windows (PowerShell):**
```powershell
iwr -useb https://raw.githubusercontent.com/amirjundi/ankedo/main/install.ps1 | iex
```

**Linux / macOS (Bash):**
```bash
curl -sSL https://raw.githubusercontent.com/amirjundi/ankedo/main/install.sh | bash
```

The installer will:
1. Detect and install Python 3.11+ if missing
2. Create an isolated virtual environment
3. Install all dependencies
4. Initialize the SQLite database
5. Run a first-run configuration wizard
6. Start the agent and open the dashboard at `http://localhost:8000`

> See [Installation Guide](docs/install.md) for manual setup, advanced options, and offline installation.

---

## Quick Start (after install)

```bash
# Add an incident case
python -m src.cli cases add \
  --group "Yazidi" \
  --seed "https://facebook.com/some_public_page" \
  --keywords "كلمة,مصطلح"

# Start the monitoring agent
python -m src.cli agent run --continuous

# Open the review dashboard
# → http://localhost:8000/review
```

---

## Requirements

- **OS**: Windows 10/11 or Ubuntu 20.04+ (dedicated PC recommended)
- **Python**: 3.11+ (installed automatically by the bootstrap script)
- **RAM**: 4 GB minimum, 8 GB recommended
- **Network**: Home WiFi or residential proxy (datacenter IPs not supported)
- **LLM API**: OpenAI, Anthropic, or compatible local model (Ollama)
- **Telegram Bot**: Required for admin notifications (optional but recommended)

---

## Project Structure

```
ankedo/
├── src/
│   ├── core/           # Orchestration loop, queue manager, settings
│   ├── browsers/       # Camoufox workers, anti-detect, fingerprinting
│   ├── platforms/      # Facebook, TikTok, Instagram adapters
│   ├── classifiers/    # Lexicons, trope engine
│   │   └── committee/  # Triage, Specialist, Critic agents
│   ├── chat/           # Telegram, WhatsApp, WebSocket gateway
│   ├── models/         # SQLAlchemy ORM models
│   ├── api/            # FastAPI local dashboard
│   ├── learning/       # Learning loop, gold eval gate
│   └── notifications/  # Agent-to-admin notification dispatch
├── install.ps1         # Windows bootstrap script
├── install.sh          # Linux/macOS bootstrap script
├── pyproject.toml      # Dependency manifest
├── .env.example        # Configuration template
└── README.md
```

---

## Guardrails

AnkEdo is designed with hard safety boundaries:

- ❌ **Never auto-submits** reports to any platform — human initiation only
- ❌ **Never deletes data** autonomously
- ❌ **Never exceeds** configured crawl-rate limits
- ✅ **All decisions logged** with full reasoning trace
- ✅ **Reviewer confirmation** required for every evidence package
- ✅ **Admin chat confirmation** required before any write action via Telegram/WhatsApp

---

## Status

> 🚧 **Pre-release — implementation in progress**

This repository contains the source code for the AnkEdo agent. Specifications, planning artifacts, and internal documentation are maintained separately and are not included in this public repository.

---

## License

[MIT License](LICENSE) — see LICENSE file for details.

---

## Contributing

This project is focused on monitoring hate speech against minority communities in Iraq. Contributions that improve classification accuracy for Arabic dialects and Kurdish are especially welcome.

Please open an issue before submitting a pull request for significant changes.
