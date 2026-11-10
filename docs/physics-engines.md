# Physics model API

Physics routes are implemented for the CPU matrix below. Production activation is gated by exact-release acceptance. Check authenticated `GET /api/capabilities` on your deployment; an omitted engine is unavailable, and new physics submissions return `503` while activation is off. A source merge or image build does not establish production availability.

Use Lindblad dynamics to follow states, dissipation and drives; scqubits to build selected superconducting-qubit spectra/operators; or JoSIM to study superconducting circuit transients. These sit beside the existing Clifft, Google qsim and Aer circuit APIs and share their access keys and durable job lifecycle. All physical-model operations in this first matrix run on classical CPUs in float64/complex128.

| Engine | Submit | Generic input format | Authenticated schema |
| --- | --- | --- | --- |
| QuTiP 5.3.1 Lindblad | `POST /api/jobs/lindblad` | `lindblad_model` | `GET /api/schemas/lindblad/1.json` |
| scqubits 4.3.1 | `POST /api/jobs/scqubits` | `scqubits_model` | `GET /api/schemas/scqubits/1.json` |
| Patched JoSIM v2.7 | `POST /api/jobs/josim` | `josim_netlist` | `GET /api/schemas/josim/1.json` |

## Start with a schema and a saved request

Use `Authorization: Bearer <API_KEY>`, `Content-Type: application/json` and a persisted `Idempotency-Key` for each explicit submission. `schema_version` is the integer `1`, not a string or boolean. Only documented JSON fields are accepted; shots, executable Python, symbolic coefficient strings, arbitrary native options, resources and backend selectors are not physics fields.

The generic `POST /api/jobs` accepts the same normalized bodies with the matching `engine`/`input_format`. Its idempotency header remains optional for compatibility; without it every call creates new work. Explicit and generic routes replay the same owner-scoped reservation when the normalized body/key match. Changed bodies conflict with `409`. A matching retry retains the admitted job/profile even after current image or admission policy changes. A new key explicitly requests new work; retrying does not restart failed/cancelled jobs.

Acceptance returns `202`, the job ID, `Location` and `Idempotency-Replayed`. Follow `GET /api/jobs/{id}` or authenticated `GET /api/jobs/{id}/events`; resume SSE after the last processed `Last-Event-ID`. SSE carries status and the final summary, not incremental trajectories, traces or samples. Download the completed NPZ from `GET /api/jobs/{id}/result`, using `numpy.load(..., allow_pickle=False)`. Arrays are numeric and `metadata_json` is a scalar Unicode JSON string describing units, ordering, versions, actual backend and work counters.

Each call is independent. Your client sequences exports, optimizers, sweeps, batches and state handoffs. wqsim does not maintain a persistent quantum session or an implicit batch graph. Cancellation and deletion use the existing job endpoints; deletion waits for terminal cleanup. Complete results are returned or the job fails, without silent output truncation or precision fallback.

Fetch a schema with the same bearer key:

```sh
BASE=https://simulation.example.invalid
curl --fail-with-body "$BASE/api/schemas/lindblad/1.json" \
  -H "Authorization: Bearer $QSIM_API_KEY" -o lindblad-v1.json

# Save one JSON example below as request.json and persist this key with that file.
KEY=$(uuidgen)
printf '%s\n' "$KEY" > request.idempotency-key
curl --fail-with-body "$BASE/api/jobs/lindblad" \
  -H "Authorization: Bearer $QSIM_API_KEY" \
  -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' \
  --data-binary @request.json
```

A structural schema cannot establish memory admission or all physical conditions. The API also validates the model and its certified native execution plan.

## Lindblad dynamics and static models

