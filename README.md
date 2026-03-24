# S5-SHB Agent

**Society 5.0 enabled Multi-model Agentic Blockchain Framework for Smart Home**

S5-SHB Agent is a research framework that integrates adaptive blockchain consensus with multi-agent LLM orchestration for autonomous smart home governance. It features an adaptive Proof-of-Work mechanism with firmware-level emergency bypass, ten specialized AI agents coordinated through a multi-model LLM router, a four-stage conflict resolution cascade, and a four-tier human-centered governance model aligned with Society 5.0 principles.

## Key Features

- **Adaptive PoW Blockchain** — Four-phase difficulty adaptation (Idle, Normal, Emergency, Recovery) with firmware-level emergency bypass, Ed25519 digital signatures, and Merkle tree anchoring for tamper-evident auditability
- **Ten-Agent Multi-Model Orchestration** — Ten specialized agents organized into four priority tiers (Safety, Security, Efficiency, Comfort), routed across four LLM provider backends (Gemini, OpenAI, Anthropic, Ollama) with tier-constrained model assignment
- **Four-Stage Conflict Resolution** — Safety override, contextual LLM arbitration, ML-based historical scoring, and priority-based fallback policy for deterministic inter-agent dispute resolution
- **Society 5.0 Governance Model** — Four-tier resident-centric governance separating routine preferences, advanced overrides, and immutable safety thresholds via on-chain enforcement with natural-language interfaces
- **Multi-Mode Deployment** — Simulation, real-hardware, and hybrid deployment modes under a unified orchestration layer with Merkle anchoring for reproducibility

## Architecture

S5-SHB Agent follows a three-layer architecture:

1. **Control Plane** — FastAPI backend with REST/WebSocket APIs, Vue 3 frontend dashboard, and external interface management
2. **Agent Intelligence** — Ten specialized agents, multi-model LLM router with tier-constrained provider assignment, four-stage conflict resolution cascade, and anomaly detection ensemble
3. **Device & Data** — IoT device abstraction via Model Context Protocol (MCP), adaptive PoW blockchain with Ed25519/Merkle trust infrastructure, and SQLite off-chain persistence

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- At least one LLM API key (Gemini, OpenAI, or Anthropic)

### Backend Setup

```bash
# Install dependencies
pip install -e .

# Configure environment
cp engine/.env.example engine/.env
# Edit engine/.env to set your API keys

# Start the backend server
python -m web.main
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

The application will be available at `http://localhost:5174` (frontend) and `http://localhost:8001` (API).

## Project Structure

```
S5-SHB-Agent/
├── engine/                  # Core Python engine
│   ├── adapters/            # Device adapters (HTTP, MQTT, Mock)
│   ├── agent.py             # SmartHomeAgent — 7-role LLM agent
│   ├── anomaly_agent.py     # Anomaly detection (4-model ensemble)
│   ├── arbitration_agent.py # Four-stage conflict resolution
│   ├── blockchain.py        # Adaptive PoW blockchain
│   ├── config.py            # Central configuration
│   ├── devices.py           # HESDevice abstraction layer
│   ├── governance_contract.py # Four-tier preference validation
│   ├── mcp_server.py        # FastMCP server (9 tools)
│   ├── model_router.py      # Multi-provider LLM routing
│   ├── offchain.py          # SQLite off-chain persistence
│   ├── resident_preferences.py # Tier 1-4 governance
│   ├── scenarios.py         # 39 test scenarios
│   └── session_manager.py   # File-based session CRUD
├── web/                     # FastAPI + WebSocket layer
│   ├── api/                 # 15 REST sub-routers
│   ├── core/                # Session lifecycle, orchestrator, state
│   └── ws/                  # 5 WebSocket channels
├── frontend/                # Vue 3 + TypeScript SPA
│   └── src/
│       ├── views/           # 11 dashboard views
│       ├── components/      # Reusable UI components
│       ├── stores/          # Pinia state management
│       └── services/        # API client services
└── publish_test.py          # MQTT test utility
```

## Citation

If you use S5-SHB Agent in your research, please cite:

```bibtex
@misc{rangila2026s5shbagentsociety50,
      title={S5-SHB Agent: Society 5.0 enabled Multi-model Agentic Blockchain Framework for Smart Home}, 
      author={Janani Rangila and Akila Siriweera and Incheon Paik and Keitaro Naruse and Isuru Jayanada and Vishmika Devindi},
      year={2026},
      eprint={2603.05027},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2603.05027}, 
}
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

