# GridWise: LLM-Assisted Smart Campus Energy Optimizer
**BUP CSE FEST 2026 Hackathon — Online Preliminary Round**

A production-grade, mathematically optimal energy scheduling API service for smart campuses. The system parses natural-language operator directives using an LLM, sanitizes and validates constraints with deterministic guardrails, and solves 24-hour campus cost minimization using Linear Programming (LP).

---

## 1. Submission Endpoints & Registry References

- **Live Deployed API URL**: `https://gridwise-api.onrender.com`
- **Health Readiness Endpoint**: `GET https://gridwise-api.onrender.com/health`
- **Optimization Endpoint**: `POST https://gridwise-api.onrender.com/optimize-energy`
- **Docker Fallback Registry**: `ghcr.io/afnananikk/gridwise-hackathon-2026:latest`
- **Source Repository**: `https://github.com/AfnanAnikk/gridwise-hackathon-2026`

---

## 2. Architecture Overview

The system operates as a strict 4-stage pipeline:

```
[Request JSON: 24h demand, solar, tariff, battery specs, operator notes]
                                │
                                ▼
  ┌───────────────────────────────────────────────────────────┐
  │ 1. LLM Directive Interpreter (interpreter.py)             │
  │    - Parses 1-3 natural-language operator notes           │
  │    - Powered by Google Gemini Flash (OpenAI/Groq fallback)│
  │    - Caches model discovery at startup for <1.5s latency  │
  │    - Supplies battery capacity context for % reserves     │
  └─────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
  ┌───────────────────────────────────────────────────────────┐
  │ 2. Deterministic Guardrail Validator (guardrails.py)      │
  │    - Clamps hours to unique integers [0..23] ascending    │
  │    - Normalizes solar factor [0.0..1.0]                   │
  │    - Defensively scales fractional reserves (% of battery)│
  │    - Enforces applies=false & adjustment=null for no_op   │
  └─────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
  ┌───────────────────────────────────────────────────────────┐
  │ 3. Mathematical LP/MILP Optimizer (optimizer.py)          │
  │    - Mixed-Integer Linear Program formulated with PuLP    │
  │    - Solved via COIN-OR CBC in < 20 milliseconds          │
  │    - Enforces hourly balance: grid + solar + dis = dem+chg│
  │    - Enforces battery transitions, limits, and neutrality │
  │    - Secondary tie-breaker minimizes peak grid imports    │
  └─────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
[Response JSON: scenario_id, directive_interpretation, hourly_plan, totals]
```

---

## 3. Environment Variables & Model Configuration

| Environment Variable | Description | Default / Fallback |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key (Primary: Gemini Flash) | Required |
| `OPENAI_API_KEY` | OpenAI API key (`gpt-4o-mini`) | Secondary Fallback |
| `GROQ_API_KEY` | Groq API key (`llama-3.3-70b-versatile`) | Tertiary Fallback |

> **Secret Handling Notice**: Never commit API keys, tokens, `.env` files, or secrets to the repository or Docker images. The service cleanly reads keys from system environment variables at runtime and never exposes credentials, prompts, or stack traces in API responses or error logs.

---

## 4. Quickstart: Clean Local Setup

### Prerequisites
- Python 3.11+
- Git
- COIN-OR CBC solver (automatically handled via `apt-get install -y coinor-cbc` on Linux, `brew install cbc` on macOS, or PuLP default)

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

# 5. Start the service (binds to 0.0.0.0:8000)
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 5. API Endpoints & Verification

### Health Readiness Endpoint
```bash
curl -s http://localhost:8000/health
```
**Expected Response:**
```json
{
  "status": "ok"
}
```

### Energy Optimization Endpoint: Sample Request
```bash
curl -s -X POST http://localhost:8000/optimize-energy \
     -H "Content-Type: application/json" \
     -d @sample_request.json
```

**Expected Response Shape:**
```json
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [13, 14],
        "factor": 0.2
      },
      "explanation": "Solar output drops to 20% from 1 PM to 3 PM."
    },
    {
      "note_index": 1,
      "applies": true,
      "directive_type": "no_charge_window",
      "structured_adjustment": {
        "hours": [14, 15]
      },
      "explanation": "Do not charge the battery between 2 PM and 4 PM."
    },
    {
      "note_index": 2,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Cafeteria menu note is unrelated to energy scheduling."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 120.0,
      "solar_used_kwh": 0.0,
      "battery_action": "idle",
      "battery_kwh": 0.0,
      "battery_energy_after_kwh": 200.0
    }
    // ... 23 more hourly entries ...
  ],
  "total_grid_kwh": 2720.0,
  "total_cost_bdt": 38400.0,
  "peak_grid_kwh": 180.0,
  "plan_summary": "Successfully scheduled 24h campus load with 2 applied directive(s) (solar_reduction, no_charge_window). Minimized peak tariff imports and preserved battery neutrality."
}
```

