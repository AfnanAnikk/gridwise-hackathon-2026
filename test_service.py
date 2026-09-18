import json
import pytest
from fastapi.testclient import TestClient
from main import app
from schemas import OptimizeRequest, OptimizeResponse

client = TestClient(app)

@pytest.fixture
def sample_payload():
    with open("sample_request.json") as f:
        return json.load(f)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_optimize_energy_schema_and_physics(sample_payload):
    response = client.post("/optimize-energy", json=sample_payload)
    assert response.status_code == 200
    data = response.json()

    # Validate against Pydantic schema
    resp_obj = OptimizeResponse(**data)
    assert resp_obj.scenario_id == sample_payload["scenario_id"]

    # 1. Directives validation
    directives = resp_obj.directive_interpretation
    assert len(directives) == len(sample_payload["operator_notes"])
    for i, d in enumerate(directives):
        assert d.note_index == i
        if d.directive_type == "no_op":
            assert d.applies is False
            assert d.structured_adjustment is None
        else:
            assert d.applies is True
            assert d.structured_adjustment is not None
            hours = d.structured_adjustment["hours"]
            assert hours == sorted(list(set(hours)))
            for h in hours:
                assert 0 <= h <= 23

    # 2. Hourly Plan & Energy Rules validation
    plan = resp_obj.hourly_plan
    assert len(plan) == 24
    battery_cfg = sample_payload["battery"]
    hours_cfg = sample_payload["hours"]

    running_energy = battery_cfg["initial_energy_kwh"]
    total_grid_calc = 0.0
    total_cost_calc = 0.0
    peak_grid_calc = 0.0

    # Extract effective solar
    effective_solar = [h["solar_kwh"] for h in hours_cfg]
    for d in directives:
        if d.applies and d.directive_type == "solar_reduction":
            factor = d.structured_adjustment.get("factor", 1.0)
            for h in d.structured_adjustment.get("hours", []):
                effective_solar[h] *= factor

    for h in range(24):
        entry = plan[h]
        assert entry.hour == h
        assert entry.grid_kwh >= -1e-5
        assert entry.solar_used_kwh >= -1e-5
        assert entry.battery_kwh >= -1e-5
        assert entry.battery_action in ("charge", "discharge", "idle")

        # Solar bound check
        assert entry.solar_used_kwh <= effective_solar[h] + 0.01

        # Determine charge and discharge
        c = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        d = entry.battery_kwh if entry.battery_action == "discharge" else 0.0

        # Rate limit checks
        assert c <= battery_cfg["max_charge_kwh_per_hour"] + 0.01
        assert d <= battery_cfg["max_discharge_kwh_per_hour"] + 0.01

        # Energy Balance check: grid + solar_used + discharge == demand + charge
        demand = hours_cfg[h]["demand_kwh"]
        supplied = entry.grid_kwh + entry.solar_used_kwh + d
        demanded = demand + c
        assert abs(supplied - demanded) < 0.05, f"Energy balance broken at hour {h}: {supplied} vs {demanded}"

        # Battery state update
        running_energy = running_energy + c - d
        assert abs(entry.battery_energy_after_kwh - running_energy) < 0.05
        assert entry.battery_energy_after_kwh <= battery_cfg["capacity_kwh"] + 0.01
        assert entry.battery_energy_after_kwh >= battery_cfg["minimum_energy_kwh"] - 0.01

        # Accumulate metrics
        total_grid_calc += entry.grid_kwh
        total_cost_calc += entry.grid_kwh * hours_cfg[h]["tariff_bdt_per_kwh"]
        if entry.grid_kwh > peak_grid_calc:
            peak_grid_calc = entry.grid_kwh

    # End of day neutrality check: E_after[23] == initial_energy
    assert abs(plan[23].battery_energy_after_kwh - battery_cfg["initial_energy_kwh"]) < 0.05, "End-of-day battery neutrality failed!"

    # Totals agreement check
    assert abs(resp_obj.total_grid_kwh - total_grid_calc) < 0.1
    assert abs(resp_obj.total_cost_bdt - total_cost_calc) < 0.1
    assert abs(resp_obj.peak_grid_kwh - peak_grid_calc) < 0.1

def test_paraphrased_directives(sample_payload):
    # Test hidden paraphrases from the problem statement
    payload = dict(sample_payload)
    payload["operator_notes"] = [
        "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
        "Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
        "All staff members should attend the town hall tomorrow afternoon."
    ]

    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200
    data = response.json()
    directives = data["directive_interpretation"]

    # Note 0: solar reduction
    assert directives[0]["applies"] is True
    assert directives[0]["directive_type"] == "solar_reduction"
    assert directives[0]["structured_adjustment"]["hours"] == [13, 14]
    assert abs(directives[0]["structured_adjustment"]["factor"] - 0.2) < 0.05

    # Note 1: battery reserve
    assert directives[1]["applies"] is True
    assert directives[1]["directive_type"] == "minimum_battery_reserve"
    assert directives[1]["structured_adjustment"]["hours"] == [18, 19, 20]
    assert directives[1]["structured_adjustment"]["minimum_energy_kwh"] == 120.0

    # Note 2: no_op
    assert directives[2]["applies"] is False
    assert directives[2]["directive_type"] == "no_op"
    assert directives[2]["structured_adjustment"] is None

def test_malformed_input_handling():
    # Send empty json
    resp = client.post("/optimize-energy", json={})
    assert resp.status_code == 400

    # Send missing hour entry (only 23 hours)
    with open("sample_request.json") as f:
        bad_payload = json.load(f)
    bad_payload["hours"] = bad_payload["hours"][:23]
    resp = client.post("/optimize-energy", json=bad_payload)
    assert resp.status_code == 400
