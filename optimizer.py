from typing import List, Tuple
import pulp
from schemas import OptimizeRequest, DirectiveInterpretationEntry, HourlyPlanEntry

def solve_energy_schedule(
    request: OptimizeRequest,
    directives: List[DirectiveInterpretationEntry]
) -> Tuple[List[HourlyPlanEntry], float, float, float]:
    hours = request.hours
    battery = request.battery

    # 1. Compute effective solar and active directive bounds for each hour
    effective_solar = [h.solar_kwh for h in hours]
    min_reserve = [battery.minimum_energy_kwh for _ in range(24)]
    max_charge = [battery.max_charge_kwh_per_hour for _ in range(24)]
    max_discharge = [battery.max_discharge_kwh_per_hour for _ in range(24)]
    max_grid = [float("inf") for _ in range(24)]

    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        adj = d.structured_adjustment
        affected_hours = adj.get("hours", [])

        if d.directive_type == "solar_reduction":
            factor = adj.get("factor", 1.0)
            for h in affected_hours:
                if 0 <= h < 24:
                    effective_solar[h] = effective_solar[h] * factor

        elif d.directive_type == "minimum_battery_reserve":
            req_min = adj.get("minimum_energy_kwh", battery.minimum_energy_kwh)
            for h in affected_hours:
                if 0 <= h < 24:
                    min_reserve[h] = max(min_reserve[h], req_min)

        elif d.directive_type == "no_charge_window":
            for h in affected_hours:
                if 0 <= h < 24:
                    max_charge[h] = 0.0

        elif d.directive_type == "no_discharge_window":
            for h in affected_hours:
                if 0 <= h < 24:
                    max_discharge[h] = 0.0

        elif d.directive_type == "max_grid_window":
            req_max_grid = adj.get("max_grid_kwh", float("inf"))
            for h in affected_hours:
                if 0 <= h < 24:
                    max_grid[h] = min(max_grid[h], req_max_grid)

    # 2. Formulate PuLP Linear Program / MILP
    prob = pulp.LpProblem("SmartCampusEnergyOptimization", pulp.LpMinimize)

    # Decision variables for 24 hours
    grid_vars = [pulp.LpVariable(f"grid_{h}", lowBound=0.0) for h in range(24)]
    solar_used_vars = [pulp.LpVariable(f"solar_used_{h}", lowBound=0.0, upBound=effective_solar[h]) for h in range(24)]
    charge_vars = [pulp.LpVariable(f"charge_{h}", lowBound=0.0) for h in range(24)]
    discharge_vars = [pulp.LpVariable(f"discharge_{h}", lowBound=0.0) for h in range(24)]
    energy_after_vars = [pulp.LpVariable(f"E_after_{h}", lowBound=min_reserve[h], upBound=battery.capacity_kwh) for h in range(24)]
    is_charging_vars = [pulp.LpVariable(f"is_charging_{h}", cat=pulp.LpBinary) for h in range(24)]
    peak_var = pulp.LpVariable("peak_grid", lowBound=0.0)

    # Objective: Minimize total electricity cost from grid with tiny peak penalty for tie-breaking
    prob += pulp.lpSum([grid_vars[h] * hours[h].tariff_bdt_per_kwh for h in range(24)]) + 1e-4 * peak_var

    # Constraints
    for h in range(24):
        demand = hours[h].demand_kwh

        # Energy Balance: grid + solar_used + discharge = demand + charge
        prob += (
            grid_vars[h] + solar_used_vars[h] + discharge_vars[h] == demand + charge_vars[h],
            f"EnergyBalance_{h}"
        )

        # Rate limits and mutual exclusion of charge/discharge
        prob += charge_vars[h] <= max_charge[h] * is_charging_vars[h], f"MaxCharge_{h}"
        prob += discharge_vars[h] <= max_discharge[h] * (1 - is_charging_vars[h]), f"MaxDischarge_{h}"

        # Peak grid tracking
        prob += grid_vars[h] <= peak_var, f"PeakGrid_{h}"

        # Grid ceiling
        if max_grid[h] < float("inf"):
            prob += grid_vars[h] <= max_grid[h], f"MaxGrid_{h}"

        # Battery energy transition: E_after[h] = E_before[h] + charge - discharge
        if h == 0:
            prob += energy_after_vars[0] == battery.initial_energy_kwh + charge_vars[0] - discharge_vars[0], "BatteryTransition_0"
        else:
            prob += energy_after_vars[h] == energy_after_vars[h-1] + charge_vars[h] - discharge_vars[h], f"BatteryTransition_{h}"

    # End of day neutrality: E_after[23] == initial_energy_kwh
    prob += energy_after_vars[23] == battery.initial_energy_kwh, "EndOfDayNeutrality"

    # Solve using default solver silently
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=10)
    prob.solve(solver)

    # 3. Extract solution and build hourly_plan
    hourly_plan: List[HourlyPlanEntry] = []
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0

    for h in range(24):
        g = max(0.0, float(pulp.value(grid_vars[h]) or 0.0))
        s = max(0.0, min(effective_solar[h], float(pulp.value(solar_used_vars[h]) or 0.0)))
        c = max(0.0, float(pulp.value(charge_vars[h]) or 0.0))
        d = max(0.0, float(pulp.value(discharge_vars[h]) or 0.0))
        e = max(0.0, float(pulp.value(energy_after_vars[h]) or 0.0))

        # Determine discrete battery action
        if c > 1e-4:
            action = "charge"
            bat_kwh = c
        elif d > 1e-4:
            action = "discharge"
            bat_kwh = d
        else:
            action = "idle"
            bat_kwh = 0.0

        # Maintain exact precision
        g = round(g, 4)
        s = round(s, 4)
        bat_kwh = round(bat_kwh, 4)
        e = round(e, 4)

        hourly_plan.append(HourlyPlanEntry(
            hour=h,
            grid_kwh=g,
            solar_used_kwh=s,
            battery_action=action,
            battery_kwh=bat_kwh,
            battery_energy_after_kwh=e
        ))

        total_grid_kwh += g
        total_cost_bdt += g * hours[h].tariff_bdt_per_kwh
        if g > peak_grid_kwh:
            peak_grid_kwh = g

    total_grid_kwh = round(total_grid_kwh, 4)
    total_cost_bdt = round(total_cost_bdt, 4)
    peak_grid_kwh = round(peak_grid_kwh, 4)

    return hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh
