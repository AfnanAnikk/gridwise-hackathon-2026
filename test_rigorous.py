import json
import pytest
from fastapi.testclient import TestClient
from main import app
from schemas import OptimizeRequest, OptimizeResponse

client = TestClient(app)

def create_base_payload(
    notes=None,
    initial_energy=200.0,
    min_energy=50.0,
    capacity=500.0,
    max_charge=100.0,
    max_discharge=100.0,
    demand_val=150.0,
    solar_val=50.0,
    tariff_val=8.0
):
    if notes is None:
        notes = ["Normal routine operations today."]

    hours = []
    for h in range(24):
        # Vary tariff so optimizer has clear arbitrage opportunity:
        # Off-peak 0-5 (4.0 BDT), peak 17-22 (14.0 BDT), regular otherwise (8.0 BDT)
        t = 4.0 if h < 6 else (14.0 if 17 <= h < 23 else 8.0)
        s = 100.0 if 10 <= h <= 15 else 0.0
        # Set demand reasonable so constraints with 50 max_grid + 100 battery are feasible
        d = 140.0 if 9 <= h <= 21 else 80.0
        hours.append({
            "hour": h,
            "demand_kwh": float(d),
            "solar_kwh": float(s),
            "tariff_bdt_per_kwh": float(t)
        })

    return {
        "scenario_id": "TEST-SCENARIO",
        "operator_notes": notes,
        "hours": hours,
        "battery": {
            "capacity_kwh": capacity,
            "initial_energy_kwh": initial_energy,
            "minimum_energy_kwh": min_energy,
            "max_charge_kwh_per_hour": max_charge,
            "max_discharge_kwh_per_hour": max_discharge
        }
    }

def verify_physics(response_data, payload):
    resp = OptimizeResponse(**response_data)
    plan = resp.hourly_plan
    bat = payload["battery"]
    hours = payload["hours"]

    # Calculate effective solar based on parsed directives
    effective_solar = [h["solar_kwh"] for h in hours]
    for d in resp.directive_interpretation:
        if d.applies and d.directive_type == "solar_reduction":
            factor = d.structured_adjustment.get("factor", 1.0)
            for h in d.structured_adjustment.get("hours", []):
                effective_solar[h] *= factor

    running_e = bat["initial_energy_kwh"]
    total_g = 0.0
    total_cost = 0.0
    peak_g = 0.0

    for h in range(24):
        entry = plan[h]
        assert entry.hour == h
        assert entry.grid_kwh >= -1e-4
        assert entry.solar_used_kwh >= -1e-4
        assert entry.battery_kwh >= -1e-4

        # Solar bound
        assert entry.solar_used_kwh <= effective_solar[h] + 0.01, f"Hour {h}: solar used exceeds effective solar!"

        c = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        d = entry.battery_kwh if entry.battery_action == "discharge" else 0.0

        # Rate limits
        assert c <= bat["max_charge_kwh_per_hour"] + 0.01
        assert d <= bat["max_discharge_kwh_per_hour"] + 0.01

        # Energy balance: grid + solar + discharge == demand + charge
        demand = hours[h]["demand_kwh"]
        supplied = entry.grid_kwh + entry.solar_used_kwh + d
        demanded = demand + c
        assert abs(supplied - demanded) < 0.02, f"Hour {h}: Energy balance broken: {supplied} vs {demanded}"

        # Battery state update
        running_e = running_e + c - d
        assert abs(entry.battery_energy_after_kwh - running_e) < 0.02
        assert entry.battery_energy_after_kwh <= bat["capacity_kwh"] + 0.01
        assert entry.battery_energy_after_kwh >= bat["minimum_energy_kwh"] - 0.01

        total_g += entry.grid_kwh
        total_cost += entry.grid_kwh * hours[h]["tariff_bdt_per_kwh"]
        if entry.grid_kwh > peak_g:
            peak_g = entry.grid_kwh

    # End of day neutrality
    assert abs(plan[23].battery_energy_after_kwh - bat["initial_energy_kwh"]) < 0.02, "End-of-day battery neutrality failed!"

    # Summary metrics match
    assert abs(resp.total_grid_kwh - total_g) < 0.05
    assert abs(resp.total_cost_bdt - total_cost) < 0.05
    assert abs(resp.peak_grid_kwh - peak_g) < 0.05

    return resp

