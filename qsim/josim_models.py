"""Strict, solver-free JoSIM request normalization and admission boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .josim_validation import _normalize_josim_netlist, validate_josim_netlist
from .physics_results import AllocationPlan, ArraySpec, plan_result


__all__ = ('JoSIMRequest',)


class JoSIMRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    engine: Literal['josim'] = 'josim'
    input_format: Literal['josim_netlist'] = 'josim_netlist'
    schema_version: int = Field(default=1, ge=1, le=1)
    operation: Literal['transient'] = 'transient'
    name: str = Field(default='Untitled simulation', min_length=1, max_length=80)
    netlist: str = Field(min_length=1, max_length=65536, description=(
        'ASCII self-contained R/L/B and numeric DC/PWL transient netlist. '
        'Semantic validation additionally checks the positive statement subset, '
        'numeric units, device/model/trace references, finite values and bounded output.'))

    @field_validator('netlist')
    @classmethod
    def _normalized_netlist(cls, value: str) -> str:
        return _normalize_josim_netlist(value)

    def canonical_envelope(self) -> dict:
        return self.model_dump(mode='json')

    def admission_plan(self) -> AllocationPlan:
        _, info = validate_josim_netlist(self.netlist)
        rows, traces = info['predicted_rows'], len(info['trace_names'])
        plan_result((ArraySpec('times', 'float64', (rows,), 's'),
                     ArraySpec('traces', 'float64', (rows, traces), None)),
                    metadata_characters=65536, summary_bytes=65536)
        # Structural/result bounds do not certify a native solver workspace.
        # The combined-image certificate must precede public execution admission.
        raise ValueError('resource_limit: JoSIM native workspace certificate is unavailable')
