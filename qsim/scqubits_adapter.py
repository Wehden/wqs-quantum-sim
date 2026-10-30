"""CPU scqubits numerics for isolated execution, gated by certified admission."""
from collections.abc import Mapping
import hashlib

import numpy as np

from .physics_results import AllocationPlan, PhysicsExecutionResult
from .scqubits_models import ScqubitsRequest, _OPERATORS, _CPU_CERTIFICATE

__all__ = ('run_scqubits',)


def _verify_runtime_identity():
    import scipy
    import scqubits
    import qutip

    packages = {'numpy': np, 'scipy': scipy, 'scqubits': scqubits, 'qutip': qutip}
    try:
        if any(package.__version__ != _CPU_CERTIFICATE['versions'][name]
               for name, package in packages.items()):
            raise ValueError('version')
        roots = {package.__file__.rsplit('/', 2)[0] for package in packages.values()}
        if len(roots) != 1:
            raise ValueError('package root')
        root = roots.pop()
        for group in ('source_hashes', 'native_hashes'):
            for relative, expected in _CPU_CERTIFICATE[group].items():
                digest = hashlib.sha256()
                remaining = expected['size']
                with open(root + '/' + relative, 'rb') as stream:
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError('short runtime file')
                        remaining -= len(chunk)
                        digest.update(chunk)
                    if stream.read(1):
                        raise ValueError('extra runtime bytes')
                if digest.hexdigest() != expected['sha256']:
                    raise ValueError('runtime digest')
    except (OSError, ValueError, AttributeError, TypeError):
        raise ValueError('execution_policy_mismatch: scqubits runtime identity') from None


def _native_constructor(request: ScqubitsRequest):
    dimension = request._physical_checks()
    request._result_plan(dimension)
    import scqubits as scq

    if scq.__version__ != '4.3.1':
        raise ValueError('execution_policy_mismatch: scqubits version')
    p = request.model.params
    options = {'truncated_dim': request.evals_count, 'esys_method': 'esys_scipy_dense',
               'evals_method': 'evals_scipy_dense'}
    if request.model.qubit == 'Transmon':
        native = scq.Transmon(EJ=p.EJ, EC=p.EC, ng=p.ng, ncut=p.ncut, **options)
    elif request.model.qubit == 'TunableTransmon':
        native = scq.TunableTransmon(EJmax=p.EJmax, EC=p.EC, d=p.d, flux=p.flux,
                                    ng=p.ng, ncut=p.ncut, **options)
    elif request.model.qubit == 'Fluxonium':
        native = scq.Fluxonium(EJ=p.EJ, EC=p.EC, EL=p.EL, flux=p.flux,
                              cutoff=p.cutoff, **options)
    elif request.model.qubit == 'FluxQubit':
        native = scq.FluxQubit(EJ1=p.EJ1, EJ2=p.EJ2, EJ3=p.EJ3,
                              ECJ1=p.ECJ1, ECJ2=p.ECJ2, ECJ3=p.ECJ3,
                              ECg1=p.ECg1, ECg2=p.ECg2, ng1=p.ng1, ng2=p.ng2,
                              flux=p.flux, ncut=p.ncut, **options)
    else:
        raise ValueError('physical_validation: unsupported scqubits class')
    if native.hilbertdim() != dimension:
        raise ValueError('invalid_result: native basis dimension differs from plan')
    return native


def _eigensystem(native, request: ScqubitsRequest):
    dimension, levels = request._physical_checks(), request.evals_count
    hamiltonian = np.asarray(native.hamiltonian())
    if (hamiltonian.shape != (dimension, dimension)
            or not np.isfinite(hamiltonian).all()):
        raise ValueError('invalid_result: invalid native Hamiltonian')
    scale = max(1., float(np.max(np.abs(hamiltonian))))
    scaled_h = hamiltonian / scale
    if not np.allclose(scaled_h, scaled_h.conj().T, atol=1e-12, rtol=0):
        raise ValueError('invalid_result: non-Hermitian native Hamiltonian')
    energies, vectors = native.eigensys(evals_count=levels)
    if (np.iscomplexobj(energies) or energies.shape != (levels,)
            or vectors.shape != (dimension, levels)
            or not np.isfinite(energies).all() or not np.isfinite(vectors).all()):
        raise ValueError('invalid_result: invalid native eigenpair arrays')
    order = np.argsort(energies, kind='stable')
    energies = np.asarray(energies[order], dtype=np.float64)
    vectors = np.asarray(vectors[:, order], dtype=np.complex128)
    if (np.linalg.norm(vectors.conj().T @ vectors - np.eye(levels), ord=np.inf) > 1e-9
            or np.linalg.norm(scaled_h @ vectors - vectors * (energies / scale),
                              ord=np.inf) > 1e-9):
        raise ValueError('invalid_result: native eigenpair residual or orthogonality')
    return energies, vectors


