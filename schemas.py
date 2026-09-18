from typing import List, Optional, Literal, Union, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator

class HourInput(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Unique integer from 0 to 23")
    demand_kwh: float = Field(..., ge=0, description="Campus demand that must be supplied in this hour")
    solar_kwh: float = Field(..., ge=0, description="Base solar energy available before adjustments")
    tariff_bdt_per_kwh: float = Field(..., ge=0, description="Grid electricity price for this hour")

class BatteryInput(BaseModel):
    capacity_kwh: float = Field(..., gt=0, description="Maximum energy the battery can store")
    initial_energy_kwh: float = Field(..., ge=0, description="Battery energy at the start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0, description="Base reserve level the battery must never go below")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Maximum energy that may be added in one hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Maximum energy that may be removed in one hour")

class OptimizeRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique synthetic scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly entries")
    battery: BatteryInput

    @field_validator("hours")
    def validate_hours_sequence(cls, v):
        hours = [h.hour for h in v]
        if hours != list(range(24)):
            raise ValueError("hours array must contain exactly 24 entries with hour 0 through 23 in order")
        return v

class StructuredAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Unique whole-hour integers in ascending order (0..23)")
    factor: Optional[float] = Field(None, ge=0.0, le=1.0, description="Usable solar fraction remaining (0..1)")
    minimum_energy_kwh: Optional[float] = Field(None, ge=0.0, description="Minimum battery reserve in kWh")
    max_grid_kwh: Optional[float] = Field(None, ge=0.0, description="Maximum grid import in kWh")

    @field_validator("hours")
    def validate_hours_list(cls, v):
        if not v:
            raise ValueError("hours array cannot be empty")
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(f"Hour {h} must be between 0 and 23")
        if v != sorted(list(set(v))):
            raise ValueError("hours must contain unique integers in ascending order")
        return v

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
]

class DirectiveInterpretationEntry(BaseModel):
    note_index: int = Field(..., ge=0, description="Zero-based index of the corresponding operator_notes entry")
    applies: bool = Field(..., description="true for applicable directives, false only for no_op")
    directive_type: DirectiveType = Field(..., description="One supported directive type")
    structured_adjustment: Optional[Dict[str, Any]] = Field(None, description="Exact machine-checkable adjustment object or null for no_op")
    explanation: str = Field(..., description="Short explanation of the interpretation")

    @model_validator(mode="after")
    def validate_applies_semantics(self):
        if self.directive_type == "no_op":
            if self.applies is not False:
                raise ValueError("applies must be false when directive_type is no_op")
            if self.structured_adjustment is not None:
                raise ValueError("structured_adjustment must be null when directive_type is no_op")
        else:
            if self.applies is not True:
                raise ValueError(f"applies must be true for directive_type '{self.directive_type}'")
            if self.structured_adjustment is None:
                raise ValueError(f"structured_adjustment must not be null for directive_type '{self.directive_type}'")
        return self

class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0.0)
    solar_used_kwh: float = Field(..., ge=0.0)
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float = Field(..., ge=0.0)
    battery_energy_after_kwh: float = Field(..., ge=0.0)

class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretationEntry]
    hourly_plan: List[HourlyPlanEntry] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
