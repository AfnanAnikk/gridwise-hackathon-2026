# GridWise: LLM-Assisted Smart Campus Energy Optimizer
**BUP CSE FEST 2026 Hackathon — Preliminary Round**

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

- **LLM Role**: Mandatory extraction of `operator_notes` into machine-checkable structured directives (`directive_type`, `hours`, `structured_adjustment`).
- **Guardrails**: Deterministic normalization that prevents hallucinated parameters, ensures whole-hour convention compliance (start included, end excluded), and validates numeric limits.
- **Optimizer**: Solves 24-hour cost minimization to provable mathematical optimality using PuLP / CBC solver in `< 20ms`.
- **Battery Rules**: Enforces hourly rate limits, capacity bounds, dynamic directive reserves, and end-of-day neutrality ($E_{23} = E_{\text{init}}$).

---

## 2. Environment Variables & Model Configuration

The service supports zero-configuration local runs, as well as production LLM providers:

| Environment Variable | Description | Default / Fallback |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key (uses `gemini-2.5-flash` / `gemini-1.5-flash`) | Optional (Recommended) |
| `OPENAI_API_KEY` | OpenAI API key (uses `gpt-4o-mini`) | Optional |
| `GROQ_API_KEY` | Groq API key (uses `llama-3.3-70b-versatile`) | Optional |

> **Secret Handling Notice**: Never bake secrets or API keys into Docker images or git repositories. The service cleanly reads keys from system environment variables at runtime. If no external key is present, the built-in deterministic heuristic engine operates offline to ensure zero crashes and reliable execution.

---

## 3. Quickstart: Clean Local Setup

### Prerequisites
- Python 3.11+
- Git

### Installation & Execution
```bash
# 1. Clone repository
git clone <YOUR_REPO_URL>
cd gridwise

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Set your preferred LLM API key
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

## 5. Running the Test Suite

```bash
pytest test_service.py -v
```
The test suite automatically verifies:
1. Health endpoint readiness (`GET /health`).
2. Exact Pydantic schema validation for `POST /optimize-energy`.
3. Strict hourly energy balance ($\text{grid} + \text{solar\_used} + \text{discharge} = \text{demand} + \text{charge}$).
4. Battery state transition consistency and rate limits.
5. End-of-day battery neutrality ($E_{23} == E_{\text{init}}$).
6. Paraphrase robustness for operator notes and distractor filtering (`no_op`).
7. Malformed request error handling (HTTP 400).

---

## 6. Docker Fallback Instructions

The service is fully containerized and can be pulled and run anywhere:

### Build & Run Locally
```bash
# Build docker image
docker build -t gridwise-service:latest .

# Run container (exposes port 8000, binds to 0.0.0.0)
docker run -d -p 8000:8000 \
  -e GEMINI_API_KEY="your_optional_api_key" \
  --name gridwise-app gridwise-service:latest

# Verify health inside container
curl -s http://localhost:8000/health
```

### Docker Hub / Registry Pull Command
```bash
docker pull <DOCKER_USERNAME>/gridwise-service:latest
docker run -d -p 8000:8000 <DOCKER_USERNAME>/gridwise-service:latest
```

---

## 7. Dependencies & Technical Specifications

- **Web Framework**: FastAPI 0.110+, Uvicorn
- **Validation**: Pydantic v2
- **Mathematical Solver**: PuLP 2.8+ (with COIN-OR CBC solver) & SciPy
- **LLM Clients**: `google-genai` (Gemini Flash), `openai` (OpenAI / Groq)
- **Known Limitations**: Scenarios must have feasible ground-truth constraints as guaranteed by the challenge specification.
