# GridWise: LLM-Assisted Smart Campus Energy Optimizer
**BUP CSE FEST 2026 Hackathon — Online Preliminary Round**

A production-grade, mathematically optimal energy scheduling API service for smart campuses. The system parses natural-language operator directives using an LLM, sanitizes and validates constraints with deterministic guardrails, and solves 24-hour campus cost minimization using Linear Programming (LP).

---

## 1. Architecture Overview

The system operates as a strict 4-stage pipeline:

```
[Request JSON] 
     │
     ├──> [1. LLM Interpreter] ───> Extracts structured directives from human notes
     │                               (solar_reduction, minimum_battery_reserve, etc.)
     │                                    │
     │                                    ▼
     ├──> [2. Deterministic Guardrails] ──> Enforces strict schema, unique sorted hours,
     │                                      clamps factors [0..1] and non-negative bounds
     │                                    │
     │                                    ▼
     └──> [3. LP/MILP Optimizer] ─────────> Formulates PuLP model with energy balance,
                                            battery transitions & end-of-day neutrality
                                          │
                                          ▼
                                   [4. Response JSON] (Exact Canonical Schema)
```

- **LLM Role**: Mandatory extraction of `operator_notes` into machine-checkable structured directives (`directive_type`, `hours`, `structured_adjustment`). Uses Gemini Flash with OpenAI and Groq fallbacks.
- **Guardrails**: Deterministic normalization that prevents hallucinated parameters, ensures whole-hour convention compliance (start included, end excluded), and validates numeric limits.
- **Optimizer**: Solves 24-hour cost minimization to provable mathematical optimality using PuLP / CBC solver in `< 20ms`.
- **Battery Rules**: Enforces hourly rate limits, capacity bounds, dynamic directive reserves, and end-of-day neutrality ($E_{23} = E_{\text{init}}$).

---

## 2. Environment Variables & Model Configuration

| Environment Variable | Description | Default / Fallback |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key (Primary: Gemini Flash) | Required |
| `OPENAI_API_KEY` | OpenAI API key (`gpt-4o-mini`) | Secondary Fallback |
| `GROQ_API_KEY` | Groq API key (`llama-3.3-70b-versatile`) | Tertiary Fallback |

> **Secret Handling Notice**: Never bake secrets or API keys into Docker images or git repositories. The service cleanly reads keys from system environment variables at runtime.

---

## 3. Quickstart: Clean Local Setup

### Prerequisites
- Python 3.11+
- Git

### Installation & Execution
```bash
# 1. Clone repository
git clone https://github.com/AfnanAnikk/gridwise-hackathon-2026.git
cd gridwise-hackathon-2026

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your LLM API key
export GEMINI_API_KEY="your_api_key_here"

# 5. Start the service
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 4. API Endpoints & Verification

### Health Readiness Endpoint
```bash
curl -s http://localhost:8000/health
```
**Expected Response:**
```json
{"status": "ok"}
```

### Energy Optimization Endpoint
```bash
curl -s -X POST http://localhost:8000/optimize-energy \
     -H "Content-Type: application/json" \
     -d @sample_request.json
```

---

## 5. Running the Official Public Benchmark & Judge Harness

```bash
# Run against local server:
python judge_simulator.py http://localhost:8000

# Run against deployed live endpoint:
python judge_simulator.py https://gridwise-api.onrender.com
```

The harness runs all 10 official benchmark cases from `official_sample_cases.json` and evaluates:
1. Health readiness (`GET /health`)
2. Directive interpretation correctness across all 5 directive types + `no_op` distractors
3. Physical energy constraints (energy balance, rate limits, capacity bounds, battery neutrality)
4. Cost optimization ratio against organizer reference schedules
5. P95 latency and reliability

---

## 6. Docker Fallback Image Instructions

The service is fully containerized and published to GitHub Container Registry (GHCR):

- **Image Registry**: `ghcr.io/afnananikk/gridwise-hackathon-2026:latest`
- **Exposed Port**: `8000` (binds to `0.0.0.0`)
- **Required Environment Variable**: `GEMINI_API_KEY`

### Pull and Run Command
```bash
# Pull the fallback container image
docker pull ghcr.io/afnananikk/gridwise-hackathon-2026:latest

# Run the container
docker run -d -p 8000:8000 \
  -e GEMINI_API_KEY="your_api_key_here" \
  --name gridwise-app \
  ghcr.io/afnananikk/gridwise-hackathon-2026:latest

# Test health check
curl -s http://localhost:8000/health
```

### Build Locally (Optional)
```bash
docker build -t gridwise-service:latest .
docker run -d -p 8000:8000 -e GEMINI_API_KEY="your_api_key_here" gridwise-service:latest
```

---

## 7. Dependencies & Technical Specifications

- **Web Framework**: FastAPI 0.110+, Uvicorn
- **Validation**: Pydantic v2
- **Mathematical Solver**: PuLP 2.8+ (COIN-OR CBC MILP solver)
- **LLM Clients**: `google-genai` / Google Generative Language REST API (Gemini Flash), `openai` (OpenAI / Groq)
- **Known Limitations**: Scenarios must have feasible ground-truth constraints as guaranteed by the official challenge specification.
