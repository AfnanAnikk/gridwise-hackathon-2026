import json
import pytest
from schemas import OptimizeRequest, DirectiveInterpretationEntry
from optimizer import solve_energy_schedule

def test_all_official_benchmark_cases():
    with open("official_sample_cases.json") as f:
        data = json.load(f)

    for c in data["cases"]:
        cid = c["id"]
        req = OptimizeRequest(**c["input"])
        directives = [
            DirectiveInterpretationEntry(**d)
            for d in c["expected_output"]["directive_interpretation"]
        ]
        hourly_plan, total_grid, total_cost, peak_grid = solve_energy_schedule(req, directives)

        exp_cost = c["expected_output"]["total_cost_bdt"]
        exp_grid = c["expected_output"]["total_grid_kwh"]
        exp_peak = c["expected_output"]["peak_grid_kwh"]

        assert abs(total_cost - exp_cost) < 0.05, f"{cid} cost mismatch: {total_cost} != {exp_cost}"
        assert abs(total_grid - exp_grid) < 0.05, f"{cid} grid mismatch: {total_grid} != {exp_grid}"
        assert abs(peak_grid - exp_peak) < 0.05, f"{cid} peak mismatch: {peak_grid} != {exp_peak}"
