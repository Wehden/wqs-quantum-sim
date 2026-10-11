"""Select the first CI attempt without runner inventory credentials."""
import json
import os
from pathlib import Path


def choose(event, kind, repository, ref, enabled):
    trusted = (kind == 'push' and ref == 'refs/heads/main') or (
        kind == 'pull_request' and event.get('pull_request', {}).get('head', {}).get('repo', {}).get('full_name') == repository
    ) or (kind == 'workflow_dispatch' and ref == 'refs/heads/main')
    if not trusted:
        return 'hosted', 'This event is not eligible for the private runner.'
    if enabled == 'false':
        return 'hosted', 'Standpunkt CI is disabled by repository configuration.'
    return 'local', 'Standpunkt is the default; GitHub-hosted CI retries if the local attempt fails.'


def main():
    lane, reason = choose(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()),
                          os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REPOSITORY'],
                          os.environ['GITHUB_REF'], os.environ.get('LOCAL_ENABLED', 'true'))
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
