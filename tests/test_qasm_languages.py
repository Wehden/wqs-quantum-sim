"""Bounded native gate mapping and the explicitly supported QASM3 subset."""
import json
import math
import os
import subprocess
import sys

import cirq
import numpy as np
import pytest
import qsimcirq

from qsim.qasm_adapter import prepare

PREFIX = 'OPENQASM 3.0; include "stdgates.inc"; '


def payload(source, **kwargs):
    return dict(engine='qsim',input_format='qasm3',operation='sample',shots=8,seed=42,
                circuit=PREFIX+source,**kwargs)


@pytest.mark.parametrize('kind',['float[32]','float[64]','angle[32]','angle[64]'])
def test_native_sized_binding(kind):
    circuit,metadata = prepare(payload(f'input {kind} theta; qubit q; bit c; rx(theta) q; c=measure q;',bindings={'theta':math.pi}))
    result = qsimcirq.QSimSimulator(seed=42).run(circuit,repetitions=8)
    assert np.all(result.records['c_0'] == 1)
    assert metadata['input_format'] == 'qasm3'


def test_nested_custom_gate_mapping_is_per_call():
    request = payload('gate inner(a) t { rx(a) t; } gate outer(b) t { inner(b/2) t; } '
        'qubit[2] q; bit[2] c; outer(0) q[1]; outer(2*pi) q[0]; c=measure q;')
    circuit,_ = prepare(request)
    assert not any(isinstance(op,cirq.CircuitOperation) for op in circuit.all_operations())
    result = qsimcirq.QSimSimulator().run(circuit,repetitions=8)
    assert np.all(result.records['c_0'] == 1)
    assert np.all(result.records['c_1'] == 0)


@pytest.mark.parametrize('bindings',[{}, {'theta':1,'extra':2},{'theta':True},{'theta':'1'},{'theta':float('inf')},{'theta':None}])
def test_invalid_bindings(bindings):
    with pytest.raises(ValueError,match='invalid_binding'):
        prepare(payload('input float[64] theta; qubit q; bit c; rx(theta) q; c=measure q;',bindings=bindings))


@pytest.mark.parametrize('kind',['float','float[1]','float[1000000000]','int[32]'])
def test_unsupported_input_types(kind):
    with pytest.raises(ValueError,match='(invalid_circuit|invalid_binding|unsupported_feature)'):
        prepare(payload(f'input {kind} theta; qubit q; bit c; rx(theta) q; c=measure q;',bindings={'theta':1}))


def test_derived_nonfinite_gate_rejected():
    with pytest.raises(ValueError,match='(invalid_circuit|invalid_binding)'):
        prepare(payload('input float[64] theta; qubit q; bit c; rx(theta*theta) q; c=measure q;',bindings={'theta':1e308}))


@pytest.mark.parametrize('order',['big','little'])
def test_qasm3_probability_worker_preserves_declared_qubit_order(tmp_path,order):
    request=payload('qubit[12] q; qubit a; x q[10]; x a;',bindings={})
    request.update(operation='basis_probabilities',bitstrings=['0000000000101'],bit_order=order)
    if order=='little':request['bitstrings']=[bits[::-1] for bits in request['bitstrings']]
    source,output=tmp_path/'in.json',tmp_path/'out.json';source.write_text(json.dumps(request))
    process=subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],
        env={'PATH':os.environ['PATH'],'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},capture_output=True,timeout=30)
    assert process.returncode==0,process.stderr.decode()
    result=json.loads(output.read_text())
    assert result['status']=='SUCCEEDED',result
    assert result['summary']['probabilities']==pytest.approx([1],abs=1e-6)
    assert result['summary']['input_format']=='qasm3'


def test_qasm3_nonunitary_probability_rejected():
    request=payload('qubit q; bit c; c=measure q;')
    request['operation']='basis_probabilities'
    with pytest.raises(ValueError,match='unsupported_feature: nonunitary'):
        prepare(request)


@pytest.mark.parametrize('input_format',['qasm2','qasm3'])
def test_worker_executes_parameterized_custom_gate(tmp_path,input_format):
    prefix = 'OPENQASM 2.0; include "qelib1.inc"; ' if input_format=='qasm2' else PREFIX
    declarations = 'qreg q[1]; creg c[1];' if input_format=='qasm2' else 'qubit q; bit c;'
    request = dict(engine='qsim',input_format=input_format,operation='sample',shots=8,seed=42,
        circuit=prefix+'gate turn(a) t { rx(a) t; } '+declarations+' turn(pi) q[0]; measure q -> c;')
    source,output=tmp_path/'in.json',tmp_path/'out.json';source.write_text(json.dumps(request))
    process=subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],
        env={'PATH':os.environ['PATH'],'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},capture_output=True,timeout=30)
    assert process.returncode==0 and not process.stderr
    result=json.loads(output.read_text())
    assert result['status']=='SUCCEEDED',result
    assert result['summary']['counts']=={'1':8}


def test_worker_resolves_qasm3_bound_probability(tmp_path):
    request = payload('input angle[64] theta; qubit q; rx(theta) q;',bindings={'theta':math.pi/3})
    request.update(operation='basis_probabilities',bitstrings=['0','1'])
    source,output=tmp_path/'in.json',tmp_path/'out.json';source.write_text(json.dumps(request))
    process=subprocess.run([sys.executable,'-m','qsim.worker','simulate',str(source),str(output)],
        env={'PATH':os.environ['PATH'],'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},capture_output=True,timeout=30)
    assert process.returncode==0 and not process.stderr
    result=json.loads(output.read_text())
    assert result['status']=='SUCCEEDED',result
    assert result['summary']['probabilities']==pytest.approx([.75,.25],abs=1e-6)