| Operation | Solver | Use it for | Numeric NPZ arrays |
| --- | --- | --- | --- |
| `evolve` | `se` | Pure-state Schrödinger evolution without dissipation | `times`, optional `expectations`, optional `state_times`/`states` |
| `evolve` | `me` | Lindblad density-matrix evolution with or without collapse channels | Same names; retained `states` are density matrices |
| `evolve` | `mc` | Seeded serial quantum-jump trajectories with a pure input and nonzero collapse | Same names; retained `states` are ensemble density matrices |
| `propagator` | `se` | Hilbert-space propagators | `times`, `propagators` |
| `propagator` | `me` | Density/Liouville propagators | `times`, `propagators` |
| `spectrum` | Omit solver | Static Hamiltonian energies and optional eigenvectors | `eigenvalues`, optional `eigenvectors` |
| `steadystate` | Omit solver | A unique stationary density matrix and optional observables | Optional `steady_state`, optional `expectations` |

Time is in **ns**. Frequencies, Kerr values and Hamiltonian entries are linear **GHz**; the service applies the `2*pi` conversion to its angular-frequency Hamiltonian once. Phases are radians, collapse `rate` is in `1/ns`, and `T1`/`Tphi` are in ns. Complex values use finite numbers or `{"re": ..., "im": ...}` objects. Tensor order follows `model.modes`.

Modes may be `oscillator`, `duffing` or an explicit Hermitian `matrix`. Static terms and drives reference named matrices or product operators (`identity`, `a`, `adag`, `n`, `x`, `y`, or `proj:i,j`). Drive envelopes are `constant`, `square`, `gaussian`, `flattop_cosine` or linearly interpolated `sampled` values, with numeric controls. DRAG and positive `square.rise` are not supported in schema v1. Density inputs must have trace one and be Hermitian/positive semidefinite within the documented numerical checks; invalid densities are not clipped into validity.

For dynamics, `solver` defaults to `se`; use `me` for density/collapse evolution and `mc` for a pure input with at least one nonzero collapse channel. `seed` defaults to 42 and applies only to MC, as does `trajectories`. Same-image repeated seeds reproduce the tested trajectory/ensemble result; cross-version/backend bitwise identity is not promised. ME with no collapse still returns density matrices, rather than switching to SE. Static spectrum rejects drives/collapse; steady state rejects a nonunique or numerically ambiguous stationary solution.

With T requested times, O observables, D total Hilbert dimension and R retained states: expectations have shape `[T,O]`; SE states `[R,D]`; ME/MC states `[R,D,D]`. `store_states` is `none` (default), `final` or `all`, with matching `state_times`. Propagators are `[T,D,D]` for SE or `[T,D*D,D*D]` for ME and are identity at the first requested time. ME propagators act on column-stacked density matrices. Spectrum returns K energies and optional `[D,K]` vectors. Steady-state expectations have shape `[O]`, and its optional density is `[D,D]`. Operation-inapplicable arrays/controls are absent.

### Schrödinger evolution

The number expectation stays one; the excited amplitude acquires phase `exp(-2*pi*i*0.017*t)`.

<!-- example: physics-lindblad-evolve-se -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "evolve",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ]
  },
  "solver": "se",
  "times": [
    0.0,
    0.5,
    1.0
  ],
  "initial_state": {
    "kind": "product",
    "modes": {
      "q": {
        "kind": "fock",
        "index": 1
      }
    }
  },
  "observables": [
    {
      "name": "n",
      "operator": {
        "kind": "product",
        "factors": {
          "q": "n"
        }
      }
    }
  ],
  "store_states": "all"
}
```

### Dissipative density evolution

The excited population decays as `exp(-t/3)`.

<!-- example: physics-lindblad-evolve-me -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "evolve",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ],
    "collapse": [
      {
        "kind": "relaxation",
        "mode": "q",
        "T1": 3.0
      }
    ]
  },
  "solver": "me",
  "times": [
    0.0,
    0.5,
    1.0
  ],
  "initial_state": {
    "kind": "product",
    "modes": {
      "q": {
        "kind": "fock",
        "index": 1
      }
    }
  },
  "observables": [
    {
      "name": "n",
      "operator": {
        "kind": "product",
        "factors": {
          "q": "n"
        }
      }
    }
  ],
  "store_states": "all"
}
```

### Seeded Monte Carlo evolution

This repeats deterministically on the same pinned image with seed 42. Eight trajectories are an example choice, not a convergence guarantee.

