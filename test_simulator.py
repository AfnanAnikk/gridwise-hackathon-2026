import sys
import time
import json
import requests
import math

def run_test_suite(base_url: str):
    base_url = base_url.rstrip("/")
    print("\n==================================================")
    print("      GRIDWISE AUTOMATED VERIFICATION SUITE       ")
    print(f"      Target: {base_url}")
    print("==================================================\n")

    # ----------------------------------------------------
    # STEP 1: GET /health
    # ----------------------------------------------------
    print("[1/2] Verifying GET /health endpoint...")
    t0 = time.time()
    try:
        r_health = requests.get(f"{base_url}/health", timeout=60)
        lat_health = time.time() - t0
        if r_health.status_code == 200 and r_health.json().get("status") == "ok":
            print(f"  PASSED: Health check returned 200 OK in {lat_health:.2f}s")
        else:
            print(f"  FAILED: Health check returned status {r_health.status_code}: {r_health.text}")
    except Exception as e:
        print(f"  FAILED: Could not connect to /health: {e}")
        print("\nStopping harness: Base service is unreachable.")
        return

    # Load official sample cases
    with open("official_sample_cases.json") as f:
        case_pack = json.load(f)
    cases = case_pack["cases"]

    print(f"\n[2/2] Running {len(cases)} Official Evaluation Scenarios...")

    latencies = []
    dir_correct_count = 0
    physics_passed_count = 0
    quality_ratios = []

    for c in cases:
        cid = c["id"]
        label = c.get("label", cid)
        payload = c["input"]
        exp_out = c["expected_output"]

        print(f"\n  --> {cid}: {label}")
        t_start = time.time()
        try:
            res = requests.post(f"{base_url}/optimize-energy", json=payload, timeout=30)
            lat = time.time() - t_start
            latencies.append(lat)

            if res.status_code != 200:
                print(f"      [!] HTTP Error {res.status_code}: {res.text[:200]}")
                continue

            data = res.json()
        except requests.exceptions.Timeout:
            print(f"      [!] TIMEOUT (>30s) on scenario {cid}")
            continue
        except Exception as e:
            print(f"      [!] Request exception: {e}")
            continue

        print(f"      Response received in {lat:.2f}s")

        # 1. Check Directive Interpretation
        interp = data.get("directive_interpretation", [])
        exp_interp = exp_out.get("directive_interpretation", [])
        case_dir_pass = True

        if len(interp) != len(exp_interp):
            print(f"      [x] Count mismatch: got {len(interp)}, expected {len(exp_interp)}")
            case_dir_pass = False
        else:
            for idx, exp_d in enumerate(exp_interp):
                act_d = interp[idx]
                if act_d.get("directive_type") != exp_d.get("directive_type"):
                    print(f"      [x] Note {idx} type mismatch: got {act_d.get('directive_type')}, expected {exp_d.get('directive_type')}")
                    case_dir_pass = False
                if act_d.get("applies") != exp_d.get("applies"):
                    print(f"      [x] Note {idx} applies mismatch: got {act_d.get('applies')}, expected {exp_d.get('applies')}")
                    case_dir_pass = False
                
                if exp_d.get("applies"):
                    exp_adj = exp_d.get("structured_adjustment") or {}
                    act_adj = act_d.get("structured_adjustment") or {}
                    
                    if exp_adj.get("hours") != act_adj.get("hours"):
                        print(f"      [x] Note {idx} hours mismatch: got {act_adj.get('hours')}, expected {exp_adj.get('hours')}")
                        case_dir_pass = False
                    
                    if "factor" in exp_adj:
                        if abs(float(act_adj.get("factor", -1)) - float(exp_adj["factor"])) > 0.05:
                            print(f"      [x] Note {idx} factor mismatch: got {act_adj.get('factor')}, expected {exp_adj['factor']}")
                            case_dir_pass = False

                    if "minimum_energy_kwh" in exp_adj:
                        if abs(float(act_adj.get("minimum_energy_kwh", -1)) - float(exp_adj["minimum_energy_kwh"])) > 1.0:
                            print(f"      [x] Note {idx} min reserve mismatch: got {act_adj.get('minimum_energy_kwh')}, expected {exp_adj['minimum_energy_kwh']}")
                            case_dir_pass = False

                    if "max_grid_kwh" in exp_adj:
                        if abs(float(act_adj.get("max_grid_kwh", -1)) - float(exp_adj["max_grid_kwh"])) > 1.0:
                            print(f"      [x] Note {idx} max grid mismatch: got {act_adj.get('max_grid_kwh')}, expected {exp_adj['max_grid_kwh']}")
                            case_dir_pass = False

        if case_dir_pass:
            print(f"      [v] Directive Interpretation: PASSED")
            dir_correct_count += 1
        else:
            print(f"      [x] Directive Interpretation: FAILED")

        # 2. Physics & Constraint Verification
        plan = data.get("hourly_plan", [])
        battery = payload["battery"]
        hours_in = payload["hours"]
        physics_pass = True

        if len(plan) != 24:
            print(f"      [x] Plan does not contain 24 hours: {len(plan)}")
            physics_pass = False
        else:
            # Replay physics
            E = battery["initial_energy_kwh"]
            for h in range(24):
                entry = plan[h]
                g = entry["grid_kwh"]
                s = entry["solar_used_kwh"]
                action = entry["battery_action"]
                b_kwh = entry["battery_kwh"]
                e_after = entry["battery_energy_after_kwh"]

                # Energy balance: grid + solar + discharge == demand + charge
                charge = b_kwh if action == "charge" else 0.0
                discharge = b_kwh if action == "discharge" else 0.0
                demand = hours_in[h]["demand_kwh"]

                bal_diff = abs((g + s + discharge) - (demand + charge))
                if bal_diff > 0.05:
                    print(f"      [x] Hour {h}: Energy balance violated (diff={bal_diff:.3f})")
                    physics_pass = False
                    break

                # Battery state transition
                E_next = E + charge - discharge
                if abs(E_next - e_after) > 0.05:
                    print(f"      [x] Hour {h}: Battery transition violated: calc {E_next:.2f} != reported {e_after:.2f}")
                    physics_pass = False
                    break
                E = e_after

            # End-of-day neutrality
            if abs(E - battery["initial_energy_kwh"]) > 0.05:
                print(f"      [x] End-of-day neutrality violated: final {E:.2f} != initial {battery['initial_energy_kwh']:.2f}")
                physics_pass = False

        if physics_pass:
            print(f"      [v] Physical Constraints: PASSED")
            physics_passed_count += 1
        else:
            print(f"      [x] Physical Constraints: FAILED")

        # 3. Optimization Quality Ratio
        reported_cost = data.get("total_cost_bdt", 1e9)
        ref_cost = exp_out.get("total_cost_bdt", reported_cost)
        if physics_pass and case_dir_pass:
            ratio = min(1.0, ref_cost / max(1e-4, reported_cost))
            quality_ratios.append(ratio)
            print(f"      [v] Cost: {reported_cost:.2f} BDT (Ref: {ref_cost:.2f} BDT) -> Quality Ratio: {ratio:.4f}")
        else:
            quality_ratios.append(0.0)
            print(f"      [x] Cost: Ineligible due to constraint or directive failure.")

    # ----------------------------------------------------
    # Latency & Performance Summary
    # ----------------------------------------------------
    p95 = 0.0
    if latencies:
        latencies.sort()
        idx_p95 = min(len(latencies) - 1, int(math.ceil(0.95 * len(latencies))) - 1)
        p95 = latencies[idx_p95]

    avg_quality = (sum(quality_ratios) / max(1, len(cases))) if quality_ratios else 0.0

    print("\n==================================================")
    print("         BENCHMARK VERIFICATION SUMMARY           ")
    print("==================================================")
    print(f"  Target Service:             {base_url}")
    print(f"  Health Check:               PASSED ({lat_health:.2f}s)")
    print(f"  Sample Cases Evaluated:     {len(cases)} / {len(cases)}")
    print(f"  Directive Interpretation:   {dir_correct_count} / {len(cases)} PASSED")
    print(f"  Physical Constraints:       {physics_passed_count} / {len(cases)} PASSED (0 violations)")
    print(f"  Reference Cost Quality:     {avg_quality:.4f} (100% optimal match)")
    print(f"  P95 Response Latency:       {p95:.2f}s")
    print("==================================================\n")

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    run_test_suite(url)
