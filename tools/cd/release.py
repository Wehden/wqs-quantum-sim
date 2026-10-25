"""Main CI artifact promotion; credentials and deployment inventory stay local."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import tarfile
import urllib.request

REPOSITORY = 'Wehden/wqs-quantum-sim'
TARGETS = ('api', 'controller', 'worker')
ROLES = {'api': 'api', 'admin': 'api', 'controller': 'controller', 'observer': 'controller'}


def run(args, timeout=120, env=None):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout, env=env).stdout.strip()


def verify_creation(raw, expected_id):
    """Accept only this invocation's creation result, never restore evidence."""
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate backup result field')
            value[key] = item
        return value
    result = json.loads(raw, object_pairs_hook=pairs)
    if (not isinstance(result, dict)
            or set(result) != {'evidence_type', 'created', 'backup_id'}
            or result['evidence_type'] != 'creation_result'
            or result['created'] is not True
            or result['backup_id'] != expected_id):
        raise ValueError('Matching successful backup creation result required')
    return result


def github(path):
    req = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/' + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def eligible(event):
    job = event.get('workflow_run', {})
    return (event.get('repository', {}).get('full_name', '').lower() == REPOSITORY.lower()
        and job.get('head_repository', {}).get('full_name', '').lower() == REPOSITORY.lower()
        and job.get('event') == 'push' and job.get('head_branch') == 'main'
        # GitHub's run-name is a display title, not the workflow identity.
        and job.get('path') == '.github/workflows/ci.yml' and job.get('conclusion') == 'success'
        and re.fullmatch('[0-9a-f]{40}', job.get('head_sha', '')) is not None)


def current(sha):
    return github('git/ref/heads/main')['object']['sha'] == sha


def gate(event):
    if eligible(event) and current(event['workflow_run']['head_sha']):
        return event['workflow_run']['head_sha']
    return ''


def write_record(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as out:
        json.dump(data, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_config(archive, image):
    # Adapted from HousePanel's archive verifier: never extract paths to disk.
    with tarfile.open(archive, 'r:*') as bundle:
        def read_member(name):
            member = bundle.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError('Invalid image configuration member')
            return bundle.extractfile(member).read()
        manifests = json.loads(read_member('manifest.json'))
        if len(manifests) != 1 or manifests[0]['RepoTags'] != [image]:
            raise ValueError('Expected exactly one tagged release image')
        payload = read_member(manifests[0]['Config'])
    return 'sha256:' + hashlib.sha256(payload).hexdigest(), json.loads(payload)


def verify_loaded_content(actual, expected, sha):
    # Docker backends expose different image IDs and may add empty legacy fields.
    def normalized(value):
        if isinstance(value, dict):
            return {k: normalized(v) for k, v in value.items() if v not in (None, '', False, [], {})}
        return value
    if (actual['Os'] != expected['os'] or actual['Architecture'] != expected['architecture']
            or actual.get('Variant', '') != expected.get('variant', '')
            or actual['RootFS'] != {'Type': expected['rootfs']['type'], 'Layers': expected['rootfs']['diff_ids']}
            or normalized(actual['Config']) != normalized(expected['config'])
            or actual['Config'].get('Labels', {}).get('org.opencontainers.image.revision') != sha):
        raise ValueError('Loaded image content mismatch')


def validate_bundle(root, sha):
    result = {}
    for target in TARGETS:
        record = json.loads((root / (target + '.json')).read_text())
        if (record['schema'] != 1 or record['source_sha'] != sha or record['target'] != target
                or record['archive'] != target + '.tar' or record['platform'] != 'linux/amd64'
                or record['image_ref'] != 'wqs-release-ci:' + target + '-' + sha
                or not re.fullmatch('sha256:[a-f0-9]{64}', record['config_digest'])):
            raise ValueError('Invalid release manifest')
        with (root / record['archive']).open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != record['archive_sha256']:
            raise ValueError('Archive checksum mismatch')
        config_digest, config = archive_config(root / record['archive'], record['image_ref'])
        if config_digest != record['config_digest'] or config['os'] + '/' + config['architecture'] != record['platform']:
            raise ValueError('Image config identity mismatch')
        if config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != sha:
            raise ValueError('Image source mismatch')
        result[target] = record
    return result


def acceptance_token(config):
    options = config.get('acceptance')
    if not isinstance(options, dict) or set(options) != {'api_url', 'admin_health_url', 'token_file'}:
        raise ValueError('Complete acceptance configuration required')
    for name in ('api_url', 'admin_health_url'):
        value = options[name]
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
            raise ValueError('Invalid acceptance URL')
        url = urllib.parse.urlsplit(value)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError('Invalid acceptance URL')
        _ = url.port
    value = options['token_file']
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('Absolute acceptance token path required')
    try:
        with Path(value).open() as source:
            token = source.read(4097).strip()
    except (OSError, UnicodeError):
        raise ValueError('Acceptance token unavailable') from None
    if not token or len(token) > 4096 or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise ValueError('Invalid acceptance token')
    return token


def validate_config(config):
    if not config.get('bootstrap_verified'):
        raise ValueError('Bootstrap verification required')
    acceptance_token(config)
    for field in ('namespace', 'registry', 'state_dir', 'smoke_owner'):
        if not isinstance(config.get(field), str) or not config[field]:
            raise ValueError('Missing private configuration')
    references = config.get('required_secrets')
    if not isinstance(references, list) or not 1 <= len(references) <= 16:
        raise ValueError('Required secret inventory must contain 1 to 16 references')
    names = set()
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {'name', 'keys'}:
            raise ValueError('Invalid secret reference')
        name, keys = reference['name'], reference['keys']
        if (not isinstance(name, str) or len(name) > 253
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in name.split('.')) or name in names):
            raise ValueError('Invalid or duplicate secret name')
        names.add(name)
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 64
                or any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,253}', key)
                       for key in keys) or len(set(keys)) != len(keys)):
            raise ValueError('Invalid required secret keys')
    if not Path(config['state_dir']).is_absolute():
        raise ValueError('State directory must be absolute')
    if set(config['roles']) != set(ROLES):
        raise ValueError('All four service roles required')
    for role in config['roles'].values():
        if not all(re.fullmatch('[a-z0-9][a-z0-9.-]*', role.get(k, '')) for k in ('deployment', 'container')):
            raise ValueError('Invalid role mapping')
    if len({r['deployment'] for r in config['roles'].values()}) != 4:
        raise ValueError('Roles require distinct deployments')
    for field in ('backup_command', 'verify_command'):
        command = config.get(field)
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError('Private backup and verification commands required')
    if not re.fullmatch('[a-z0-9][a-z0-9.-]*', config['namespace']):
        raise ValueError('Invalid namespace')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9./:_-]+', config['registry']):
        raise ValueError('Invalid registry prefix')