<!-- example: physics-lindblad-evolve-mc -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "evolve",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ],
    "collapse": [
      {
        "kind": "relaxation",
        "mode": "q",
        "T1": 3.0
      }
    ]
  },
  "solver": "mc",
  "times": [
    0.0,
    0.5,
    1.0
  ],
  "initial_state": {
    "kind": "product",
    "modes": {
      "q": {
        "kind": "fock",
        "index": 1
      }
    }
  },
  "observables": [
    {
      "name": "n",
      "operator": {
        "kind": "product",
        "factors": {
          "q": "n"
        }
      }
    }
  ],
  "store_states": "all",
  "trajectories": 8,
  "seed": 42
}
```

### SE propagators

<!-- example: physics-lindblad-propagator-se -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "propagator",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ]
  },
  "solver": "se",
  "times": [
    0.0,
    0.5,
    1.0
  ]
}
```

### ME propagators

<!-- example: physics-lindblad-propagator-me -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "propagator",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ],
    "collapse": [
      {
        "kind": "relaxation",
        "mode": "q",
        "T1": 3.0
      }
    ]
  },
  "solver": "me",
  "times": [
    0.0,
    0.5,
    1.0
  ]
}
```

### Static spectrum

The two energies are `[0, 0.017]` GHz.

<!-- example: physics-lindblad-spectrum -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "spectrum",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ]
  },
  "evals_count": 2,
  "return_vectors": true
}
```

### Unique steady state

Relaxation produces the ground density `[[1,0],[0,0]]`.

<!-- example: physics-lindblad-steadystate -->
```json
{
  "engine": "lindblad",
  "input_format": "lindblad_model",
  "schema_version": 1,
  "operation": "steadystate",
  "model": {
    "modes": [
      {
        "name": "q",
        "kind": "oscillator",
        "dimension": 2,
        "frequency": 0.017
      }
    ],
    "collapse": [
      {
        "kind": "relaxation",
        "mode": "q",
        "T1": 3.0
      }
    ]
  },
  "return_state": true,
  "observables": [
    {
      "name": "n",
      "operator": {
        "kind": "product",
        "factors": {
          "q": "n"
        }
      }
    }
  ]
}
```

## Superconducting qubit models with scqubits

Supported classes are `Transmon`, `TunableTransmon`, `Fluxonium` and `FluxQubit`. Supply their strict numeric constructor parameters, not arbitrary upstream constructor arguments. Energies are in GHz, charge offsets and flux fractions are dimensionless. Cutoff convergence is the client's responsibility.

| Operation | Controls | Result |
| --- | --- | --- |
| `spectrum` | `evals_count`, optional `return_vectors` | `eigenvalues` in ascending GHz; optional complex `[raw_basis,levels]` `eigenvectors` |
| `matrix_elements` | `evals_count`, one `operator` | GHz `eigenvalues` plus complex `[levels,levels]` `matrix_elements` |
| `export_for_lindblad` | `evals_count`, ordered `operators` list, which may be empty | Ground-shifted GHz `eigenvalues`, complex diagonal `hamiltonian`, one array per selected operator, and `metadata_json.lindblad_request` |

Transmon/TunableTransmon operators: `n_operator`, `cos_phi_operator`, `sin_phi_operator`. Fluxonium also offers `phi_operator`. FluxQubit offers `n_1_operator`, `n_2_operator`, `cos_phi_1_operator`, `cos_phi_2_operator`, `sin_phi_1_operator`, `sin_phi_2_operator`. Each matrix is transformed using the same native eigensystem. Vector phases and bases inside degenerate subspaces are not unique; compare physical/projector quantities when appropriate.

### Spectrum and eigenvectors

<!-- example: physics-scqubits-spectrum -->
```json
{
  "engine": "scqubits",
  "input_format": "scqubits_model",
  "schema_version": 1,
  "operation": "spectrum",
  "evals_count": 4,
  "model": {
    "qubit": "Transmon",
    "params": {
      "EJ": 15.0,
      "EC": 0.3,
      "ng": 0.0,
      "ncut": 30
    }
  },
  "return_vectors": true
}
```

### Matrix elements