---

## 6. Official Benchmark Pack Results

We validated the service against all **10 official public benchmark cases** (`SAMPLE-01` to `SAMPLE-10`) from the organizers:

| Case ID | Test Focus | Total Cost (BDT) | Total Grid (kWh) | Peak Grid (kWh) | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SAMPLE-01** | Solar cleaning + distractor | 38,365.0 | 2,692.5 | 175.0 | **100% Match** |
| **SAMPLE-02** | Battery charging maintenance | 42,885.0 | 2,915.0 | 180.0 | **100% Match** |
| **SAMPLE-03** | Emergency reserve percentage | 35,480.0 | 2,430.0 | 205.0 | **100% Match** |
| **SAMPLE-04** | No-discharge protection test | 40,495.0 | 2,645.0 | 225.0 | **100% Match** |
| **SAMPLE-05** | Temporary feeder grid cap | 33,950.0 | 2,430.0 | 175.0 | **100% Match** |
| **SAMPLE-06** | Multiple notes with distractor | 34,090.0 | 2,395.0 | 175.0 | **100% Match** |
| **SAMPLE-07** | Reserve plus transformer cap | 38,550.0 | 2,560.0 | 185.0 | **100% Match** |
| **SAMPLE-08** | Separate charge/discharge outages | 37,665.0 | 2,490.0 | 210.0 | **100% Match** |
| **SAMPLE-09** | Reduction wording normalization | 34,873.0 | 2,504.0 | 170.0 | **100% Match** |
| **SAMPLE-10** | Multi-constraint evening operation | 41,620.0 | 2,715.0 | 190.0 | **100% Match** |

### Running the Benchmark / Judge Simulator
To replay all 10 scenarios against the service:
```bash
# Against local instance:
python judge_simulator.py http://localhost:8000

# Against live deployment:
python judge_simulator.py https://gridwise-api.onrender.com
```

---

## 7. Docker Fallback Image Instructions

The service is fully containerized and published to GitHub Container Registry (GHCR):

- **Image Reference**: `ghcr.io/afnananikk/gridwise-hackathon-2026:latest`
- **Exposed Port**: `8000` (binds to `0.0.0.0`)
- **Required Environment Variable**: `GEMINI_API_KEY`

### Pull and Run
```bash
# 1. Pull the container image
docker pull ghcr.io/afnananikk/gridwise-hackathon-2026:latest

# 2. Run container
docker run -d -p 8000:8000 \
  -e GEMINI_API_KEY="your_api_key_here" \
  --name gridwise-app \
  ghcr.io/afnananikk/gridwise-hackathon-2026:latest

# 3. Test readiness
curl -s http://localhost:8000/health
```

### Build Locally (Optional)
```bash
docker build -t gridwise-service:latest .
docker run -d -p 8000:8000 -e GEMINI_API_KEY="your_api_key_here" gridwise-service:latest
```

---

## 8. Dependencies & Credited Tools

Under the official rulebook policy, all third-party libraries and frameworks used are credited below:

| Tool / Library | Version | Purpose |
| :--- | :--- | :--- |
| **FastAPI** | `^0.110.0` | Asynchronous high-performance HTTP web framework |
| **Uvicorn** | `^0.28.0` | Lightning-fast ASGI production server |
| **Pydantic** | `^2.6.0` | Schema definition, input parsing, and strict validation |
| **PuLP** | `^2.8.0` | High-level mathematical LP/MILP formulation library |
| **COIN-OR CBC** | System package | Industrial branch-and-cut MILP solver for provable optimality |
| **google-genai / REST** | `^0.1.1` | Google Gemini API integration for natural language note interpretation |
| **OpenAI / Groq** | `^1.14.0` | Secondary and tertiary fallback LLM integration |
| **Requests** | `^2.31.0` | HTTP client for REST calls and harness evaluation |

---

## 9. Known Limitations

- **Feasibility Guarantee**: The LP optimizer assumes the scenario has a feasible schedule under the valid operator directives, in accordance with Section 08 of the official challenge specification (*"Organizer valid scoring scenarios will have a feasible ground-truth interpretation and will not require contradictory hard directives"*).
- **Whole-Hour Discretization**: All energy quantities and time intervals are evaluated at 1-hour resolution across the 24 intervals of the day (`h = 0..23`).
