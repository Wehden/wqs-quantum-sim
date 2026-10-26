"""Fixed native discovery; not an API or deployed capability.

Run with Python 3.12, QuTiP==5.3.1 and NumPy==2.5.4. No WQS credentials,
network, production data, caller code or deployment actions are used.
"""
import importlib.metadata
import json
import resource
import sys
import io
import time

if '--constrained' in sys.argv:
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024**2, 12 * 1024**2))

import numpy as np
import qutip as qt


def main():
    assert qt.__version__ == '5.3.1' and np.__version__ == '2.5.4'
    started = time.monotonic()
    options = {'atol': 1e-10, 'rtol': 1e-9, 'nsteps': 10000, 'progress_bar': False}
    times = np.linspace(0, 400, 401)
    a = qt.destroy(2)
    n = a.dag() * a
    h = 2 * np.pi * 0.005 * (a + a.dag())
    result = qt.sesolve(h, qt.basis(2, 0), times, e_ops=[n], options=options)
    rabi_error = float(np.max(np.abs(result.expect[0] - np.sin(np.pi * .01 * times) ** 2)))
    assert rabi_error < 1e-4

    decay_times = np.linspace(0, 3000, 101)
    result = qt.mesolve(0 * n, qt.basis(2, 1), decay_times,
                        c_ops=[np.sqrt(.001) * a], e_ops=[n], options=options)
    decay_error = float(np.max(np.abs(result.expect[0] / np.exp(-decay_times / 1000) - 1)))
    assert decay_error < 1e-4

    unitary = qt.propagator(h, 50, options=options)
    propagator_error = float((unitary - (-1j * h * 50).expm()).norm())
    assert propagator_error < 1e-6
    superoperator = qt.propagator(qt.liouvillian(h, []), 50, options=options)
    superoperator_error = float((superoperator - qt.sprepost(unitary, unitary.dag())).norm())
    assert superoperator.issuper and superoperator.shape == (4, 4) and superoperator_error < 1e-6

    coefficient = qt.coefficient(np.full(times.shape, .005, dtype=complex), tlist=times, order=1)
    time_dependent = qt.QobjEvo([[2 * np.pi * a, coefficient],
                               [2 * np.pi * a.dag(), coefficient.conj()]])
    result = qt.sesolve(time_dependent, qt.basis(2, 0), times, e_ops=[n], options=options)
    array_error = float(np.max(np.abs(result.expect[0] - np.sin(np.pi * .01 * times) ** 2)))
    assert array_error < 1e-4

    b = qt.destroy(30)
    occupation = b.dag() * b
    ring_times = np.linspace(0, 3000, 61)
    result = qt.mesolve(0 * occupation, qt.coherent(30, 2), ring_times,
                        c_ops=[np.sqrt(.001) * b], e_ops=[occupation], options=options)
    ring_error = float(np.max(np.abs(result.expect[0] / (4 * np.exp(-.001 * ring_times)) - 1)))
    assert ring_error < 1e-3

    b = qt.destroy(10)
    kappa, thermal_n = .001, .05
    rho = qt.steadystate(0 * b.dag() * b,
                        [np.sqrt(kappa * (thermal_n + 1)) * b,
                         np.sqrt(kappa * thermal_n) * b.dag()])
    thermal_error = float(abs(qt.expect(b.dag() * b, rho) - thermal_n))
    assert thermal_error < 1e-6

    aq = qt.tensor(qt.destroy(5), qt.qeye(6))
    ar = qt.tensor(qt.qeye(5), qt.destroy(6))
    nq, nr = aq.dag() * aq, ar.dag() * ar
    static = 6 * nq + 5 * nr - .25 / 2 * nq * (nq - 1) + .05 * (aq.dag() * ar + aq * ar.dag())
    energies, vectors = static.eigenstates()
    dressed = {}
    for q, r in ((0, 0), (0, 1), (1, 0), (1, 1)):
        bare = qt.tensor(qt.basis(5, q), qt.basis(6, r))
        index = int(np.argmax([abs(bare.overlap(vector)) ** 2 for vector in vectors]))
        dressed[q, r] = energies[index]
    shift = dressed[1, 1] - dressed[1, 0] - dressed[0, 1] + dressed[0, 0]
    expected_shift = 2 * .05 ** 2 * (-.25) / (1 * (1 - .25))
    shift_error = float(abs(shift / expected_shift - 1))
    assert shift_error < .05

    mc_times = np.linspace(0, 3000, 21)
    mc = qt.mcsolve(0 * n, qt.basis(2, 1), mc_times,
                    c_ops=[np.sqrt(.001) * a], e_ops=[n], ntraj=1000,
                    seeds=42, options={**options, 'map': 'serial'})
    probabilities = np.exp(-mc_times / 1000)
    standard_errors = np.sqrt(probabilities[1:] * (1 - probabilities[1:]) / 1000)
    mc_standard_errors = float(np.max(np.abs(np.asarray(mc.expect[0])[1:] - probabilities[1:]) / standard_errors))
    assert mc_standard_errors <= 3

    # A two-ns pulse is completely absent from the requested output grid.
    pulse_errors = []
    pulse_counts = []
    pulse_ideal_errors = []
    for step in (.02, .01):
        grid = np.unique(np.concatenate(([0., 100.], np.linspace(40., 42., round(2 / step) + 1))))
        envelope = np.where((grid > 40.) & (grid < 42.), .125, 0.).astype(complex)
        coefficient = qt.coefficient(envelope, tlist=grid, order=1)
        evo = qt.QobjEvo([[2 * np.pi * a, coefficient],
                         [2 * np.pi * a.dag(), coefficient.conj()]])
        solved = qt.sesolve(evo, qt.basis(2, 0), [0., 100.], e_ops=[n],
                            options={**options, 'max_step': step / 2, 'nsteps': 100000})
        area = np.trapezoid(envelope.real, grid)
        pulse_errors.append(float(abs(solved.expect[0][-1] - np.sin(2 * np.pi * area)**2)))
        pulse_counts.append(len(grid))
        pulse_ideal_errors.append(float(abs(solved.expect[0][-1] - 1.)))
    assert max(pulse_errors) < 1e-5
    assert pulse_ideal_errors[1] < 1e-4
    assert pulse_ideal_errors[1] < pulse_ideal_errors[0] / 3

    complex_coefficient = .003 + .004j
    complex_h = 2 * np.pi * (complex_coefficient * a + complex_coefficient.conjugate() * a.dag())
    complex_evo = qt.QobjEvo([[2 * np.pi * a, qt.coefficient(
        np.full(2, complex_coefficient), tlist=[0., 50.], order=1)],
        [2 * np.pi * a.dag(), qt.coefficient(
            np.full(2, complex_coefficient.conjugate()), tlist=[0., 50.], order=1)]])
    complex_result = qt.sesolve(complex_evo, qt.basis(2, 0), [0., 50.], options=options)
    conjugation_error = float((complex_result.states[-1] -
                               (-1j * complex_h * 50).expm() * qt.basis(2, 0)).norm())
    assert complex_h.isherm and conjugation_error < 1e-6
    assert not (2 * np.pi * complex_coefficient * (a + a.dag())).isherm

    density_errors = []
    for initial in (qt.basis(2, 0), qt.ket2dm(qt.basis(2, 0)), qt.qeye(2) / 2):
        initial_density = qt.ket2dm(initial) if initial.isket else initial
        final = qt.mesolve(h, initial_density, [0., 50.], c_ops=[], options=options).states[-1]
        exact = unitary * initial_density * unitary.dag()
        density_errors.append(float((final - exact).norm()))
        assert final.isoper and abs(final.tr() - 1) < 1e-8
        assert min(final.eigenenergies()) >= -1e-8
    assert max(density_errors) < 1e-6
    invalid_density = np.diag([1.1, -.1]).astype(complex)
    assert np.min(np.linalg.eigvalsh(invalid_density)) < -1e-10
    assert np.min(np.linalg.eigvalsh(np.diag([1., 0.]))) >= -1e-10
    product_density = qt.tensor(qt.ket2dm(qt.basis(2, 0)), qt.qeye(3) / 3)
    assert product_density.shape == (6, 6) and abs(product_density.tr() - 1) < 1e-12

    # Native steady-state return behavior alone does not detect non-uniqueness.
    dephasing = qt.liouvillian(0 * n, [np.sqrt(.001) * n]).full()
    singular_values = np.linalg.svd(dephasing, compute_uv=False)
    nullity = int(np.count_nonzero(singular_values < 1e-12))
    assert nullity == 2
    try:
        ambiguous = qt.steadystate(0 * n, [np.sqrt(.001) * n])
        degenerate_native = 'returned arbitrary stationary state'
        assert (qt.liouvillian(0 * n, [np.sqrt(.001) * n]) *
                qt.operator_to_vector(ambiguous)).norm() < 1e-9
    except (ValueError, RuntimeError) as error:
        degenerate_native = type(error).__name__

    repeated = qt.mcsolve(0 * n, qt.basis(2, 1), mc_times,
                          c_ops=[np.sqrt(.001) * a], e_ops=[n], ntraj=1000,
                          seeds=42, options={**options, 'map': 'serial'})
    seed_error = float(np.max(np.abs(np.asarray(repeated.expect) - np.asarray(mc.expect))))
    assert seed_error == 0.

    # Dense eigensolves and sparse Lindblad construction use different growth laws.
    dense_dimension = 256
    dense_h = (qt.qdiags(np.ones(dense_dimension - 1), 1) +
               qt.qdiags(np.ones(dense_dimension - 1), -1)).full()
    dense_values = np.linalg.eigvalsh(dense_h)
    dense_expected = np.sort(2 * np.cos(np.arange(1, dense_dimension + 1) *
                                         np.pi / (dense_dimension + 1)))
    dense_error = float(np.max(np.abs(dense_values - dense_expected)))
    assert dense_error < 1e-12
    sparse_a = qt.destroy(64)
    sparse_liouvillian = qt.liouvillian(sparse_a.dag() * sparse_a, [.01 * sparse_a])
    assert sparse_liouvillian.shape == (4096, 4096)
    sparse_matrix = sparse_liouvillian.to('CSR').data.as_scipy()
    sparse_bytes = int(sparse_matrix.data.nbytes + sparse_matrix.indices.nbytes +
                       sparse_matrix.indptr.nbytes)
    output = io.BytesIO()
    np.savez_compressed(output, times=times, density=product_density.full(),
                        metadata_json=np.asarray('{"units":"ns"}'))
    report = {'qutip': qt.__version__, 'numpy': np.__version__,
              'scipy': importlib.metadata.version('scipy'),
              'rabi_max_absolute_error': rabi_error,
              't1_max_relative_error': decay_error,
              'unitary_propagator_error': propagator_error,
              'superoperator_error': superoperator_error,
              'array_coefficient_rabi_error': array_error,
              'ringdown_max_relative_error': ring_error,
              'thermal_occupation_error': thermal_error,
              'dispersive_shift_relative_error': shift_error,
              'mc_max_standard_errors': mc_standard_errors,
              'between_output_pulse_errors': pulse_errors,
              'coefficient_grid_counts': pulse_counts,
              'between_output_ideal_square_errors': pulse_ideal_errors,
              'complex_conjugation_state_error': conjugation_error,
              'pure_mixed_density_errors': density_errors,
              'degenerate_liouvillian_nullity': nullity,
              'degenerate_native_result': degenerate_native,
              'repeated_mc_seed_max_error': seed_error,
              'dense_dimension': dense_dimension,
              'dense_input_bytes': dense_h.nbytes,
              'dense_spectrum_max_error': dense_error,
              'sample_npz_bytes': len(output.getvalue()),
              'sparse_liouvillian_dimension': 4096,
              'sparse_liouvillian_bytes': sparse_bytes,
              'address_limit_bytes': resource.getrlimit(resource.RLIMIT_AS)[0],
              'file_limit_bytes': resource.getrlimit(resource.RLIMIT_FSIZE)[0],
              'wall_seconds': time.monotonic() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
