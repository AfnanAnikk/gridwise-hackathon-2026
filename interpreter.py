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

def call_gemini_api(notes: List[str], api_key: str) -> Optional[List[Dict[str, Any]]]:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"Operator notes to interpret:\n{json.dumps(notes, indent=2)}"
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"{PROMPT_SYSTEM}\n\n{prompt}",
            config={"response_mime_type": "application/json"}
        )
        if response and response.text:
            return extract_json_array(response.text)
    except Exception:
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

WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20
}

def parse_time_window(text: str) -> List[int]:
    n_lower = text.lower()

    # Pattern 1: 1-3 PM or 1-3pm or 1 - 3 PM
    match_dash = re.search(r'(\d{1,2})\s*-\s*(\d{1,2})\s*(am|pm)', n_lower)
    if match_dash:
        h1, h2, ampm = int(match_dash.group(1)), int(match_dash.group(2)), match_dash.group(3)
        if ampm == 'pm' and h1 < 12: h1 += 12
        if ampm == 'pm' and h2 < 12: h2 += 12
        if ampm == 'am' and h1 == 12: h1 = 0
        if ampm == 'am' and h2 == 12: h2 = 0
        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    # Pattern 2: 1 PM to 3 PM / 1pm until 3pm / between 1 PM and 3 PM
    match_ampm = re.search(r'(\d{1,2})\s*(am|pm)?\s*(?:to|until|and|-)\s*(\d{1,2})\s*(am|pm)', n_lower)
    if match_ampm:
        h1, p1, h2, p2 = match_ampm.groups()
        h1, h2 = int(h1), int(h2)
        p1 = p1 or p2
        if p1 == 'pm' and h1 < 12: h1 += 12
        if p1 == 'am' and h1 == 12: h1 = 0
        if p2 == 'pm' and h2 < 12: h2 += 12
        if p2 == 'am' and h2 == 12: h2 = 0
        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    # Pattern 3: word numbers: "from one until three", "one to three"
    words = "|".join(WORD_TO_NUM.keys())
    match_word = re.search(rf'({words})\s*(?:to|until|and|-)\s*({words})', n_lower)
    if match_word:
        w1, w2 = match_word.group(1), match_word.group(2)
        h1, h2 = WORD_TO_NUM[w1], WORD_TO_NUM[w2]
        # In afternoon context (like solar hours), map 1..5 to 13..17
        if any(solar_word in n_lower for solar_word in ["solar", "pv", "sun"]):
            if h1 <= 12: h1 += 12
            if h2 <= 12: h2 += 12
        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    # Pattern 4: 24-hour: "13:00 to 15:00", "between 13:00 and 15:00", "13 to 15"
    match_24 = re.search(r'(?:between|from)?\s*(\d{1,2})(?::00)?\s*(?:and|to|until|-)\s*(\d{1,2})(?::00)?', n_lower)
    if match_24:
        h1, h2 = int(match_24.group(1)), int(match_24.group(2))
        if 0 <= h1 < h2 <= 24:
            return list(range(h1, h2))

    # Pattern 5: single hour: "during hour 14", "at 2 PM"
    match_single = re.search(r'(?:hour|at)\s*(\d{1,2})\s*(am|pm)?', n_lower)
    if match_single:
        h, p = int(match_single.group(1)), match_single.group(2)
        if p == 'pm' and h < 12: h += 12
        if p == 'am' and h == 12: h = 0
        if 0 <= h < 24:
            return [h]

    return []

def heuristic_offline_parser(notes: List[str]) -> List[Dict[str, Any]]:
    results = []
    for idx, note in enumerate(notes):
        n_lower = note.lower()
        hours = parse_time_window(note)

        # 1. Solar reduction
        if any(w in n_lower for w in ["solar", "pv", "rooftop", "photovoltaic", "sun"]):
            factor = 0.2
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

        # 2. No charge window
        elif any(phrase in n_lower for phrase in [
            "not charge", "no charge", "stop charging", "prevent charge",
            "avoid charge", "avoid charging", "prohibit charge", "prohibited from charging",
            "disable charge", "charging unavailable", "pause charge"
        ]):
            if not hours: hours = [14, 15]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery charging prohibited during window."
            })

        # 3. No discharge window
        elif any(phrase in n_lower for phrase in [
            "not discharge", "no discharge", "stop discharging", "prevent discharge",
            "avoid discharge", "avoid discharging", "prohibit discharge", "prohibited from discharging",
            "disable discharge", "discharging unavailable", "pause discharge"
        ]):
            if not hours: hours = [18, 19]
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery discharging prohibited during window."
            })

        # 4. Minimum battery reserve
        elif any(phrase in n_lower for phrase in [
            "reserve", "hold at least", "keep at least", "maintain at least",
            "reserve level", "reserve should not drop", "at or above"
        ]):
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

        # 5. Max grid window
        elif "grid" in n_lower and any(w in n_lower for w in ["limit", "cap", "max", "exceed", "purchase", "draw", "intake"]):
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

        # 6. Distractor (no_op)
        else:
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

    # 4. Heuristic parser fallback
    return heuristic_offline_parser(notes)
