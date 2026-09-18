import os
import json
import re
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("gridwise-interpreter")

PROMPT_SYSTEM = """You are an expert energy grid operator and parser for the GridWise Smart Campus Energy System.
Your task is to interpret 1-3 natural-language operator notes and convert each note into a structured energy directive.

There are exactly 5 supported directive types + no_op:
1. "solar_reduction": Usable solar generation drops during specific hours.
   structured_adjustment: {"hours": [integer list], "factor": float between 0.0 and 1.0}
   NOTE: "factor" is the USABLE FRACTION REMAINING!
   Example: "Solar output drops to 20%" -> factor = 0.2
   Example: "80% reduction in rooftop solar" -> 1.0 - 0.8 = factor = 0.2
   Example: "leave roughly one-fifth of normal output" -> factor = 0.2

2. "minimum_battery_reserve": Battery energy must stay at or above a required kWh level.
   structured_adjustment: {"hours": [integer list], "minimum_energy_kwh": float}
   Example: "Keep at least 120 kWh in reserve from 6 PM until 9 PM" -> hours: [18, 19, 20], minimum_energy_kwh: 120.0

3. "no_charge_window": Battery charging is prohibited during specific hours.
   structured_adjustment: {"hours": [integer list]}
   Example: "Do not charge the battery between 2 PM and 4 PM" -> hours: [14, 15]

4. "no_discharge_window": Battery discharging is prohibited during specific hours.
   structured_adjustment: {"hours": [integer list]}
   Example: "Avoid discharging the battery from 10 AM to 12 PM" -> hours: [10, 11]

5. "max_grid_window": Grid power import may not exceed a stated kWh amount during specific hours.
   structured_adjustment: {"hours": [integer list], "max_grid_kwh": float}
   Example: "Limit grid purchase to 80 kWh between 5 PM and 8 PM" -> hours: [17, 18, 19], max_grid_kwh: 80.0

6. "no_op": Note is irrelevant to today's 24-hour campus energy schedule (e.g., cafeteria menus, future weather next week, routine meetings).
   For no_op:
   applies = false
   directive_type = "no_op"
   structured_adjustment = null

TIME CONVENTION (CRUCIAL):
- Whole-hour intervals: start hour is INCLUDED, end hour is EXCLUDED.
  - "1 PM to 3 PM" (13:00 to 15:00) -> hours: [13, 14]
  - "2 PM and 4 PM" (14:00 to 16:00) -> hours: [14, 15]
  - "6 PM until 9 PM" (18:00 to 21:00) -> hours: [18, 19, 20]
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

def call_gemini_api(notes: List[str], api_key: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    import requests
    last_err = None

    # Step 1: Dynamically ask Google which models are active for this key
    active_models = []
    try:
        list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        list_res = requests.get(list_url, timeout=8)
        if list_res.status_code == 200:
            for m_obj in list_res.json().get("models", []):
                methods = m_obj.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    m_name = m_obj.get("name", "").replace("models/", "")
                    if "flash" in m_name.lower():
                        active_models.insert(0, m_name)
                    else:
                        active_models.append(m_name)
    except Exception as e_list:
        logger.warning(f"ListModels failed: {e_list}")

    if not active_models:
        active_models = ["gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-pro"]

    # Step 2: Try models in priority order
    for m in active_models:
        # A. Try REST endpoint
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": f"{PROMPT_SYSTEM}\n\nOperator notes to interpret:\n{json.dumps(notes)}"}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            resp = requests.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    text = candidates[0]["content"]["parts"][0]["text"]
                    extracted = extract_json_array(text)
                    if extracted:
                        return extracted, None
            else:
                last_err = f"{m} HTTP {resp.status_code}: {resp.text}"
        except Exception as e_rest:
            last_err = f"{m} exception: {str(e_rest)}"

        # B. Try SDK
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = f"Operator notes to interpret:\n{json.dumps(notes, indent=2)}"
            response = client.models.generate_content(
                model=m,
                contents=f"{PROMPT_SYSTEM}\n\n{prompt}",
                config={"response_mime_type": "application/json"}
            )
            if response and response.text:
                extracted = extract_json_array(response.text)
                if extracted:
                    return extracted, None
        except Exception as e_sdk:
            last_err = f"{m} SDK: {str(e_sdk)}"

    return None, f"All models failed. Last error: {last_err}. Checked: {active_models[:3]}"

def call_openai_compatible_api(
    notes: List[str],
    api_key: str,
    base_url: Optional[str] = None,
    model: str = "gpt-4o-mini"
) -> Optional[List[Dict[str, Any]]]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        prompt = f"Operator notes to interpret:\n{json.dumps(notes, indent=2)}"
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        if content:
            return extract_json_array(content)
    except Exception as e:
        logger.error(f"OpenAI compatible API call failed: {e}")
    return None

def interpret_operator_notes(notes: List[str]) -> List[Dict[str, Any]]:
    """
    Mandatory LLM interpretation path.
    Invokes generative language model (Gemini 2.0 Flash or OpenAI GPT-4o-mini)
    to convert unstructured operator notes into structured directives.
    """
    diag = []

    # 1. Primary: Google Gemini 2.0 Flash
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        gemini_key = gemini_key.strip().strip("'").strip('"')
        res, err = call_gemini_api(notes, gemini_key)
        if res is not None:
            return res
        diag.append(f"Gemini failed ({err})")
    else:
        diag.append("GEMINI_API_KEY not found in env")

    # 2. Secondary: OpenAI GPT-4o-mini
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        openai_key = openai_key.strip().strip("'").strip('"')
        res = call_openai_compatible_api(notes, openai_key, model="gpt-4o-mini")
        if res is not None:
            return res
        diag.append("OpenAI call failed")

    # 3. Tertiary: Groq
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        groq_key = groq_key.strip().strip("'").strip('"')
        res = call_openai_compatible_api(
            notes, groq_key,
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile"
        )
        if res is not None:
            return res
        diag.append("Groq call failed")

    raise RuntimeError(f"LLM interpretation error: {'; '.join(diag)}")
