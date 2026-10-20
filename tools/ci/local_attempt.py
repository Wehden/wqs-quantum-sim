"""Bounded child-workflow admission; test failures never become hosted retries.

Outputs: lane=local|hosted, outcome=success|failure|capacity_unavailable,
child_run_id, reason. A successful child job named 'Local capacity unavailable'
may explicitly report Kubernetes scheduling failure; ordinary failures cannot.
"""
import argparse
import json
import os
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class APIError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f'GitHub API request failed ({status})')


class API:
    def __init__(self, repository, token):
        self.root = 'https://api.github.com/repos/' + repository
        self.token = token

    def __call__(self, method, path, body=None):
        request = urllib.request.Request(self.root + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise APIError('oversized response')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            exc.close()
            raise APIError(exc.code) from None
        except (OSError, ValueError):
            raise APIError('transport') from None


def workflow_matches(path, workflow):
    if not isinstance(path, str):
        return False
    name, separator, ref = path.partition('@')
    return name == '.github/workflows/' + workflow and (not separator or bool(re.fullmatch(r'[A-Za-z0-9_./-]+', ref)))


def validate_parent(api, parent_id, attempt, sha, workflow='ci.yml'):
    if not str(parent_id).isdigit() or not str(attempt).isdigit() or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid parent identity')
    parent = api('GET', '/actions/runs/' + str(parent_id))
    if parent.get('run_attempt') != int(attempt) or not workflow_matches(parent.get('path'), workflow):
        raise ValueError('Parent workflow identity mismatch')
    if parent.get('status') == 'completed':
        raise ValueError('Parent is no longer active')
    if not parent.get('repository', {}).get('id') or parent.get('head_repository', {}).get('id') != parent.get('repository', {}).get('id'):
        raise ValueError('Untrusted parent repository')
    event = parent.get('event')
    if event in ('push', 'workflow_dispatch'):
        if parent.get('head_branch') != 'main' or sha != parent.get('head_sha'):
            raise ValueError('Untrusted parent ref')
    elif event == 'pull_request':
        # The run API's base SHA can lag the synthetic merge actually checked out.
        # Bind to GitHub's merge ref and the trusted head, never an arbitrary commit
        # with plausible parents. A moving merge ref fails closed.
        prs = [p for p in parent.get('pull_requests', []) if
               p.get('head', {}).get('repo', {}).get('id') == parent['repository']['id']
               and p.get('head', {}).get('sha') == parent.get('head_sha')
               and type(p.get('number')) is int and p['number'] > 0]
        commit = api('GET', '/git/commits/' + sha)
        parents = [p.get('sha') for p in commit.get('parents', [])]
        matched = False
        if len(parents) == 2 and parents[1] == parent.get('head_sha'):
            for pr in prs:
                merge_ref = api('GET', f"/git/ref/pull/{pr['number']}/merge")
                if merge_ref.get('object', {}).get('sha') == sha:
                    matched = True
                    break
        if not matched:
            raise ValueError('Checkout SHA is not the current trusted PR merge')
    else:
        raise ValueError('Untrusted parent event')
    return parent


class Controller:
    def __init__(self, api, clock=time.monotonic, sleep=time.sleep, record_child=None):
        self.api, self.clock, self.sleep = api, clock, sleep
        self.record_child = record_child or (lambda child: None)

    def jobs(self, run_id):
        jobs = []
        for page in range(1, 11):
            value = self.api('GET', f'/actions/runs/{run_id}/jobs?per_page=100&page={page}')['jobs']
            jobs.extend(value)
            if len(value) < 100:
                return jobs
        raise ValueError('Child job inventory exceeded bound')

    def cancel_and_confirm(self, run_id):
        try:
            self.api('POST', f'/actions/runs/{run_id}/cancel')
        except APIError as exc:
            if exc.status != 409:
                raise
        deadline = self.clock() + 60
        while self.clock() < deadline:
            run = self.api('GET', f'/actions/runs/{run_id}')
            if run['status'] == 'completed':
                return self.jobs(run_id)
            self.sleep(2)
        raise ValueError('Child cancellation not confirmed; hosted fallback withheld')

    def run(self, parent_id, attempt, sha, ref, workflow='ci.yml', job_name='Local execution',
            capacity_marker='Local capacity unavailable', queue_timeout=120, execution_timeout=1800):
        parent = validate_parent(self.api, parent_id, attempt, sha, workflow)
        if ref != parent['head_branch']:
            raise ValueError('Dispatch ref differs from parent branch')
        correlation = f'ci-local-{parent_id}-{attempt}-{sha}'
        workflow_path = '/actions/workflows/' + urllib.parse.quote(workflow, safe='')
        self.record_child({'child_run_id': None, 'correlation': correlation})
        try:
            self.api('POST', workflow_path + '/dispatches', {'ref': ref, 'inputs': {
                'local_parent_run': str(parent_id), 'local_parent_attempt': str(attempt), 'local_checkout_sha': sha}})
        except APIError as exc:
            if exc.status in (404, 422):
                self.record_child({'dispatch_rejected': True, 'correlation': correlation})
                return dict(lane='hosted', outcome='capacity_unavailable', child_run_id='', reason='Child workflow dispatch is not available on this ref.')
            raise  # Unknown POST outcome must not dispatch a second attempt.
        queue_deadline = self.clock() + queue_timeout
        child = None
        # Dispatch responses have no run ID. Discover only the unique parent/attempt/SHA title.
        while self.clock() < queue_deadline:
            runs = self.api('GET', workflow_path + '/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
            matches = [r for r in runs if r.get('display_title') == correlation]
            if len(matches) > 1:
                for match in matches:
                    self.cancel_and_confirm(match['id'])
                raise ValueError('Multiple child attempts found; fallback withheld')
            if matches:
                child = matches[0]
                break
            self.sleep(2)
        if child is None:
            raise ValueError('Dispatched child identity unresolved; fallback withheld')
        run_id = child['id']
        try:
            self.record_child({'child_run_id': run_id, 'correlation': correlation})
            if child.get('head_sha') != parent['head_sha']:
                raise ValueError('Child branch advanced; fallback withheld')
            return self.monitor(run_id, job_name, capacity_marker, queue_deadline, execution_timeout)
        except BaseException:
            self.cancel_and_confirm(run_id)
            raise

    def monitor(self, run_id, job_name, capacity_marker, queue_deadline, execution_timeout):
        execution_deadline = None
        while True:
            jobs = self.jobs(run_id)
            accepted = any(j['name'] == job_name and (j.get('runner_id') or 0) > 0 for j in jobs)
            if accepted and execution_deadline is None:
                execution_deadline = self.clock() + execution_timeout
            run = self.api('GET', f'/actions/runs/{run_id}')
            if run['status'] == 'completed':
                capacity = any(j['name'] == capacity_marker and j.get('conclusion') == 'success' for j in jobs)
                if accepted and run['conclusion'] == 'success' and capacity:
                    return dict(lane='hosted', outcome='capacity_unavailable', child_run_id=run_id, reason='Child explicitly reported scheduling unavailable.')
                passed = accepted and run['conclusion'] == 'success'
                return dict(lane='local', outcome='success' if passed else 'failure', child_run_id=run_id,
                            reason='Local attempt passed.' if passed else 'Local execution or child validation failed; no hosted retry.')
            if execution_deadline is not None and self.clock() >= execution_deadline:
                self.cancel_and_confirm(run_id)
                return dict(lane='local', outcome='failure', child_run_id=run_id, reason='Accepted local execution exceeded its bound; no hosted retry.')
            if execution_deadline is None and self.clock() >= queue_deadline:
                final_jobs = self.cancel_and_confirm(run_id)
                # Assignment can race cancellation. If it did, never hide execution.
                raced = any(j['name'] == job_name and (j.get('runner_id') or 0) > 0 for j in final_jobs)
                return dict(lane='local' if raced else 'hosted', outcome='failure' if raced else 'capacity_unavailable',
                            child_run_id=run_id, reason='Assignment raced cancellation; no hosted retry.' if raced else 'Local assignment timed out; child cancellation confirmed.')
            self.sleep(15 if execution_deadline is not None else 2)


def cleanup(api, state_file, parent_id, attempt, sha, workflow='ci.yml'):
    path = Path(state_file)
    if not path.exists():
        print('No known child receipt; nothing to reconcile.')
        return
    if path.stat().st_size > 4096:
        raise ValueError('Oversized child receipt')
    state = json.loads(path.read_text())
    correlation = f'ci-local-{parent_id}-{attempt}-{sha}'
    run_id = state.get('child_run_id')
    if state.get('correlation') != correlation:
        raise ValueError('Child receipt identity mismatch')
    if state.get('dispatch_rejected') is True:
        return
    if run_id is None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            runs = api('GET', '/actions/workflows/' + urllib.parse.quote(workflow, safe='') + '/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
            matches = [r for r in runs if r.get('display_title') == correlation]
            if len(matches) > 1:
                raise ValueError('Ambiguous child discovery during cleanup')
            if matches:
                run_id = matches[0]['id']
                break
            time.sleep(2)
        if run_id is None:
            raise ValueError('Dispatch outcome unresolved during cleanup')
    if type(run_id) is not int or run_id <= 0:
        raise ValueError('Child receipt identity mismatch')
    run = api('GET', f'/actions/runs/{run_id}')
    if (run.get('display_title') != correlation or run.get('event') != 'workflow_dispatch'
            or not workflow_matches(run.get('path'), workflow)
            or not run.get('repository', {}).get('id')
            or run.get('head_repository', {}).get('id') != run['repository']['id']):
        raise ValueError('Cleanup child identity mismatch')
    if run.get('status') != 'completed':
        Controller(api).cancel_and_confirm(run_id)
    print(f'Child {run_id} terminal state confirmed.')


def write_child_receipt(path, child):
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(child, out)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('--state-file', default=os.path.join(os.environ.get('RUNNER_TEMP', '.'), 'local-attempt.json'))
    parser.add_argument('--workflow', default='ci.yml')
    parser.add_argument('--job-name', default='Local execution')
    parser.add_argument('--execution-timeout', type=int, default=1800)
    args = parser.parse_args()
    if not 60 <= args.execution_timeout <= 14400:
        parser.error('execution timeout must be 60..14400 seconds')
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Controller interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    api = API(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
    identity = (os.environ['LOCAL_PARENT_RUN'], os.environ['LOCAL_PARENT_ATTEMPT'], os.environ['LOCAL_CHECKOUT_SHA'])
    if args.cleanup:
        cleanup(api, args.state_file, *identity, args.workflow)
        return
    if args.validate:
        parent = validate_parent(api, *identity, args.workflow)
        if os.environ['GITHUB_SHA'] != parent['head_sha']:
            raise ValueError('Child workflow SHA differs from parent head')
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f"parent_event={parent['event']}\nparent_head_sha={parent['head_sha']}\n")
        return
    result = Controller(api, record_child=lambda child: write_child_receipt(args.state_file, child)).run(*identity, os.environ['LOCAL_DISPATCH_REF'], args.workflow, args.job_name, execution_timeout=args.execution_timeout)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        for key, value in result.items():
            out.write(f'{key}={value}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
        out.write('## Local scheduling attempt\n\n' + result['reason'] + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
