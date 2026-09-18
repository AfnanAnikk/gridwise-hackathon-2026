from typing import List, Dict, Any, Optional
from schemas import DirectiveInterpretationEntry, DirectiveType, BatteryInput

ALLOWED_DIRECTIVES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
}

def clean_hours(hours: Any) -> List[int]:
    if not isinstance(hours, list):
        return []
    cleaned = []
    for h in hours:
        try:
            val = int(h)
            if 0 <= val <= 23:
                cleaned.append(val)
        except (ValueError, TypeError):
            continue
    return sorted(list(set(cleaned)))

def validate_and_sanitize_directive(
    raw_entry: Dict[str, Any],
    expected_index: int,
    battery: BatteryInput
) -> DirectiveInterpretationEntry:
    note_idx = raw_entry.get("note_index", expected_index)
    if not isinstance(note_idx, int) or note_idx != expected_index:
        note_idx = expected_index

    directive_type = str(raw_entry.get("directive_type", "no_op")).strip().lower()
    if directive_type not in ALLOWED_DIRECTIVES:
        directive_type = "no_op"

    explanation = str(raw_entry.get("explanation", "Operator note processed."))

    raw_adj = raw_entry.get("structured_adjustment")
    if directive_type == "no_op" or raw_adj is None or not isinstance(raw_adj, dict):
        return DirectiveInterpretationEntry(
            note_index=note_idx,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=explanation
        )

    hours = clean_hours(raw_adj.get("hours"))
    if not hours:
        # If no valid hours could be parsed for a directive requiring hours, safe fallback to no_op
        return DirectiveInterpretationEntry(
            note_index=note_idx,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation=f"{explanation} (Ignored: no valid hours specified)"
        )

    sanitized_adj: Dict[str, Any] = {"hours": hours}

    if directive_type == "solar_reduction":
        raw_factor = raw_adj.get("factor")
        try:
            factor = float(raw_factor)
            if factor > 1.0 and factor <= 100.0:
                # Heuristic: if LLM returned 20 instead of 0.20 or 80 instead of 0.8
                factor = factor / 100.0
            factor = max(0.0, min(1.0, factor))
        except (TypeError, ValueError):
            factor = 1.0
        sanitized_adj["factor"] = round(factor, 4)

    elif directive_type == "minimum_battery_reserve":
        raw_min = raw_adj.get("minimum_energy_kwh")
        if raw_min is None:
            raw_min = raw_adj.get("factor") or raw_adj.get("percentage")
        try:
            min_energy = float(raw_min)
            if 0.0 < min_energy <= 1.0 and battery.capacity_kwh > 1.0:
                min_energy = min_energy * battery.capacity_kwh
            min_energy = max(0.0, min(battery.capacity_kwh, min_energy))
        except (TypeError, ValueError):
            min_energy = battery.minimum_energy_kwh
        sanitized_adj["minimum_energy_kwh"] = round(min_energy, 4)

    elif directive_type == "max_grid_window":
        raw_max = raw_adj.get("max_grid_kwh")
        try:
            max_grid = float(raw_max)
            max_grid = max(0.0, max_grid)
        except (TypeError, ValueError):
            max_grid = 0.0
        sanitized_adj["max_grid_kwh"] = round(max_grid, 4)

        # no_charge_window and no_discharge_window: hours already set, nothing else needed

    return DirectiveInterpretationEntry(
        note_index=note_idx,
        applies=True,
        directive_type=directive_type,
        structured_adjustment=sanitized_adj,
        explanation=explanation
    )

def guardrail_directives(
    raw_directives: List[Dict[str, Any]],
    num_notes: int,
    battery: BatteryInput
) -> List[DirectiveInterpretationEntry]:
    sanitized_entries: List[DirectiveInterpretationEntry] = []
    lookup = {}
    for entry in raw_directives:
        if isinstance(entry, dict) and "note_index" in entry:
            try:
                idx = int(entry["note_index"])
                lookup[idx] = entry
            except (ValueError, TypeError):
                pass

    for i in range(num_notes):
        raw = lookup.get(i)
        if raw is None and i < len(raw_directives) and isinstance(raw_directives[i], dict):
            raw = raw_directives[i]
        if raw is None:
            raw = {
                "note_index": i,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "No directive detected."
            }
        validated = validate_and_sanitize_directive(raw, i, battery)
        sanitized_entries.append(validated)

    return sanitized_entries
