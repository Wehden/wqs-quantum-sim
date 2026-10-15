"""Read-only probes of pinned Cirq/qsim; not service implementation."""
import json
from importlib.metadata import version
import cirq
import qsimcirq
from cirq.contrib.qasm_import._parser import QasmParser
prefix='OPENQASM 2.0; include "qelib1.inc"; '
cases={
 'scalar':'qreg q[2]; creg c[1]; creg o[1]; x q[0]; measure q[0]->c[0]; if(c==1) x q[1]; measure q[1]->o[0];',
 'unwritten':'qreg q[1]; creg c[1]; creg o[1]; if(c==0) x q[0]; measure q[0]->o[0];',
 'broadcast':'qreg q[3]; creg c[1]; creg o[3]; x q[0]; measure q[0]->c[0]; if(c==1) x q; measure q->o;',
}
output={'versions':{n:version(n) for n in ['cirq-core','qsimcirq']},'results':[]}
for name,source in cases.items():
 circuit=QasmParser().parse(prefix+source).circuit
 for engine,sim in [('cirq',cirq.Simulator(seed=42)),('qsim',qsimcirq.QSimSimulator(seed=42))]:
  row={'case':name,'engine':engine}
  try:
   r=sim.run(circuit,repetitions=8)
   row['records']={k:v.tolist() for k,v in r.records.items()}
  except Exception as e: row['error']=type(e).__name__+': '+str(e)
  output['results'].append(row)
print(json.dumps(output,indent=2))
