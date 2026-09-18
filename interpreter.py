import os
import json
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from schemas import BatteryInput

logger = logging.getLogger("gridwise-interpreter")

PROMPT_SYSTEM = """You are an expert energy grid operator and parser for the GridWise Smart Campus Energy System.
Your task is to interpret 1-3 natural-language operator notes and convert each note into a structured energy directive.

There are exactly 5 supported directive types + no_op:
1. "solar_reduction": Usable solar generation drops during specific hours.
   structured_adjustment: {"hours": [integer list], "factor": float between 0.0 and 1.0}
   NOTE: "factor" is the USABLE FRACTION REMAINING!
   Example: "Solar output drops to 20%" -> factor = 0.2
   Example: "roughly 25% of the forecast" -> factor = 0.25
   Example: "about half of the forecast" -> factor = 0.5
   Example: "80% reduction in rooftop solar" -> 1.0 - 0.8 = factor = 0.2
   Example: "leave roughly one-fifth of normal output" -> factor = 0.2

2. "minimum_battery_reserve": Battery energy must stay at or above a required kWh level.
   structured_adjustment: {"hours": [integer list], "minimum_energy_kwh": float}
   NOTE: If reserve is given as a percentage of battery capacity (e.g., "Keep at least 50% of the battery capacity"), compute the exact kWh using capacity_kwh from the Battery context (e.g. 50% of 200 kWh = 100.0 kWh).
   Example: "Keep at least 120 kWh in reserve from 6 PM until 9 PM" -> hours: [18, 19, 20], minimum_energy_kwh: 120.0
   Example: "Keep at least 50% of the battery capacity stored ... from 6 PM until 9 PM" (capacity 200 kWh) -> hours: [18, 19, 20], minimum_energy_kwh: 100.0
   Example: "Keep at least 90 kWh in the battery from 6 PM until 10 PM" -> hours: [18, 19, 20, 21], minimum_energy_kwh: 90.0

3. "no_charge_window": Battery charging is prohibited during specific hours.
   structured_adjustment: {"hours": [integer list]}
   Example: "Do not charge the battery between 2 PM and 4 PM" -> hours: [14, 15]
   Example: "The battery charger will be isolated from 2 AM until 5 AM" -> hours: [2, 3, 4]
   Example: "Battery charging is disabled from 11 AM until 1 PM" -> hours: [11, 12]

4. "no_discharge_window": Battery discharging is prohibited during specific hours.
   structured_adjustment: {"hours": [integer list]}
   Example: "Avoid discharging the battery from 10 AM to 12 PM" -> hours: [10, 11]
   Example: "must not discharge from 6 PM until 8 PM" -> hours: [18, 19]
   Example: "Do not discharge the battery from 5 PM until 7 PM" -> hours: [17, 18]

5. "max_grid_window": Grid power import may not exceed a stated kWh amount during specific hours.
   structured_adjustment: {"hours": [integer list], "max_grid_kwh": float}
   Example: "Limit grid purchase to 80 kWh between 5 PM and 8 PM" -> hours: [17, 18, 19], max_grid_kwh: 80.0
   Example: "grid import must not exceed 155 kWh in any hour ... from 6 PM until 9 PM" -> hours: [18, 19, 20], max_grid_kwh: 155.0
   Example: "The evening transformer limit is 180 kWh of grid import from 7 PM until 9 PM" -> hours: [19, 20], max_grid_kwh: 180.0
   Example: "Grid intake must stay at or below 190 kWh from 7 PM until 10 PM" -> hours: [19, 20, 21], max_grid_kwh: 190.0

6. "no_op": Note is irrelevant to today's 24-hour campus energy schedule (e.g., cafeteria menus, sports registration, library hours, club notices, seminar room bookings, future weather next week, routine meetings).
   For no_op:
   applies = false
   directive_type = "no_op"
   structured_adjustment = null

TIME CONVENTION (CRUCIAL):
- Whole-hour intervals: start hour is INCLUDED, end hour is EXCLUDED.
  - "noon until 2 PM" (12:00 to 14:00) -> hours: [12, 13]
  - "1 PM to 3 PM" (13:00 to 15:00) -> hours: [13, 14]
  - "2 PM and 4 PM" / "2 PM until 4 PM" (14:00 to 16:00) -> hours: [14, 15]
  - "5 PM until 7 PM" (17:00 to 19:00) -> hours: [17, 18]
  - "6 PM until 8 PM" (18:00 to 20:00) -> hours: [18, 19]
  - "6 PM until 9 PM" (18:00 to 21:00) -> hours: [18, 19, 20]
  - "6 PM until 10 PM" (18:00 to 22:00) -> hours: [18, 19, 20, 21]
  - "7 PM until 9 PM" (19:00 to 21:00) -> hours: [19, 20]
  - "7 PM until 10 PM" (19:00 to 22:00) -> hours: [19, 20, 21]
  - "2 AM until 5 AM" (02:00 to 05:00) -> hours: [2, 3, 4]
  - "10 AM until noon" (10:00 to 12:00) -> hours: [10, 11]
  - "11 AM until 1 PM" (11:00 to 13:00) -> hours: [11, 12]
  - "11 AM and 2 PM" / "11 AM until 2 PM" (11:00 to 14:00) -> hours: [11, 12, 13]
  - "from 0 to 4" (00:00 to 04:00) -> hours: [0, 1, 2, 3]
- "hours" must be unique integers strictly ascending, between 0 and 23.
- Every note in operator_notes must have exactly one entry in directive_interpretation with its note_index (0..N-1).

Output format: Return ONLY a valid JSON array of objects:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
    "explanation": "Solar output reduced to 20% from 13:00 to 15:00"
  },
  ...
]
"""

def extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    try:
        data = json.loads(text.strip())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
    except Exception:
        pass

    match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None

_CACHED_ACTIVE_MODELS: Optional[List[str]] = None

def format_prompt_content(notes: List[str], battery: Optional[BatteryInput] = None) -> str:
    ctx = ""
    if battery is not None:
        ctx = f"\nBattery context:\n- capacity_kwh: {battery.capacity_kwh}\n- initial_energy_kwh: {battery.initial_energy_kwh}\n- minimum_energy_kwh: {battery.minimum_energy_kwh}\n"
    return f"{PROMPT_SYSTEM}{ctx}\n\nOperator notes to interpret:\n{json.dumps(notes, indent=2)}"

def call_gemini_api(prompt_text: str, api_key: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    global _CACHED_ACTIVE_MODELS
    import requests
    last_err = None

    # Step 1: Discover models once and cache
    if not _CACHED_ACTIVE_MODELS:
        active = []
        try:
            list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            list_res = requests.get(list_url, timeout=6)
            if list_res.status_code == 200:
                for m_obj in list_res.json().get("models", []):
                    methods = m_obj.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        m_name = m_obj.get("name", "").replace("models/", "")
                        if "flash" in m_name.lower():
                            active.insert(0, m_name)
                        else:
                            active.append(m_name)
        except Exception as e_list:
            logger.warning(f"ListModels failed: {e_list}")

        if not active:
            active = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"]
        _CACHED_ACTIVE_MODELS = active

    # Step 2: Try models in priority order
    for idx, m in enumerate(_CACHED_ACTIVE_MODELS):
        # A. Try REST endpoint
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    text = candidates[0]["content"]["parts"][0]["text"]
                    extracted = extract_json_array(text)
                    if extracted:
                        if idx > 0:
                            _CACHED_ACTIVE_MODELS.insert(0, _CACHED_ACTIVE_MODELS.pop(idx))
                        return extracted, None
            else:
                last_err = f"{m} HTTP {resp.status_code}: {resp.text}"
        except Exception as e_rest:
            last_err = f"{m} exception: {str(e_rest)}"

        # B. Try SDK
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=m,
                contents=prompt_text,
                config={"response_mime_type": "application/json"}
            )
            if response and response.text:
                extracted = extract_json_array(response.text)
                if extracted:
                    if idx > 0:
                        _CACHED_ACTIVE_MODELS.insert(0, _CACHED_ACTIVE_MODELS.pop(idx))
                    return extracted, None
        except Exception as e_sdk:
            last_err = f"{m} SDK: {str(e_sdk)}"

    return None, f"All models failed. Last error: {last_err}. Checked: {_CACHED_ACTIVE_MODELS[:3]}"

def interpret_operator_notes(
    notes: List[str],
    battery: Optional[BatteryInput] = None
) -> List[Dict[str, Any]]:
    """
    Mandatory LLM interpretation path.
    Invokes Google Gemini generative language model
    to convert unstructured operator notes into structured directives.
    """
    prompt_text = format_prompt_content(notes, battery)

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise RuntimeError("LLM interpretation error: GEMINI_API_KEY not found in env")

    gemini_key = gemini_key.strip().strip("'").strip('"')
    res, err = call_gemini_api(prompt_text, gemini_key)
    if res is not None:
        return res

    raise RuntimeError(f"LLM interpretation error: Gemini failed ({err})")
