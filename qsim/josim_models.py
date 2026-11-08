"""Strict, solver-free JoSIM request normalization and admission boundary."""

import hashlib
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

    def _certified_allocation_plan(self) -> AllocationPlan:
        """Plan the fixed patched build; runtime identity and packaging are separate."""
        _, info = validate_josim_netlist(self.netlist)
        rows, traces = info['predicted_rows'], len(info['trace_names'])
        result = plan_result((ArraySpec('times', 'float64', (rows,), 's'),
                              ArraySpec('traces', 'float64', (rows, traces), None)),
                             metadata_characters=65536, summary_bytes=65536)
        # Fixed maximum native shapes include growth, retry and copy overlap.
        # The source audit covers phase analysis, startup and disabled FIR only.
        d, selected, native_rows, devices, source_bytes = 80, 24, 4096, 32, 65536
        factor_units = 2*d*(d+1)
        grown_units = (6*factor_units + 4)//5 + 2*d + 1
        numeric = 2*grown_units*8 + 16*(d*d+1) + 8*27*d + 1024
        ordering = 8*(5*d*d + 21*d + 2)
        base64_bytes = 4*((result.npz_upper_bytes+2)//3)
        items = (
            ('history', 8*(3*d + 3*selected*native_rows + native_rows) + d*32 + 512),
            ('output_and_indices', 8*10*native_rows + 512),
            ('source_tokens_and_temporaries', 4*(36*source_bytes + 2*256*24)),
            ('all_source_vectors_and_copy_overlap', 2*devices*2*64*8),
            ('device_matrix_vectors_and_copy_overlap', 4*3*d*d*8*(devices+1)),
            ('klu_factor_retry_reallocation_and_ordering', 2*(numeric + ordering)),
            ('rhs_with_sentinel_and_copy_overlap', 4*(d+1)*8),
            ('native_import_reserve', 512*1024**2),
            # Payload, decoded ASCII and StringIO's UCS4 buffer overlap.
            ('csv_payload_and_text', 6*6*1024**2),
            ('parsed_python_rows', native_rows*(256 + 32*9)),
            # Conversion matrix and separately copied result arrays coexist.
            ('numeric_result_arrays', 2*result.numeric_bytes),
            ('metadata_unicode_utf8_and_scalar', 3*4*65536 + 65536),
            ('npz_serialization_buffers', 4*result.npz_upper_bytes),
            ('base64_bytes_and_ascii_string', 2*base64_bytes),
            ('callback_json_unicode_and_utf8', 5*result.callback_upper_bytes),
            ('parent_child_handoff', 2*result.callback_upper_bytes),
            # Generated text/table and certificate-copy owners fit this fixed
            # envelope, including the native token increase exactly once.
            ('native_projection_envelope', 1024**2),
        )
        peak = sum(size for _, size in items)
        if peak > 2*1024**3:
            raise ValueError('resource_limit: certified JoSIM allocation exceeds the CPU budget')
        # Fresh nested dictionaries prevent a caller changing subsequent plans.
        certificate = {
            'projection_revision': 'nodev-positional-sign-positive-zero-v1',
            'native_input_bytes': 65568, 'projection_json_bytes': 2048,
            'native_netlist_sha256': hashlib.sha256(info['native_netlist'].encode('ascii')).hexdigest(),
            'trace_projection': info['trace_projection'],
            'binary_sha256': '0bca2802d5e152a1b4325cc4a9f4e8e5b01331d705d45ea757c72e0cb1afe676',
            'source_commit': '02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224',
            'patch_sha256': 'eb727347b5933f7bb42c983c26ec63098e51d0d5a43fa7ceb865bfa28ce768ae',
            'patched_source_sha256': {
                'include/JoSIM/Simulation.hpp': '52be5d18624dab8a5311d29412152a427ba4c6b1528c37fd6ba9accafc7986e4',
                'include/JoSIM/Transient.hpp': '6633e999427b540fd055e823a5323bb86c4378311b373fe1f40d3e07bffcfedd',
                'src/Output.cpp': 'e15afbe9799edb873f988bbe090028dbca1013dd5e2cb039483a40d53066ba13',
                'src/Simulation.cpp': '43b309c8861369558b6722cc6d6324da1121c23b89db7a038907a7c309a1707c',
            },
            'build_image': 'python@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0',
            'source_date_epoch': 1792989136,
            'compiler': 'g++ (Debian 12.2.0-14+deb12u1) 12.2.0',
            'build_type': 'Release -O3 -DNDEBUG',
            'phase_analysis': True, 'startup': True,
            'print_start_seconds': 0, 'fir_window_seconds': 0,
            'cpu_count': 2, 'scheduler_memory_bytes': 4*1024**3,
            'child_address_space_bytes': 3*1024**3, 'child_file_bytes': 12*1024**2,
        }
        return AllocationPlan(
            'josim-cpu-bookworm-nodev-v1', 'josim-phase-bounded-history-nodev-v1',
            {'output_rows_upper_bound': rows, 'trace_count': traces,
             'device_count': info['device_count'], 'node_count': info['node_count'],
             'native_dimension_upper_bound': d, 'selected_variables_upper_bound': selected},
            items, peak, certificate, result)

    def admission_plan(self) -> AllocationPlan:
        # Pure planning cannot verify the runtime executable or worker image.
        self._certified_allocation_plan()
        raise ValueError('resource_limit: JoSIM native workspace certificate is unavailable; '
                         'production worker packaging and activation are pending')