def _operator_matrices(native, request: ScqubitsRequest, eigensystem):
    if request.operator not in _OPERATORS[request.model.qubit]:
        raise ValueError('physical_validation: unsupported scqubits operator')
    raw = np.asarray(getattr(native, request.operator)())
    dimension = request._physical_checks()
    if raw.shape != (dimension, dimension) or not np.isfinite(raw).all():
        raise ValueError('invalid_result: invalid native operator')
    scale = max(1., float(np.max(np.abs(raw))))
    if not np.allclose(raw / scale, raw.conj().T / scale, atol=1e-12, rtol=0):
        raise ValueError('invalid_result: non-Hermitian native operator')
    vectors = eigensystem[1]
    transformed = np.asarray(vectors.conj().T @ raw @ vectors, dtype=np.complex128)
    if not np.isfinite(transformed).all():
        raise ValueError('invalid_result: nonfinite operator matrix elements')
    return transformed


def _run_native(request: ScqubitsRequest) -> PhysicsExecutionResult:
    """Private numerical stage; callers establish certified admission first."""
    request = ScqubitsRequest.model_validate(request.canonical_envelope())
    if request.operation not in ('spectrum', 'matrix_elements'):
        raise ValueError('physical_validation: scqubits operation not implemented')
    native = _native_constructor(request)
    energies, vectors = _eigensystem(native, request)
    arrays = {'eigenvalues': energies}
    units = {'eigenvalues': 'GHz'}
    if request.operation == 'spectrum' and request.return_vectors:
        arrays['eigenvectors'] = vectors
        units['eigenvectors'] = 'dimensionless'
    elif request.operation == 'matrix_elements':
        arrays['matrix_elements'] = _operator_matrices(native, request, (energies, vectors))
        units['matrix_elements'] = ('radians' if request.operator == 'phi_operator'
                                     else 'dimensionless')
    dimension = request._physical_checks()
    basis = {'native': ('harmonic oscillator' if request.model.qubit == 'Fluxonium'
                         else 'tensor charge' if request.model.qubit == 'FluxQubit' else 'charge'),
             'phase_convention': 'native arbitrary phases',
             'degenerate_vectors': 'arbitrary basis within degenerate subspaces',
             'ordering': 'ascending energy', 'operator_basis': 'same returned eigensystem'}
    metadata = {'engine': 'scqubits', 'engine_version': '4.3.1', 'schema_version': 1,
                'operation': request.operation, 'solver': 'esys_scipy_dense',
                'precision': 'float64/complex128', 'backend': 'cpu',
                'qubit': request.model.qubit, 'parameters': request.model.params.model_dump(),
                'raw_basis_dimension': dimension, 'evals_count': request.evals_count,
                'energy_reference': 'native absolute', 'units': units, 'basis': basis}
    if request.operator is not None:
        metadata['operator'] = request.operator
    work = {'eigensystem_calls': '1', 'raw_basis_dimension': str(dimension),
            'requested_levels': str(request.evals_count),
            'operator_transforms': str(int(request.operation == 'matrix_elements'))}
    return PhysicsExecutionResult(arrays, metadata, work)


def run_scqubits(request: ScqubitsRequest, plan: AllocationPlan, *,
                 execution_context: Mapping) -> PhysicsExecutionResult:
    """Reproduce certified admission before constructing any native object."""
    normalized = ScqubitsRequest.model_validate(request.canonical_envelope())
    reproduced = normalized.admission_plan()
    if not isinstance(plan, AllocationPlan) or plan != reproduced:
        raise ValueError('execution_policy_mismatch: scqubits allocation plan')
    if execution_context.get('backend', 'cpu') != 'cpu':
        raise ValueError('execution_policy_mismatch: unsupported scqubits backend')
    _verify_runtime_identity()
    result = _run_native(normalized)
    return PhysicsExecutionResult(result.arrays,
                                  {**result.metadata, 'policy_revision': plan.policy_revision,
                                   'algorithm': plan.algorithm}, result.work)
