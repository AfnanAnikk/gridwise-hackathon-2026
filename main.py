import logging
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from schemas import (
    OptimizeRequest,
    OptimizeResponse,
    HealthResponse
)
from interpreter import interpret_operator_notes
from guardrails import guardrail_directives
from optimizer import solve_energy_schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise-api")

app = FastAPI(
    title="GridWise Campus Energy Optimization API",
    description="LLM-Assisted Smart Campus Energy Scheduling & Optimization for BUP CSE Fest 2026",
    version="1.0.0"
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Malformed JSON or structurally invalid request", "errors": exc.errors()}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Controlled internal error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Controlled internal error. Please check request format and constraints."}
    )

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Readiness endpoint for the judging harness."""
    return HealthResponse(status="ok")

@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(payload: OptimizeRequest):
    """
    Main LLM interpretation + 24-hour microgrid optimization endpoint.
    Pipeline:
      1. Interpret operator notes using language model.
      2. Apply deterministic guardrails & normalization.
      3. Solve 24-hour cost minimization via Linear Programming.
      4. Format response matching canonical schema.
    """
    try:
        # Step 1: Interpret operator notes
        raw_directives = interpret_operator_notes(payload.operator_notes, battery=payload.battery)

        # Step 2: Pass through deterministic guardrails
        validated_directives = guardrail_directives(
            raw_directives=raw_directives,
            num_notes=len(payload.operator_notes),
            battery=payload.battery
        )

        # Step 3: Run Linear Programming optimizer
        hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh = solve_energy_schedule(
            request=payload,
            directives=validated_directives
        )

        # Step 4: Generate concise plan summary
        applied_types = [d.directive_type for d in validated_directives if d.applies]
        if applied_types:
            summary_text = (
                f"Successfully scheduled 24h campus load with {len(applied_types)} applied directive(s) "
                f"({', '.join(applied_types)}). Minimized peak tariff imports and preserved battery neutrality."
            )
        else:
            summary_text = (
                "Standard 24h cost minimization applied. Solar prioritized, battery shifted to off-peak hours, "
                "and end-of-day battery neutrality preserved."
            )

        return OptimizeResponse(
            scenario_id=payload.scenario_id,
            directive_interpretation=validated_directives,
            hourly_plan=hourly_plan,
            total_grid_kwh=total_grid_kwh,
            total_cost_bdt=total_cost_bdt,
            peak_grid_kwh=peak_grid_kwh,
            plan_summary=summary_text
        )

    except Exception as e:
        logger.error(f"Error processing scenario {payload.scenario_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Controlled internal error: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
