"""Local library discovery; not an API, worker benchmark or deployed capability.

Run with Python 3.12, QuTiP==5.3.1 and NumPy==2.5.4. No WQS credentials,
network, production data, caller code or deployment actions are used.
"""
import importlib.metadata
import json
import resource
import time

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
              'wall_seconds': time.monotonic() - started,
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