<!-- example: physics-scqubits-matrix-elements -->
```json
{
  "engine": "scqubits",
  "input_format": "scqubits_model",
  "schema_version": 1,
  "operation": "matrix_elements",
  "evals_count": 4,
  "model": {
    "qubit": "Transmon",
    "params": {
      "EJ": 15.0,
      "EC": 0.3,
      "ng": 0.0,
      "ncut": 30
    }
  },
  "operator": "n_operator"
}
```

### Numeric export and client handoff

<!-- example: physics-scqubits-export -->
```json
{
  "engine": "scqubits",
  "input_format": "scqubits_model",
  "schema_version": 1,
  "operation": "export_for_lindblad",
  "evals_count": 4,
  "model": {
    "qubit": "Transmon",
    "params": {
      "EJ": 15.0,
      "EC": 0.3,
      "ng": 0.0,
      "ncut": 30
    }
  },
  "operators": [
    "n_operator"
  ]
}
```

Download this result, parse `metadata_json`, and submit its `lindblad_request` as a **new** Lindblad job with a new idempotency key. The returned Hamiltonian/operators are numeric data in the selected energy eigenbasis. Ground energy is removed and recorded separately. Copy them without multiplying by `2*pi`; the Lindblad API performs that conversion. Operators retain dimensionless units, except `phi_operator` in radians. Do not synthesize a new diagonal basis independently and reuse old operator matrices.

```python
import io, json
import numpy as np

# artifact_bytes is the authenticated NPZ download from the export job.
with np.load(io.BytesIO(artifact_bytes), allow_pickle=False) as result:
    metadata = json.loads(str(result["metadata_json"]))
    next_request = metadata["lindblad_request"]
# Persist a fresh key and POST next_request to /api/jobs/lindblad.
```

The export's duplicated JSON matrix metadata has its own 64-KiB bound, so a raw basis/level count accepted for a spectrum may be too large for an export.

## Superconducting transients with JoSIM

This endpoint uses the pinned **JoSIM v2.7** implementation with a verified bounded-history patch. It accepts a positive self-contained subset: R/L passive devices, Josephson B devices with numeric `AREA`, numeric DC/PWL voltage/current sources, one numeric JJ model (`RTYPE=1`), one `.TRAN`, explicit `.PRINT DEVV`, `.PRINT DEVI` or `.PRINT PHASE`, and a terminal `.END`. Unsupported SPICE features fail before enqueue, including file includes/output paths, subcircuits, expressions, external files, noise controls and arbitrary simulator options.

Engineering suffixes are accepted by the numeric parser. Output `times` are seconds and `traces` are volts, amperes or radians according to the corresponding `trace_names`/`trace_units` metadata. Preserve declared trace order and array rows; adaptive integration can return fewer rows than the reserved ceiling, without padding. Input time suffixes such as `p` are converted to SI. This API does not expose every feature of current upstream JoSIM.

### SFQ pulse fixture

The fixed upstream MIT-licensed JTL example below gives one SFQ voltage-pulse integral within 2% of `2.0678338484619295e-15 V*s` on the tested B01/B02 windows. The JSON includes its source/license notice.

<!-- example: physics-josim-sfq -->
```json
{
  "engine": "josim",
  "input_format": "josim_netlist",
  "schema_version": 1,
  "operation": "transient",
  "netlist": "* Source: https://github.com/JoeyDelp/JoSIM/blob/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224/test/ex_jtl_basic.cir\n* MIT License\n* \n* Copyright (c) 2020 Johannes Delport\n* \n* Permission is hereby granted, free of charge, to any person obtaining a copy\n* of this software and associated documentation files (the \"Software\"), to deal\n* in the Software without restriction, including without limitation the rights\n* to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n* copies of the Software, and to permit persons to whom the Software is\n* furnished to do so, subject to the following conditions:\n* \n* The above copyright notice and this permission notice shall be included in all\n* copies or substantial portions of the Software.\n* \n* THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n* AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n* OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n* SOFTWARE.\n* Fixed single-pulse JTL fixture adapted from JoSIM v2.7 ex_jtl_basic (MIT).\nB01        3          7          jmitll     area=2.16\nB02        6          8          jmitll     area=2.16\nIB01       0          1          pwl(0      0 5p 280u)\nL01        4          3          2p        \nL02        3          2          2.425p    \nL03        2          6          2.425p    \nL04        6          5          2.031p    \nLP01       0          7          0.086p    \nLP02       0          8          0.096p    \nLPR01      2          1          0.278p    \nLRB01      7          9          0.086p    \nLRB02      8          10         0.086p    \nRB01       9          3          5.23      \nRB02       10         6          5.23      \nROUT       5          0          2         \nVIN        4          0          pwl(0 0 100p 0 102.5p 827.13u 105p 0)\n.model jmitll jj(rtype=1, vg=2.8mV, cap=0.07pF, r0=160, rN=16, icrit=0.1mA)\n.tran 0.25p 250p\n.print DEVV B01\n.print PHASE B01\n.print DEVV B02\n.print PHASE B02\n.print DEVI ROUT\n.end\n"
}
```

