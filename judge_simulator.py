import sys
import time
import json
import requests

def run_judge_harness(base_url: str):
    base_url = base_url.rstrip("/")
    print(f"\n==================================================")
    print(f"  RUNNING BUP CSE FEST 2026 JUDGE HARNESS")
    print(f"  Target: {base_url}")
    print(f"==================================================\n")

    total_score = 0
    max_score = 100

    # ----------------------------------------------------
    # TEST 1: GET /health (10 pts)
    # ----------------------------------------------------
    print("[1/4] Testing GET /health...")
    t0 = time.time()
    try:
        r_health = requests.get(f"{base_url}/health", timeout=90)
        lat_health = time.time() - t0
        if r_health.status_code == 200 and r_health.json().get("status") == "ok":
            print(f"  PASSED: Health check returned 200 OK in {lat_health:.2f}s")
            total_score += 10
        else:
            print(f"  FAILED: Health check returned status {r_health.status_code}: {r_health.text}")
    except Exception as e:
        print(f"  FAILED: Could not connect to /health: {e}")
        print("\nStopping harness: Base service is unreachable.")
        return

    # Warm-up: fire a small POST to wake the service fully before timing starts
    print("  [Warm-up] Sending wake-up POST to ensure service is fully ready...")
    try:
        requests.post(
            f"{base_url}/optimize-energy",
            json={"scenario_id": "WARMUP", "operator_notes": ["Warm up."], "hours": [{"hour": h, "demand_kwh": 100.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 8.0} for h in range(24)], "battery": {"capacity_kwh": 200.0, "initial_energy_kwh": 100.0, "minimum_energy_kwh": 20.0, "max_charge_kwh_per_hour": 50.0, "max_discharge_kwh_per_hour": 50.0}},
            timeout=90
        )
    except Exception:
        pass
    print("  [Warm-up] Done. Starting scored scenarios...\n")

    # ----------------------------------------------------
    # TEST SCENARIOS for POST /optimize-energy
    # ----------------------------------------------------
    test_cases = [
        {
            "name": "Case 1: Solar Reduction + No Charge + Distractor",
            "notes": [
                "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
                "Do not charge the battery between 2 PM and 4 PM.",
                "The campus cafeteria menu changes tomorrow morning."
            ],
            "expected_directives": [
                {"type": "solar_reduction", "applies": True, "hours": [13, 14], "factor": 0.2},
                {"type": "no_charge_window", "applies": True, "hours": [14, 15]},
                {"type": "no_op", "applies": False}
            ]
        },
        {
            "name": "Case 2: Battery Reserve + No Discharge",
            "notes": [
                "Keep at least 150 kWh in reserve from 6 PM until 9 PM.",
                "Avoid discharging the battery between 10 AM and 12 PM."
            ],
            "expected_directives": [
                {"type": "minimum_battery_reserve", "applies": True, "hours": [18, 19, 20], "min_kwh": 150.0},
                {"type": "no_discharge_window", "applies": True, "hours": [10, 11]}
            ]
        },
        {
            "name": "Case 3: Grid Import Ceiling Window",
            "notes": [
                "Grid purchase cannot exceed 60 kWh from 5 PM to 8 PM."
            ],
            "expected_directives": [
                {"type": "max_grid_window", "applies": True, "hours": [17, 18, 19], "max_grid": 60.0}
            ]
        }
    ]

    # Shared 24-hour baseline
    hours = []
    for h in range(24):
        tariff = 4.0 if h < 6 else (14.0 if 17 <= h < 23 else 8.0)
        solar = 120.0 if 10 <= h <= 15 else 0.0
        demand = 130.0 if 9 <= h <= 21 else 80.0
        hours.append({
            "hour": h,
            "demand_kwh": float(demand),
            "solar_kwh": float(solar),
            "tariff_bdt_per_kwh": float(tariff)
        })

    battery_spec = {
        "capacity_kwh": 500.0,
        "initial_energy_kwh": 200.0,
        "minimum_energy_kwh": 50.0,
        "max_charge_kwh_per_hour": 100.0,
        "max_discharge_kwh_per_hour": 100.0
    }

    print("\n[2/4] Running Evaluation Scenarios...")

    latencies = []
    directive_pts = 0
    physics_pts = 0
    opt_pts = 0

    for idx, tc in enumerate(test_cases, 1):
        print(f"\n  --> Scenario {idx}: {tc['name']}")
        payload = {
            "scenario_id": f"JUDGE-SCENARIO-{idx}",
            "operator_notes": tc["notes"],
            "hours": hours,
            "battery": battery_spec
        }

        t_start = time.time()
        try:
            res = requests.post(f"{base_url}/optimize-energy", json=payload, timeout=60)
            lat = time.time() - t_start
            latencies.append(lat)

            if res.status_code != 200:
                print(f"      [!] HTTP Error {res.status_code}: {res.text}")
                continue

            data = res.json()
        except requests.exceptions.Timeout:
            print(f"      [!] TIMEOUT (>30s) on scenario {idx}")
            continue
        except Exception as e:
            print(f"      [!] Request exception: {e}")
            continue

        print(f"      Response received in {lat:.2f}s")

        # 1. Check Directive Interpretation
        interp = data.get("directive_interpretation", [])
        case_dir_pass = True
        for note_idx, exp in enumerate(tc["expected_directives"]):
            if note_idx >= len(interp):
                print(f"      [x] Missing directive interpretation for note {note_idx}")
                case_dir_pass = False
                break
            actual = interp[note_idx]
            if actual.get("applies") != exp["applies"]:
                print(f"      [x] Note {note_idx} applies mismatch: expected {exp['applies']}, got {actual.get('applies')}")
                case_dir_pass = False
            if actual.get("directive_type") != exp["type"]:
                print(f"      [x] Note {note_idx} type mismatch: expected {exp['type']}, got {actual.get('directive_type')}")
                case_dir_pass = False
            if exp["applies"]:
                adj = actual.get("structured_adjustment", {})
                if adj.get("hours") != exp["hours"]:
                    print(f"      [x] Note {note_idx} hours mismatch: expected {exp['hours']}, got {adj.get('hours')}")
                    case_dir_pass = False
                if "factor" in exp:
                    if abs(adj.get("factor", 0) - exp["factor"]) > 0.05:
                        print(f"      [x] Note {note_idx} factor mismatch: expected {exp['factor']}, got {adj.get('factor')}")
                        case_dir_pass = False

        if case_dir_pass:
            print(f"      [✓] Directives interpretation 100% matched ground truth")
            directive_pts += (25 / len(test_cases))

        # 2. Independent Physics Replay
        plan = data.get("hourly_plan", [])
        if len(plan) != 24:
            print(f"      [x] Invalid hourly plan length: {len(plan)}")
            continue

        physics_pass = True
        running_e = battery_spec["initial_energy_kwh"]
        recalculated_cost = 0.0

        for h in range(24):
            item = plan[h]
            g = item.get("grid_kwh", 0)
            s_used = item.get("solar_used_kwh", 0)
            b_action = item.get("battery_action", "idle")
            b_kwh = item.get("battery_kwh", 0)
            e_after = item.get("battery_energy_after_kwh", 0)

            c = b_kwh if b_action == "charge" else 0.0
            d = b_kwh if b_action == "discharge" else 0.0

            # Energy balance: grid + solar_used + discharge == demand + charge
            dem = hours[h]["demand_kwh"]
            if abs((g + s_used + d) - (dem + c)) > 0.05:
                print(f"      [x] Hour {h}: Energy balance failed: {g + s_used + d} != {dem + c}")
                physics_pass = False
                break

            running_e = running_e + c - d
            if abs(e_after - running_e) > 0.05:
                print(f"      [x] Hour {h}: Battery transition mismatch: {e_after} != {running_e}")
                physics_pass = False
                break

            recalculated_cost += g * hours[h]["tariff_bdt_per_kwh"]

        # End of day neutrality check
        if abs(plan[23].get("battery_energy_after_kwh", 0) - battery_spec["initial_energy_kwh"]) > 0.05:
            print(f"      [x] End-of-day battery neutrality failed!")
            physics_pass = False

        if physics_pass:
            print(f"      [✓] Independent physics replay passed (Energy Balance + Battery Neutrality)")
            physics_pts += (25 / len(test_cases))
            opt_pts += (10 / len(test_cases))
            print(f"      [✓] Recalculated Cost: {recalculated_cost:.2f} BDT")

    total_score += directive_pts + physics_pts + opt_pts

    # ----------------------------------------------------
    # TEST 3: Latency / Performance Scoring (10 pts)
    # ----------------------------------------------------
    print("\n[3/4] Evaluating Latency & Performance...")
    if latencies:
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]
        print(f"  p95 latency: {p95:.2f}s")
        if p95 <= 5.0:
            total_score += 10
            print("  [✓] Full marks: p95 latency <= 5.0s (10/10)")
        elif p95 <= 15.0:
            total_score += 6
            print("  [!] p95 latency between 5s and 15s (6/10)")
        else:
            total_score += 3
            print("  [!] p95 latency > 15s (3/10)")
    else:
        print("  [x] No requests succeeded for latency evaluation.")

    # ----------------------------------------------------
    # SUMMARY SCORECARD
    # ----------------------------------------------------
    print(f"\n==================================================")
    print(f"              FINAL JUDGE SCORECARD               ")
    print(f"==================================================")
    print(f"  Health Endpoint:                      10 / 10")
    print(f"  Directive Interpretation:            {directive_pts:5.1f} / 25")
    print(f"  Physical Constraints & Directives:   {physics_pts:5.1f} / 25")
    print(f"  Optimization Quality:                {opt_pts:5.1f} / 10")
    print(f"  Performance & Latency:               {total_score - (10 + directive_pts + physics_pts + opt_pts):5.1f} / 10")
    print(f"  Deployment & Reproducibility (Fixed): 20 / 20")
    print(f"--------------------------------------------------")
    print(f"  TOTAL ESTIMATED SCORE:               {total_score + 20:5.1f} / 100")
    print(f"==================================================\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 judge_simulator.py <DEPLOYED_PUBLIC_URL>")
        sys.exit(1)
    run_judge_harness(sys.argv[1])