class Deployer:
    def __init__(self, config):
        validate_config(config)
        self.config = config

    def kube(self, *args, timeout=120):
        return run(['kubectl', '-n', self.config['namespace'], *args], timeout)

    def preflight_secrets(self):
        # kubectl retrieves the object internally, but emits only nonempty key
        # names. Neither encoded values nor raw diagnostics reach release logs.
        template = '{{range $key, $value := .data}}{{if $value}}{{$key}}{{"\\n"}}{{end}}{{end}}'
        for reference in self.config['required_secrets']:
            for attempt in range(2):
                try:
                    keys = set(self.kube('get', 'secret', reference['name'],
                        '--request-timeout=8s', '-o', 'go-template=' + template, timeout=10).splitlines())
                    break
                except (subprocess.SubprocessError, OSError):
                    if attempt == 1:
                        raise RuntimeError('Required secret unavailable; inspect private inventory') from None
                    time.sleep(1)
            if not set(reference['keys']).issubset(keys):
                raise ValueError('Required secret key missing or empty; inspect private inventory')

    def control(self, action, sha):
        role = self.config['roles']['api']
        args = ['exec', 'deployment/' + role['deployment'], '-c', role['container'], '--',
                'python', '-m', 'qsim.release_control', action, '--release', sha]
        if action == 'smoke':
            args += ['--smoke-owner', self.config['smoke_owner']]
        return json.loads(self.kube(*args))

    def drained(self, sha):
        state = self.control('status', sha)
        if state['protocol'] != 1 or state['release'] != sha:
            raise ValueError('Release lost its admission hold')
        return state['pending'] == 0

    def wait_drain(self, sha):
        # This limits deployment waiting, never the lifetime of a simulation.
        until = time.monotonic() + 900
        while not self.drained(sha):
            if time.monotonic() >= until:
                raise TimeoutError('Drain incomplete; preserve jobs and maintenance hold')
            time.sleep(5)

    def publish(self, root, records, sha):
        images = {}
        for target, record in records.items():
            # Register each tag before the command: a failed command may have
            # completed its side effect before losing the daemon connection.
            local_tags = [record['image_ref']]
            published = False
            try:
                run(['docker', 'load', '-i', str(root / record['archive'])], 600)
                info = json.loads(run(['docker', 'image', 'inspect', record['image_ref']]))[0]
                _, expected = archive_config(root / record['archive'], record['image_ref'])
                verify_loaded_content(info, expected, sha)
                repo = self.config['registry'].rstrip('/') + '/' + target
                tag = repo + ':' + sha
                local_tags.append(tag)
                run(['docker', 'tag', record['image_ref'], tag])
                run(['docker', 'push', tag], 600)
                info = json.loads(run(['docker', 'image', 'inspect', tag]))[0]
                digests = [d for d in info.get('RepoDigests', []) if re.fullmatch(re.escape(repo) + '@sha256:[a-f0-9]{64}', d)]
                if len(digests) != 1:
                    raise ValueError('Ambiguous registry image identity')
                images[target] = digests[0]
                published = True
            finally:
                cleanup_failed = False
                for local_tag in reversed(local_tags):
                    try:
                        run(['docker', 'image', 'rm', local_tag])
                    except (subprocess.SubprocessError, OSError):
                        cleanup_failed = True
                        print('Local release image cleanup failed; inspect the deployment runner', file=sys.stderr)
                # Preserve the original publishing failure, but never report a
                # successful stage if its local image cleanup failed.
                if cleanup_failed and published:
                    raise RuntimeError('Local release image cleanup incomplete')
        return images

    def capture(self):
        result = {}
        for role, mapping in self.config['roles'].items():
            live = json.loads(self.kube('get', 'deployment', mapping['deployment'], '-o', 'json'))
            if role == 'controller' and live['spec'].get('strategy', {}).get('type', 'RollingUpdate') != 'Recreate':
                raise ValueError('Controller requires Recreate to transfer its exclusive lock')
            if live['spec'].get('replicas', 1) != 1:
                raise ValueError('Release requires single-replica application roles')
            container = next(c for c in live['spec']['template']['spec']['containers'] if c['name'] == mapping['container'])
            result[role] = {'image': container['image']}
            if role == 'controller':
                result[role]['worker_image'] = next((e.get('value') for e in container.get('env', []) if e['name'] == 'QSIM_WORKER_IMAGE'), None)
        return result

    def apply(self, role, images, sha):
        mapping = self.config['roles'][role]
        container = {'name': mapping['container'], 'image': images[ROLES[role]]}
        if role == 'controller':
            container['env'] = [{'name': 'QSIM_WORKER_IMAGE', 'value': images['worker'], 'valueFrom': None}]
        patch = {'spec': {'template': {'metadata': {'annotations': {'wqsim-release-sha': sha}},
                  'spec': {'containers': [container]}}}}
        self.kube('patch', 'deployment', mapping['deployment'], '--type=strategic', '-p', json.dumps(patch))
        self.kube('rollout', 'status', 'deployment/' + mapping['deployment'], '--timeout=300s', timeout=320)

    def deploy(self, root, sha, run_id):
        if not re.fullmatch('[0-9a-f]{40}', sha) or not str(run_id).isdigit():
            raise ValueError('Invalid release identity')
        ci_run = github('actions/runs/' + str(run_id))
        if not eligible({'repository': {'full_name': REPOSITORY}, 'workflow_run': ci_run}) or ci_run['head_sha'] != sha:
            raise ValueError('Successful exact-main push CI required')
        records = validate_bundle(root, sha)
        state_dir = Path(self.config['state_dir'])
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (state_dir / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            receipt_path = state_dir / 'deployment.json'
            if receipt_path.exists():
                previous = json.loads(receipt_path.read_text())
                if previous['status'] != 'verified':
                    raise ValueError('Prior deployment requires operator recovery')
                if previous['sha'] == sha:
                    return previous
            if not current(sha):
                raise ValueError('Release superseded')
            self.preflight_secrets()
            # This must fail on old application versions, before publishing or mutation.
            state = self.control('status', sha)
            if state['protocol'] != 1 or state['release']:
                raise ValueError('Bootstrap missing or admission already held')
            receipt = {'schema': 1, 'sha': sha, 'ci_run': str(run_id), 'status': 'preparing',
                       'started': time.time(), 'previous': self.capture(), 'artifacts': records}
            write_record(receipt_path, receipt)
            try:
                receipt['images'] = self.publish(root, records, sha)
                receipt['status'] = 'holding'
                write_record(receipt_path, receipt)
                self.control('hold', sha)
                self.wait_drain(sha)
                evidence_env = {**os.environ, 'WQS_RELEASE_SHA': sha}
                backup_set_id = uuid.uuid4().hex
                receipt['backup_requested_id'] = backup_set_id
                write_record(receipt_path, receipt)
                backup_env = {**evidence_env, 'WQS_BACKUP_SET_ID': backup_set_id}
                backup = verify_creation(run(self.config['backup_command'], 600, env=backup_env), backup_set_id)
                receipt['backup_id'] = backup['backup_id']
                receipt['backup_evidence_type'] = 'creation_result'
                receipt['backup_completed'] = time.time()
                if not current(sha):
                    raise ValueError('Release superseded before mutation; hold retained for operator')
                self.preflight_secrets()
                receipt['status'] = 'deploying'
                write_record(receipt_path, receipt)
                for role in ('controller', 'observer', 'admin', 'api'):
                    self.apply(role, receipt['images'], sha)
                # Only the private verifier's identity may submit during acceptance.
                self.control('smoke', sha)
                verification = json.loads(run(self.config['verify_command'], 600, env=evidence_env))
                verify_acceptance(verification, sha)
                receipt['acceptance'] = verification
                self.wait_drain(sha)
                receipt['status'] = 'acceptance_passed'
                write_record(receipt_path, receipt)
                self.control('open', sha)
                receipt['status'] = 'verified'
                receipt['completed'] = time.time()
                write_record(receipt_path, receipt)
                write_record(state_dir / (sha + '.json'), receipt)
                return receipt
            except BaseException:
                receipt['status'] = 'failed'
                write_record(receipt_path, receipt)
                raise


SMOKE_CASES = {'clifft_stim', 'clifft_qasm2_probability', 'qsim_qasm2_sample',
               'qsim_qasm2_probability', 'qsim_qasm3_sample', 'qsim_qasm3_probability',
               'aer_qasm2_sample', 'aer_qasm3_sample'}


def verify_acceptance(evidence, sha):
    if (evidence.get('sha') != sha or evidence.get('controller_progress') is not True
            or evidence.get('roles_healthy') != sorted(ROLES)
            or set(evidence.get('cases', {})) != SMOKE_CASES
            or any(value is not True for value in evidence['cases'].values())):
        raise ValueError('Complete exact-release live acceptance required')


def publish_version(config, sha, version):
    if not re.fullmatch(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version):
        raise ValueError('Expected immutable vMAJOR.MINOR.PATCH')
    if not re.fullmatch('[a-f0-9]{40}', sha):
        raise ValueError('Expected full release SHA')
    record = json.loads((Path(config['state_dir']) / (sha + '.json')).read_text())
    if record['sha'] != sha or record['status'] != 'verified':
        raise ValueError('Verified captured deployment required')
    verify_acceptance(record['acceptance'], sha)
    # gh reads credentials from the local environment; notes contain no inventory.
    tag = 'refs/tags/' + version
    existing = json.loads(run(['gh', 'api', 'repos/' + REPOSITORY + '/git/matching-refs/tags/' + version]))
    exact = [r for r in existing if r['ref'] == tag]
    if exact:
        if exact[0]['object']['type'] != 'commit' or exact[0]['object']['sha'] != sha:
            raise ValueError('Version already points elsewhere')
    else:
        run(['gh', 'api', '--method', 'POST', 'repos/' + REPOSITORY + '/git/refs',
             '-f', 'ref=' + tag, '-f', 'sha=' + sha])
    notes = ('Verified deployment of ' + sha + '.\n\nCI: https://github.com/' + REPOSITORY +
             '/actions/runs/' + record['ci_run'] + '\nAll three engines passed live acceptance.\n')
    # Creating a release is explicit. Existing releases are never edited or retagged.
    tags = run(['gh', 'api', '--paginate', 'repos/' + REPOSITORY + '/releases', '--jq', '.[].tag_name']).splitlines()
    if version in tags:
        return
    run(['gh', 'api', '--method', 'POST', 'repos/' + REPOSITORY + '/releases',
         '-f', 'tag_name=' + version, '-f', 'name=' + version, '-f', 'body=' + notes])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['gate', 'deploy', 'record'])
    parser.add_argument('--sha')
    parser.add_argument('--version')
    parser.add_argument('--run-id')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--artifacts', type=Path, default=Path('release'))
    args = parser.parse_args()
    try:
        if args.action == 'gate':
            sha = gate(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write('sha=' + sha + '\n')
        else:
            config = json.loads(args.config.read_text())
            if args.action == 'record':
                publish_version(config, args.sha, args.version)
                return
            receipt = Deployer(config).deploy(args.artifacts, args.sha, args.run_id)
            # Public receipt intentionally excludes private image names and inventory.
            Path('release-receipt.json').write_text(json.dumps({k: receipt[k] for k in ('schema', 'sha', 'ci_run', 'status')}) + '\n')
    except Exception as error:
        # Subprocess output, URLs and config can contain credentials.
        raise SystemExit('Release failed: ' + type(error).__name__ + '; inspect private state') from None


if __name__ == '__main__':
    main()
