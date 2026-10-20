"""Select the first CI attempt without runner inventory credentials."""
import json
import os
import math
import time
from pathlib import Path


def available(status, now):
    """Fail closed on unavailable or inconsistent publisher evidence."""
    try:
        value = json.loads(status)
        if not isinstance(value, dict):
            return False
        if type(value.get('schema')) is not int or value['schema'] != 1:
            return False
        for field in ('capacity', 'online', 'busy', 'idle'):
            if type(value.get(field)) is not int or value[field] < 0:
                return False
        observed = value.get('observed_at')
        if type(observed) not in (int, float) or not math.isfinite(observed):
            return False
        if not -15 <= now - observed <= 120:
            return False
        return (value['capacity'] == 2 and value['online'] <= 2
                and value['busy'] + value['idle'] == value['online'] and value['idle'] > 0)
    except (ValueError, TypeError, OverflowError):
        return False


def choose(event, kind, repository, ref, enabled, status='', now=None):
    trusted = (kind == 'push' and ref == 'refs/heads/main') or (
        kind == 'pull_request' and event.get('pull_request', {}).get('head', {}).get('repo', {}).get('full_name') == repository
    ) or (kind == 'workflow_dispatch' and ref == 'refs/heads/main')
    if not trusted:
        return 'hosted', 'This event is not eligible for the private runner.'
    if kind == 'workflow_dispatch' and event.get('inputs', {}).get('hosted_tests') in (True, 'true'):
        return 'hosted', 'Explicit hosted dependency-validation run; Standpunkt skipped.'
    if enabled != 'true':
        return 'hosted', 'Standpunkt CI is disabled by repository configuration.'
    if not available(status, time.time() if now is None else now):
        return 'hosted', 'No fresh, consistent idle capacity in the two-slot local CI pool.'
    return 'local', 'A local CI slot is reported idle; GitHub-hosted CI retries if the local attempt fails.'


def main():
    lane, reason = choose(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()),
                          os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REPOSITORY'],
                          os.environ['GITHUB_REF'], os.environ.get('LOCAL_ENABLED', ''), os.environ.get('CI_LOCAL_RUNNER_STATUS', ''))
    selected = 'Standpunkt' if lane == 'local' else 'GitHub-hosted Ubuntu'
    other = 'GitHub-hosted Ubuntu (fallback only)' if lane == 'local' else 'Standpunkt (skipped)'
    summary = f'## Initial CI path\n\n- First attempt: **{selected}**\n- Other path: **{other}**\n- Reason: {reason}\n'
    print(summary)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
        out.write(summary)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write(f'lane={lane}\n')


if __name__ == '__main__':
    main()