def test_directive_no_charge_window():
    # Battery charging prohibited from 2 AM to 5 AM [2, 3, 4]
    payload = create_base_payload(notes=["Do not charge the battery between 2 AM and 5 AM."])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    res_obj = verify_physics(resp.json(), payload)

    # Directive check
    d = res_obj.directive_interpretation[0]
    assert d.applies is True
    assert d.directive_type == "no_charge_window"
    assert d.structured_adjustment["hours"] == [2, 3, 4]

    # Downstream check: in hours 2, 3, 4, battery action must NOT be charge
    for h in [2, 3, 4]:
        assert res_obj.hourly_plan[h].battery_action != "charge", f"Battery charged at hour {h} despite no_charge_window!"
        if res_obj.hourly_plan[h].battery_action == "idle":
            assert res_obj.hourly_plan[h].battery_kwh == 0.0

def test_directive_no_discharge_window():
    # Battery discharging prohibited during peak tariff 6 PM to 8 PM [18, 19]
    payload = create_base_payload(notes=["Avoid discharging the battery between 6 PM and 8 PM."])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    res_obj = verify_physics(resp.json(), payload)

    d = res_obj.directive_interpretation[0]
    assert d.applies is True
    assert d.directive_type == "no_discharge_window"
    assert d.structured_adjustment["hours"] == [18, 19]

    # Downstream check: in hours 18, 19, battery action must NOT be discharge
    for h in [18, 19]:
        assert res_obj.hourly_plan[h].battery_action != "discharge", f"Battery discharged at hour {h} despite no_discharge_window!"

def test_directive_minimum_battery_reserve():
    # Keep reserve of 300 kWh between 17:00 and 20:00 [17, 18, 19]
    payload = create_base_payload(
        notes=["Keep at least 300 kWh in reserve from 5 PM until 8 PM."],
        initial_energy=250.0,
        min_energy=50.0,
        capacity=500.0
    )
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    res_obj = verify_physics(resp.json(), payload)

    d = res_obj.directive_interpretation[0]
    assert d.applies is True
    assert d.directive_type == "minimum_battery_reserve"
    assert d.structured_adjustment["hours"] == [17, 18, 19]
    assert abs(d.structured_adjustment["minimum_energy_kwh"] - 300.0) < 0.01

    # Downstream check: in hours 17, 18, 19, battery_energy_after_kwh must be >= 300
    for h in [17, 18, 19]:
        assert res_obj.hourly_plan[h].battery_energy_after_kwh >= 300.0 - 0.02, f"Battery fell below 300 kWh reserve at hour {h}!"

def test_directive_max_grid_window():
    # Grid import cap: max 50 kWh between 18:00 and 21:00 [18, 19, 20]
    # Demand is 140 kWh, battery can discharge up to 100 kWh, 50 kWh grid is sufficient (140 <= 50 + 100)
    payload = create_base_payload(
        notes=["Grid purchase cannot exceed 50 kWh from 6 PM to 9 PM."],
        initial_energy=300.0
    )
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    res_obj = verify_physics(resp.json(), payload)

    d = res_obj.directive_interpretation[0]
    assert d.applies is True
    assert d.directive_type == "max_grid_window"
    assert d.structured_adjustment["hours"] == [18, 19, 20]
    assert abs(d.structured_adjustment["max_grid_kwh"] - 50.0) < 0.01

    # Downstream check: grid_kwh <= 50 in those hours
    for h in [18, 19, 20]:
        assert res_obj.hourly_plan[h].grid_kwh <= 50.0 + 0.02, f"Hour {h}: Grid import exceeded 50 kWh cap!"

