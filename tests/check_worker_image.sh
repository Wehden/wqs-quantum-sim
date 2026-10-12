#!/bin/bash
set -euo pipefail
image=${1:-worker:local}
# -i feeds the test script to Python in the image; no host temporary markers.
docker run --rm -i --entrypoint /usr/local/bin/python3 "$image" - <<'PY'
from pathlib import Path
import subprocess,time,signal
for log,marker in [('apt-ssh-setup','apt_ssh_setup_complete'),('runtime-setup','ray_skypilot_installation_complete'),('env-setup','env_setup_complete')]:
 p=Path('/tmp')/(log+'.log');m=Path('/tmp')/marker;p.write_text('first\n')
 proc=subprocess.Popen(['/usr/local/bin/tail','-f','-n','+1',str(p)],stdout=subprocess.PIPE)
 time.sleep(.3);assert proc.poll() is None
 with p.open('a') as f:f.write('last\n')
 m.touch();out=proc.communicate(timeout=3)[0];assert out==b'first\nlast\n' and proc.returncode==0
 m.unlink();p.with_suffix('.failed').touch()
 assert subprocess.check_output(['/usr/local/bin/tail','-f','-n','+1',str(p)],timeout=3)==out
 assert subprocess.check_output(['/usr/local/bin/tail','-n','1',str(p)])==b'last\n'
print('All startup followers: wait, completion, failed marker, drain and GNU passthrough passed')
PY
