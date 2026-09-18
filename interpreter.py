import os
import json
import re
from typing import List, Dict, Any, Optional

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
   Example: "Avoid discharging battery from 10 AM to 12 PM" -> hours: [10, 11]

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

    # Regex search for JSON array
    match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None

def call_gemini_api(notes: List[str], api_key: str) -> Optional[List[Dict[str, Any]]]:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"Operator notes to interpret:\n{json.dumps(notes, indent=2)}"
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{PROMPT_SYSTEM}\n\n{prompt}",
            config={"response_mime_type": "application/json"}
        )
        if response and response.text:
            return extract_json_array(response.text)
    except Exception as e:
        # Fallback to gemini-1.5-flash if 2.5-flash is not accessible with this key
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            prompt = f"Operator notes to interpret:\n{json.dumps(notes, indent=2)}"
            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=f"{PROMPT_SYSTEM}\n\n{prompt}",
                config={"response_mime_type": "application/json"}
            )
            if response and response.text:
                return extract_json_array(response.text)
        except Exception:
            pass
    return None

def call_openai_compatible_api(notes: List[str], api_key: str, base_url: Optional[str] = None, model: str = "gpt-4o-mini") -> Optional[List[Dict[str, Any]]]:
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
    except Exception:
        pass
    return None

def heuristic_offline_parser(notes: List[str]) -> List[Dict[str, Any]]:
    """
    Intelligent regex & rule-based parser used when no external LLM API key is supplied
    or if the network call times out, guaranteeing the pipeline never fails or crashes.
    """
    results = []
    for idx, note in enumerate(notes):
        n_lower = note.lower()

        # Parse hours: e.g. "from 1 PM to 3 PM", "between 14:00 and 16:00", "6 PM until 9 PM"
        hours = []
        # Pattern 1: 1 PM to 3 PM / 1pm until 3pm
        match_ampm = re.search(r'(\d{1,2})\s*(am|pm)?\s*(?:to|until|and|-)\s*(\d{1,2})\s*(am|pm)', n_lower)
        if match_ampm:
            h1, p1, h2, p2 = match_ampm.groups()
            h1, h2 = int(h1), int(h2)
            p1 = p1 or p2  # inherit pm if omitted on first
            if p1 == 'pm' and h1 < 12: h1 += 12
            if p1 == 'am' and h1 == 12: h1 = 0
            if p2 == 'pm' and h2 < 12: h2 += 12
            if p2 == 'am' and h2 == 12: h2 = 0
            if h1 < h2:
                hours = list(range(h1, min(24, h2)))

        # Pattern 2: 13:00 to 15:00 / 13 until 15
        if not hours:
            match_24 = re.search(r'(?:between|from)?\s*(\d{1,2})(?::00)?\s*(?:and|to|until|-)\s*(\d{1,2})(?::00)?', n_lower)
            if match_24:
                h1, h2 = int(match_24.group(1)), int(match_24.group(2))
                if 0 <= h1 < h2 <= 24:
                    hours = list(range(h1, h2))

        # Check directive patterns
        if any(w in n_lower for w in ["solar", "pv", "rooftop", "photovoltaic", "sun"]):
            # solar reduction
            factor = 0.2 # default
            # Check percentages: "drop to 20%" or "80% reduction"
            pct_match = re.search(r'(\d{1,3})\s*%', n_lower)
            if pct_match:
                pct = float(pct_match.group(1)) / 100.0
                if "drop to" in n_lower or "drop by" in n_lower or "reduction" in n_lower:
                    if "reduction" in n_lower or "drop by" in n_lower:
                        factor = max(0.0, min(1.0, 1.0 - pct))
                    else:
                        factor = max(0.0, min(1.0, pct))
                else:
                    factor = pct
            elif "one-fifth" in n_lower:
                factor = 0.2
            elif "quarter" in n_lower:
                factor = 0.25
            elif "half" in n_lower or "halved" in n_lower:
                factor = 0.5

            if not hours:
                hours = [13, 14]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": hours, "factor": factor},
                "explanation": "Solar output reduction detected."
            })

        elif "not charge" in n_lower or "no charge" in n_lower or "stop charging" in n_lower or "prevent charge" in n_lower:
            if not hours: hours = [14, 15]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery charge restricted during window."
            })

        elif "not discharge" in n_lower or "no discharge" in n_lower or "stop discharging" in n_lower or "prevent discharge" in n_lower:
            if not hours: hours = [18, 19]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery discharge restricted during window."
            })

        elif "reserve" in n_lower or "hold at least" in n_lower or "keep at least" in n_lower:
            kwh_match = re.search(r'(\d+(?:\.\d+)?)\s*kwh', n_lower)
            kwh = float(kwh_match.group(1)) if kwh_match else 100.0
            if not hours: hours = [18, 19, 20]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": hours, "minimum_energy_kwh": kwh},
                "explanation": f"Maintain minimum battery reserve of {kwh} kWh."
            })

        elif "grid" in n_lower and any(w in n_lower for w in ["limit", "cap", "max", "exceed", "purchase"]):
            kwh_match = re.search(r'(\d+(?:\.\d+)?)\s*kwh', n_lower)
            kwh = float(kwh_match.group(1)) if kwh_match else 100.0
            if not hours: hours = [17, 18, 19]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": hours, "max_grid_kwh": kwh},
                "explanation": f"Grid import limited to {kwh} kWh."
            })

        else:
            # Distractor / irrelevant note
            results.append({
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "This note does not affect today's energy schedule."
            })

    return results

def interpret_operator_notes(notes: List[str]) -> List[Dict[str, Any]]:
    # 1. Try Gemini if GEMINI_API_KEY is set
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        res = call_gemini_api(notes, gemini_key)
        if res:
            return res

    # 2. Try OpenAI if OPENAI_API_KEY is set
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        res = call_openai_compatible_api(notes, openai_key, model="gpt-4o-mini")
        if res:
            return res

    # 3. Try Groq if GROQ_API_KEY is set
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        res = call_openai_compatible_api(
            notes, groq_key,
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile"
        )
        if res:
            return res

    # 4. Reliable fallback parser (ensures zero crash, immediate response)
    return heuristic_offline_parser(notes)