def test_directive_solar_reduction_paraphrases():
    # Paraphrase 1: "drop to about 20% from 1 PM to 3 PM" -> factor = 0.2
    payload1 = create_base_payload(notes=["Solar output will drop to about 20% from 1 PM to 3 PM."])
    resp1 = client.post("/optimize-energy", json=payload1)
    d1 = resp1.json()["directive_interpretation"][0]
    assert d1["structured_adjustment"]["hours"] == [13, 14]
    assert abs(d1["structured_adjustment"]["factor"] - 0.2) < 0.01

    # Paraphrase 2: "Expect an 80% reduction in rooftop solar during the 1-3 PM window" -> factor = 0.2
    payload2 = create_base_payload(notes=["Expect an 80% reduction in rooftop solar during the 1-3 PM window."])
    resp2 = client.post("/optimize-energy", json=payload2)
    d2 = resp2.json()["directive_interpretation"][0]
    assert d2["structured_adjustment"]["hours"] == [13, 14]
    assert abs(d2["structured_adjustment"]["factor"] - 0.2) < 0.01

    # Paraphrase 3: "roughly one-fifth of normal solar output from 1 PM to 3 PM" -> factor = 0.2
    payload3 = create_base_payload(notes=["roughly one-fifth of normal solar output from 1 PM to 3 PM."])
    resp3 = client.post("/optimize-energy", json=payload3)
    d3 = resp3.json()["directive_interpretation"][0]
    assert d3["structured_adjustment"]["hours"] == [13, 14]
    assert abs(d3["structured_adjustment"]["factor"] - 0.2) < 0.01

def test_multiple_notes_with_distractor():
    # 3 notes: 1 solar reduction, 1 no charge, 1 distractor
    notes = [
        "Solar generation will drop to about 30% from 12:00 to 14:00.",
        "Maintenance staff meeting at 3 PM in the conference hall.",
        "Do not charge battery from 7 PM until 10 PM."
    ]
    payload = create_base_payload(notes=notes)
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    res_obj = verify_physics(resp.json(), payload)

    directives = res_obj.directive_interpretation
    assert len(directives) == 3

    # Note 0
    assert directives[0].note_index == 0
    assert directives[0].applies is True
    assert directives[0].directive_type == "solar_reduction"
    assert directives[0].structured_adjustment["hours"] == [12, 13]

    # Note 1 (Distractor)
    assert directives[1].note_index == 1
    assert directives[1].applies is False
    assert directives[1].directive_type == "no_op"
    assert directives[1].structured_adjustment is None

    # Note 2
    assert directives[2].note_index == 2
    assert directives[2].applies is True
    assert directives[2].directive_type == "no_charge_window"
    assert directives[2].structured_adjustment["hours"] == [19, 20, 21]

def test_extreme_battery_starting_states():
    # Test starting at minimum reserve
    payload_low = create_base_payload(initial_energy=50.0, min_energy=50.0, capacity=500.0)
    resp_low = client.post("/optimize-energy", json=payload_low)
    assert resp_low.status_code == 200
    verify_physics(resp_low.json(), payload_low)

    # Test starting at high state
    payload_high = create_base_payload(initial_energy=450.0, min_energy=50.0, capacity=500.0)
    resp_high = client.post("/optimize-energy", json=payload_high)
    assert resp_high.status_code == 200
    verify_physics(resp_high.json(), payload_high)

def test_invalid_request_shapes():
    # 1. Negative demand
    bad1 = create_base_payload()
    bad1["hours"][5]["demand_kwh"] = -10.0
    assert client.post("/optimize-energy", json=bad1).status_code == 400

    # 2. 0 operator notes
    bad3 = create_base_payload()
    bad3["operator_notes"] = []
    assert client.post("/optimize-energy", json=bad3).status_code == 400

    # 3. 4 operator notes (limit is 3)
    bad4 = create_base_payload()
    bad4["operator_notes"] = ["Note 1", "Note 2", "Note 3", "Note 4"]
    assert client.post("/optimize-energy", json=bad4).status_code == 400