## Admission and output limits

The CPU profile requests 2 CPUs and 4 GiB, with a fresh nonroot numerical child per job, a 3-GiB address-space limit and 12-MiB output-file limit. Physics native threads are serial. Admission accounts for native workspace and retained inputs/results before execution; charged allocation must fit 2 GiB, with 512 MiB reserved within that charge for imports/native overhead. Passing a dimension ceiling alone does not guarantee admission.

| Boundary | Current schema-v1 envelope |
| --- | --- |
| HTTP body / name | 16 MiB / 1–80 characters; physics name defaults to `Untitled simulation` |
| Lindblad model | At most 8 ordered modes, total Hilbert dimension 256; dense ME/steady-state dimension at most 32 |
| Lindblad controls | At most 256 static terms, 32 named matrices, 32 observables, 16 effective collapse channels and 16 scalar drive channels |
| Lindblad grids / MC | At most 65,536 requested times/combined coefficient knots; 1–1,000 serial MC trajectories |
| scqubits | Raw basis at most 512, returned levels at most 32; export JSON/array limits may admit fewer levels |
| JoSIM | 1–65,536 ASCII bytes, 256 logical statements, 32 devices, 16 node identifiers, at most 8 unique selected traces and 4,096 predicted output rows |
| JoSIM transient | `0 < step <= stop <= 1e-6` seconds; DC/PWL sources, at most 64 PWL time/value pairs |
| Physics output | At most 6 MiB numeric arrays, 64 KiB UTF-8 metadata, planned NPZ at most 9 MiB and complete callback at most 12 MiB |

There is no new elapsed-time/CPU-time deadline, global job cap or client resource override. Jobs queue when compatible capacity is unavailable. GPU execution is not enabled for this release. Installed CUDA packages or a GPU present in the cluster do not change that matrix.

## Failure handling

Structural, finite-value, physical and allocation refusals return `422` before creating a job. New physics errors include `detail[].pointer`, an escaped RFC 6901 JSON Pointer; an empty pointer refers to the whole request. `413` rejects an oversized HTTP body. `401`/`404` retain authentication and owner isolation. `503` means physics is not enabled, release maintenance holds new work, or service configuration is unavailable. Check the response before deciding whether to retry.

After acceptance, inspect terminal status/error. Bounded prefixes include `physical_validation`, `invalid_model`, `resource_limit`, `execution_policy_mismatch`, `native_nonconvergence`, `nonunique_stationary_state`, `invalid_result`, `native_failure` and `execution_failed`. A solver failure does not produce partial success. For an otherwise nonterminal job, acknowledged native completion without a committed final callback becomes a bounded failure instead of silently rerunning computation. Existing terminal states remain immutable. `202` alone is not successful simulation.

## Upstream references

The WQS schemas define the accepted subset. Upstream projects provide broader concepts and APIs:
[QuTiP documentation](https://qutip.readthedocs.io/en/stable/),
[scqubits documentation](https://scqubits.readthedocs.io/en/latest/),
and [JoSIM documentation](https://joeydelp.github.io/JoSIM/).
Thanks to their maintainers and contributors. Use the pinned WQS capabilities and schema when preparing a service request.
