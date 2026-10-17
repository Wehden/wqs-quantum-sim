"""Read-only operator check. Never prints credentials or enables a runner."""
import json
import subprocess

REPOSITORY = 'Wehden/wqs-quantum-sim'
WORKFLOW = REPOSITORY + '/.github/workflows/cd.yml@refs/heads/main'
GROUP = 'wqs-production'


def check(group, repositories):
    if (group.get('name') != GROUP or group.get('visibility') != 'selected'
            or group.get('default') is not False or group.get('inherited') is not False
            or group.get('allows_public_repositories') is not True
            or group.get('restricted_to_workflows') is not True
            or group.get('selected_workflows') != [WORKFLOW]
            or repositories != [REPOSITORY]):
        raise ValueError('Deployment runner group does not have the required access restrictions')


def github(path, key):
    result = subprocess.run(['gh', 'api', '--paginate', path, '--jq', f'.{key}[] | @json'],
                            check=True, capture_output=True, text=True, timeout=60)
    return [json.loads(line) for line in result.stdout.splitlines()]


def main():
    groups = [group for group in github('orgs/Wehden/actions/runner-groups', 'runner_groups')
              if group['name'] == GROUP]
    if len(groups) != 1:
        raise ValueError('Expected one deployment runner group')
    group = groups[0]
    repositories = [repo['full_name'] for repo in github(
        f"orgs/Wehden/actions/runner-groups/{group['id']}/repositories", 'repositories')]
    check(group, repositories)
    print('PASS: only the WQS CD workflow on main can use the deployment runner group.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, subprocess.SubprocessError):
        raise SystemExit('Runner access could not be verified. Leave the runner stopped.') from None
