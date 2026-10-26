"""Fixed scqubits discovery fixtures; no API or runtime integration.

Run in the constrained container described in discovery/scqubits.md. The probe
sets child limits before native imports and never accepts caller model data.
"""
import argparse
import importlib.metadata
import io
import json
import resource
import time

resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))

import numpy as np
import qutip as qt
import scqubits as scq


FIXTURES = {
    'Transmon': ({'EJ': 15.0, 'EC': 0.3, 'ng': 0.0, 'ncut': 30},
                 ('n_operator', 'cos_phi_operator', 'sin_phi_operator')),
    'TunableTransmon': ({'EJmax': 20.0, 'EC': 0.3, 'd': 0.01, 'flux': 0.2,
                        'ng': 0.0, 'ncut': 30},
                       ('n_operator', 'cos_phi_operator', 'sin_phi_operator')),
    'Fluxonium': ({'EJ': 8.9, 'EC': 2.5, 'EL': 0.5, 'flux': 0.3, 'cutoff': 110},
                  ('n_operator', 'phi_operator', 'cos_phi_operator', 'sin_phi_operator')),
    'FluxQubit': ({'EJ1': 1.0, 'EJ2': 1.0, 'EJ3': 0.8, 'ECJ1': 0.016,
                   'ECJ2': 0.016, 'ECJ3': 0.021, 'ECg1': 0.83, 'ECg2': 0.83,
                   'ng1': 0.0, 'ng2': 0.0, 'flux': 0.4, 'ncut': 5},
                  ('n_1_operator', 'n_2_operator', 'cos_phi_1_operator',
                   'cos_phi_2_operator', 'sin_phi_1_operator', 'sin_phi_2_operator')),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--class-name', choices=tuple(FIXTURES), required=True)
    parser.add_argument('--boundary', action='store_true')
    parser.add_argument('--levels', type=int, choices=(6, 32), default=6)
    parser.add_argument('--refuse', action='store_true')
    args = parser.parse_args()
    expected = {'scqubits': '4.3.1', 'qutip': '5.3.1', 'numpy': '2.5.4', 'scipy': '1.18.1'}
    versions = {name: importlib.metadata.version(name) for name in expected}
    assert versions == expected, versions
    started = time.monotonic()
    params, operators = FIXTURES[args.class_name]
    params = dict(params)
    if args.boundary or args.refuse:
        if args.class_name == 'Fluxonium':
            params['cutoff'] = 513 if args.refuse else 512
        else:
            params['ncut'] = (11 if args.refuse else 10) if args.class_name == 'FluxQubit' else (256 if args.refuse else 255)
    dimension = params.get('cutoff', 2 * params.get('ncut', 0) + 1)
    if args.class_name == 'FluxQubit':
        dimension **= 2
    if dimension > 512:
        print(json.dumps({'class': args.class_name, 'refused_dimension': dimension,
                          'reason': 'discovery fixture dimension exceeds 512 before native construction'}))
        return
    assert not args.refuse
    levels = args.levels
    model = getattr(scq, args.class_name)(**params, truncated_dim=levels,
                                           esys_method='esys_scipy_dense',
                                           evals_method='evals_scipy_dense')
    assert model.hilbertdim() == dimension
    h = model.hamiltonian()
    assert h.shape == (dimension, dimension)
    energies, vectors = model.eigensys(evals_count=levels)
    esys = (energies, vectors)
    direct = model.eigenvals(evals_count=levels)
    assert np.max(np.abs(direct - energies)) < 1e-9
    orthogonality = float(np.linalg.norm(vectors.conj().T @ vectors - np.eye(levels)))
    residual = float(np.linalg.norm(h @ vectors - vectors * energies))
    assert orthogonality < 1e-9 and residual < 1e-8
    reference = float(energies[0])
    exported_h = np.diag(energies - reference).astype(complex)
    roundtrip = qt.Qobj(2 * np.pi * exported_h).eigenenergies() / (2 * np.pi)
    spectrum_error = float(np.max(np.abs(roundtrip - (energies - reference))))
    assert spectrum_error < 1e-9
    native_h = model.hamiltonian(energy_esys=esys)
    assert np.max(np.abs(native_h - np.diag(energies))) < 1e-9
    arrays = {'hamiltonian_ghz': exported_h}
    errors = {}
    raw_operator_bytes = {}
    for name in operators:
        operator = getattr(model, name)()
        exported = getattr(model, name)(energy_esys=esys)
        manual = vectors.conj().T @ operator @ vectors
        error = float(np.max(np.abs(exported - manual)))
        assert error < 1e-9
        assert np.max(np.abs(exported - exported.conj().T)) < 1e-9
        # Arbitrary eigenvector phases change coordinates but not the operator.
        phases = np.diag(np.exp(1j * np.arange(levels) * 0.37))
        phased = vectors @ phases
        invariant = phases @ (phased.conj().T @ operator @ phased) @ phases.conj().T
        assert np.max(np.abs(invariant - exported)) < 1e-9
        assert np.allclose(qt.Qobj(exported).full(), manual, atol=1e-9, rtol=0)
        arrays[name] = exported.astype(complex)
        errors[name] = error
        raw_operator_bytes[name] = operator.nbytes
    # Degenerate eigenspaces admit arbitrary rotations: compare projectors and
    # block operator spectra, rather than the individual eigenvector columns.
    degenerate = scq.Transmon(EJ=0, EC=0.3, ng=0, ncut=3, truncated_dim=3,
                               esys_method='esys_scipy_dense')
    degenerate_energies, degenerate_vectors = degenerate.eigensys(evals_count=3)
    assert abs(degenerate_energies[1] - degenerate_energies[2]) < 1e-12
    block = degenerate_vectors[:, 1:3]
    rotation = np.array([[1, 1j], [1j, 1]], dtype=complex) / np.sqrt(2)
    rotated = block @ rotation
    assert np.linalg.norm(block @ block.conj().T - rotated @ rotated.conj().T) < 1e-12
    test_operator = degenerate.n_operator()
    assert np.max(np.abs(np.linalg.eigvalsh(block.conj().T @ test_operator @ block)
                         - np.linalg.eigvalsh(rotated.conj().T @ test_operator @ rotated))) < 1e-12
    numeric_json = json.dumps({name: {'real': array.real.tolist(), 'imag': array.imag.tolist()}
                               for name, array in arrays.items()}, allow_nan=False)
    decoded = json.loads(numeric_json)
    decoded_h = np.array(decoded['hamiltonian_ghz']['real']) + 1j * np.array(decoded['hamiltonian_ghz']['imag'])
    assert np.max(np.abs(qt.Qobj(2 * np.pi * decoded_h).eigenenergies() / (2 * np.pi)
                         - (energies - reference))) < 1e-9
    arrays['metadata_json'] = np.array(json.dumps({'energy_reference_ghz': reference,
                                                   'basis': 'single native eigensystem',
                                                   'units': 'linear GHz'}))
    output = io.BytesIO()
    np.savez(output, **arrays)
    payload = output.getvalue()
    with np.load(io.BytesIO(payload), allow_pickle=False) as loaded:
        assert all(loaded[name].dtype.kind in 'biufcUS' for name in loaded.files)
        assert np.max(np.abs(qt.Qobj(2 * np.pi * loaded['hamiltonian_ghz']).eigenenergies()
                             / (2 * np.pi) - (energies - reference))) < 1e-9
    analytic_error = None
    if args.class_name == 'Transmon':
        approximate = np.sqrt(8 * params['EJ'] * params['EC']) - params['EC']
        analytic_error = float(abs((energies[1] - energies[0]) / approximate - 1))
        assert analytic_error < 0.02
    print(json.dumps({'versions': versions, 'class': args.class_name, 'parameters': params,
                      'levels': levels, 'raw_dimension': dimension,
                      'raw_hamiltonian_bytes': h.nbytes, 'raw_operator_bytes': raw_operator_bytes,
                      'energies_ghz': energies.tolist(), 'e01_ghz': float(energies[1] - energies[0]),
                      'transmon_analytic_relative_error': analytic_error,
                      'native_direct_max_error_ghz': float(np.max(np.abs(direct - energies))),
                      'roundtrip_max_error_ghz': spectrum_error,
                      'operator_max_errors': errors, 'eigensystem_residual': residual,
                      'orthogonality_error': orthogonality, 'npz_bytes': len(payload), 'numeric_json_bytes': len(numeric_json.encode()),
                      'wall_seconds_after_import': time.monotonic() - started,
                      'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, indent=2))


if __name__ == '__main__':
    main()
