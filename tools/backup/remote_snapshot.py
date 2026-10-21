"""Parent-owned capture using a bounded, authenticated Kubernetes exec exporter."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tarfile
import time

from .capture import regular_file
from .metadata import canonical, digest, parse_metadata, require
from .snapshot import images, read_private, require_parent_lock, source_identity, write_private
from .v2 import validate_manifest


class KubernetesSource:
    def __init__(self, namespace, deployment, container, data, revision, budget):
        require(all(re.fullmatch('[a-z0-9][a-z0-9.-]*', value)
                    for value in (namespace, deployment, container)), 'invalid source names')
        require(revision is None or re.fullmatch('[a-f0-9]{40}', revision), 'invalid expected source revision')
        self.namespace, self.deployment, self.container = namespace, deployment, container
        self.data, self.revision, self.budget = data, revision, budget
        self.pod = None

    def get(self, kind, name=None, selector=None):
        args = ['kubectl', '-n', self.namespace, 'get', kind]
        if name:
            args.append(name)
        if selector:
            args += ['-l', selector]
        return json.loads(subprocess.run(args + ['-o', 'json'], check=True, capture_output=True,
                                        timeout=self.budget.remaining(15)).stdout)

    def identity(self):
        deployment = self.get('deployment', self.deployment)
        require(deployment['spec']['replicas'] == 1, 'single API writer required')
        selector = deployment['spec']['selector']
        require(set(selector) == {'matchLabels'}, 'simple deployment selector required')
        pods = self.get('pods', selector=','.join(k + '=' + v for k, v in sorted(selector['matchLabels'].items())))['items']
        pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
        require(len(pods) == 1, 'one current source pod required')
        pod = pods[0]
        owner = next(o for o in pod['metadata']['ownerReferences'] if o.get('controller'))
        require(owner['kind'] == 'ReplicaSet', 'deployment ReplicaSet required')
        rs = self.get('replicaset', owner['name'])
        require(rs['metadata']['uid'] == owner['uid'] and any(
            o.get('controller') and o['kind'] == 'Deployment' and o['uid'] == deployment['metadata']['uid']
            for o in rs['metadata']['ownerReferences']), 'source owner mismatch')
        container = next(c for c in pod['spec']['containers'] if c['name'] == self.container)
        desired = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == self.container)
        require(container['image'] == desired['image'] and re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}', container['image']),
                'immutable deployed source required')
        status = next(c for c in pod['status']['containerStatuses'] if c['name'] == self.container)
        require(status.get('ready') and status.get('state', {}).get('running'), 'source container not ready')
        runtime = status['imageID'].split('://')[-1]
        require(runtime.endswith('@' + container['image'].split('@')[1]), 'runtime image identity mismatch')
        revision = pod['metadata'].get('annotations', {}).get('wqsim-release-sha', '')
        require(re.fullmatch('[a-f0-9]{40}', revision)
                and deployment['spec']['template'].get('metadata', {}).get('annotations', {}).get('wqsim-release-sha') == revision
                and (self.revision is None or revision == self.revision), 'source revision annotation mismatch')
        mount = next(m for m in container['volumeMounts'] if m['mountPath'] == self.data)
        require(not mount.get('subPath') and not mount.get('subPathExpr'), 'complete data mount required')
        volume = next(v for v in pod['spec']['volumes'] if v['name'] == mount['name'])
        pvc_name = volume['persistentVolumeClaim']['claimName']
        pvc = self.get('pvc', pvc_name)
        require(pvc['status']['phase'] == 'Bound', 'source PVC not bound')
        self.pod = pod['metadata']['name']
        return {'workload': source_identity('pod', self.namespace, self.pod, pod['metadata']['uid']),
                'storage': source_identity('pvc', self.namespace, pvc_name, pvc['metadata']['uid']),
                'revision': revision, 'image_or_executable_digest': container['image'].rsplit(':', 1)[1]}

    def execute(self, action, sha, destination, max_bytes):
        require(self.pod is not None, 'source identity must be checked first')
        # The remote process owns a shorter deadline, including temp cleanup and
        # SQLite rollback. Closing kubectl alone is not treated as remote exit.
        script = Path(__file__).with_name('remote_data.py').read_text()
        args = ['kubectl', '-n', self.namespace, 'exec', '-i', self.pod, '-c', self.container,
                '--', 'python', '-c', script]
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        total = 0
        try:
            ready = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while not ready.endswith(b'\n'):
                    require(selector.select(self.budget.remaining(15)), 'remote startup timeout')
                    chunk = os.read(process.stdout.fileno(), 512)
                    require(chunk and len(ready) + len(chunk) <= 512, 'invalid remote handshake')
                    ready.extend(chunk)
            ready = parse_metadata(bytes(ready))
            require(set(ready) == {'ready'} and type(ready['ready']) is int, 'invalid remote clock')
            remaining = self.budget.remaining()
            require(remaining >= 12, 'insufficient remote cleanup budget')
            deadline = min(480, int(remaining) - 10)
            request = canonical({'action': action, 'sha': sha, 'deadline': ready['ready'] + deadline * 1000, 'max_bytes': max_bytes})
            remote_until = time.monotonic() + deadline
            process.stdin.write(request); process.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    wait = remote_until + 5 - time.monotonic()
                    if wait <= 0 or not selector.select(wait):
                        raise TimeoutError('remote exporter deadline')
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    require(total <= (4096 if action == 'validate' else max_bytes), 'remote output exceeds limit')
                    destination.write(chunk)
            require(process.wait(timeout=max(1, remote_until + 5 - time.monotonic())) == 0,
                    'remote exporter failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            # The remote deadline remains active if the exec connection failed.
            # A caller must keep admission held on every failure; no success is
            # inferred from client cancellation or a partial stream.


class RemoteSnapshot:
    def __init__(self, source, state, registry, delivery_key, registry_access, context,
                 retention_ref, budget, parent_lock_fd, *, cd_enabled, max_bytes=512 * 1024 * 1024):
        require(type(cd_enabled) is bool, 'boolean CD provenance required')
        self.source, self.state, self.registry, self.delivery_key = source, Path(state), registry, delivery_key
        self.registry_access, self.context, self.retention_ref = Path(registry_access), context, retention_ref
        self.budget, self.lock_fd, self.cd_enabled, self.max_bytes = budget, parent_lock_fd, cd_enabled, max_bytes
        self.expected = None

    def parent(self):
        require_parent_lock(self.lock_fd, self.state)
        raw = read_private(self.state / 'deployment.json')
        ledger = json.loads(raw)
        require(ledger['sha'] == self.context['scope']['release_sha'] and ledger['status'] == 'holding'
                and 'backup_id' not in ledger, 'matching holding ledger required')
        return raw, ledger

    def create(self, destination):
        root = Path(destination)
        require(root.is_dir() and root.resolve() == root and not list(root.iterdir()), 'empty staging required')
        self.ledger_bytes, ledger = self.parent()
        self.identity = self.source.identity()
        require(self.context['source'] == self.identity, 'source identity mismatch')
        self.versions = (self.registry.read(), self.delivery_key.read())
        archive_path = root / 'source.tar'
        with archive_path.open('xb') as out:
            os.chmod(archive_path, 0o600)
            self.source.execute('export', ledger['sha'], out, self.max_bytes)
        inventory = []
        with tarfile.open(archive_path, 'r:') as archive:
            first = archive.next()
            require(first and first.name == 'source.json' and first.isfile() and not first.issparse()
                    and first.size <= 4 * 1024 * 1024, 'invalid source metadata')
            raw = archive.extractfile(first).read()
            metadata = parse_metadata(raw)
            require(canonical(metadata) == raw and set(metadata) == {'inventory', 'fingerprint', 'data'}
                    and metadata['data'] == self.source.data
                    and re.fullmatch('[a-f0-9]{64}', metadata['fingerprint']), 'invalid source metadata')
            expected = {}
            for item in metadata['inventory']:
                require(set(item) == {'path', 'kind', 'role', 'sha256', 'size'} and item['kind'] == 'regular'
                        and type(item['size']) is int and 0 <= item['size'] <= self.max_bytes
                        and re.fullmatch('[a-f0-9]{64}', item['sha256']), 'invalid source inventory')
                path = item['path']
                require((path == 'database.sqlite3' and item['role'] == 'database') or
                        (re.fullmatch(r'results/[a-f0-9]{32}\.npz', path) and item['role'] == 'results'), 'unexpected source path')
                require(path not in expected, 'duplicate source path')
                expected[path] = item
            require('database.sqlite3' in expected, 'missing database')
            seen = set()
            while member := archive.next():
                require(member.name in expected and member.name not in seen and member.isfile()
                        and not member.issparse() and member.size == expected[member.name]['size'], 'invalid source member')
                value = archive.extractfile(member).read()
                require(digest(value) == {k: expected[member.name][k] for k in ('size', 'sha256')}, 'source content mismatch')
                write_private(root / member.name, value)
                inventory.append(expected[member.name]); seen.add(member.name)
            require(seen == set(expected), 'incomplete source archive')
        archive_path.unlink()
        self.fingerprint = metadata['fingerprint']
        deps = {'kind': 'wqs', 'pvc_identity': self.identity['storage'], 'parent_coordinated': True,
                'ledger_state': 'holding', 'admission_held': True, 'cd_enabled': self.cd_enabled,
                'database': 'database.sqlite3', 'results': [i['path'] for i in inventory if i['role'] == 'results'],
                'release_receipts': []}
        def add(role, path, value):
            write_private(root / path, value)
            inventory.append({'path': path, 'kind': 'regular', 'role': role, **digest(value)})
            if role == 'release_receipts':
                deps[role].append(path)
            else:
                deps[role] = path
        add('invitation_registry', 'registry.json', self.versions[0][1])
        add('pending_delivery_key', 'delivery-key', self.versions[1][1])
        add('runner_ledger', 'runner/deployment.json', self.ledger_bytes)
        for receipt in sorted(self.state.glob('*.json')):
            if re.fullmatch(r'[a-f0-9]{40}\.json', receipt.name):
                raw = read_private(receipt); value = json.loads(raw)
                require(value['status'] == 'verified' and value['sha'] + '.json' == receipt.name, 'invalid receipt')
                add('release_receipts', 'runner/' + receipt.name, raw)
        require(isinstance(self.retention_ref, str) and self.retention_ref, 'retention reference required')
        add('image_inventory', 'images.json', canonical({'images': images(ledger), 'retention_ref': self.retention_ref}))
        access = read_private(self.registry_access)
        require(isinstance(json.loads(access), dict), 'invalid registry configuration')
        add('registry_access', 'registry-access.json', access)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        context = {**self.context, 'boundary': {'method': 'wqs-kubernetes-exec-sqlite-v1',
                   'cursor': digest(canonical({'inventory': inventory, 'fingerprint': self.fingerprint,
                                              'versions': [list(v[0]) for v in self.versions]}))['sha256'], 'source_at': now}}
        manifest = {'schema_id': 'backup-recovery-set-v2', 'contract_version': 2, 'canonical_profile': 'backup-json-v1',
                    'record_id': 'manifest-' + context['set_id'], 'evidence_type': 'manifest', 'context': context,
                    'created_at': now, 'inventory': inventory, 'dependencies': deps}
        validate_manifest(manifest, context['set_id'])
        self.expected = canonical(manifest)
        return manifest

    def validate(self, root, manifest):
        require(self.parent()[0] == self.ledger_bytes and self.expected == canonical(manifest), 'parent state changed')
        require(self.source.identity() == self.identity, 'source identity changed')
        require((self.registry.read(), self.delivery_key.read()) == self.versions, 'source Secrets changed')
        import io
        out = io.BytesIO()
        self.source.execute('validate', self.context['scope']['release_sha'], out, self.max_bytes)
        require(json.loads(out.getvalue()) == {'fingerprint': self.fingerprint, 'data': self.source.data},
                'application source changed')
        for entry in manifest['inventory']:
            with regular_file(root, entry['path']) as stream:
                require(digest(stream.read()) == {k: entry[k] for k in ('size', 'sha256')}, 'staged source changed')
        return True
