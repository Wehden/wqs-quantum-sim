# Deployment runner bootstrap

The deployment runner is separate from the ordinary test runner. Tests still
default to Standpunkt, with the existing hosted fallback. This package prepares
the production runner; installation and application acceptance are separate steps.
The example has zero replicas and the release config has `bootstrap_verified: false`.

## GitHub access boundary

WQS is public. A runner label does not stop another workflow from requesting a
runner with production credentials. Use an organization runner group named
`wqs-production`, restricted to the WQS repository and exactly
`Wehden/wqs-quantum-sim/.github/workflows/cd.yml@refs/heads/main`.
The CD workflow selects that group as well as the `wqs-deploy` label.
Do not register this runner at repository scope or make the group available to CI.

An organization administrator must configure the group. In
[`runner-group.example.json`](../tools/cd/runner-group.example.json), replace `0`
with the repository ID from `gh api repos/Wehden/wqs-quantum-sim --jq .id`.
Create the group through the organization settings or the runner-groups REST API.
Verify the actual server configuration with:

```sh
python3 tools/cd/check_runner_access.py
```

The check requires organization runner-group read access. An unavailable endpoint,
missing permission, extra repository or different workflow restriction blocks
registration. If the organization's GitHub plan cannot enforce selected workflows,
leave the runner stopped and resolve that access boundary first. Do not substitute
labels or a production environment for this restriction. See GitHub's
[runner-group access documentation](https://docs.github.com/en/enterprise-cloud@latest/actions/how-tos/manage-runners/self-hosted-runners/manage-access).

Create the `production` environment with a selected deployment branch policy for
`main` only. Keep `WQS_CD_ENABLED` unset or false during bootstrap. Do not place an
organization administration token on the runner. The registration token is a
short-lived organization token; delete its Secret after registration succeeds.
The runner retains its own registration credentials on its private PVC.

## Runner and Kubernetes configuration

Build from the reviewed commit using `tools/cd/runner.Dockerfile`. Run
`bash tools/ci/check-deployment-runner.sh` to check the tools in a non-root,
network-disabled container. CI repeats this on the hosted image lane, without
production access. Publish the verified runner image to the private registry and
pin its digest in a private rendering of
[`runner.example.yaml`](../tools/cd/runner.example.yaml).

The private rendering selects the intended Standpunkt node. It uses one Recreate
replica, persistent runner/release state and a dedicated Docker daemon with TLS.
It mounts no host directories or host Docker socket. The runner is non-root; the
Docker-in-Docker sidecar **is privileged and therefore host-sensitive**. Review
that placement before installation. It receives neither the Kubernetes token nor
private release files. The daemon's client certificate is mounted only in the
runner. There is no Service and an ingress-deny NetworkPolicy; allow outbound
GitHub, registry, DNS and Kubernetes API traffic under the cluster's policies.
The example does not constrain outbound traffic.

The 100 GiB claim is an initial storage allocation, not measured release capacity.
Check space for three image archives, loaded layers, runner work and retained
state before activation. Release cleanup removes only owned image tags and archives.
Monitor PVC usage; do not use broad Docker pruning on shared hosts.

Render [`runner-rbac.example.yaml`](../tools/cd/runner-rbac.example.yaml) for the
actual application namespace, four deployment names and required Secret names.
It grants named deployment patch/read/watch access and named Secret GET only.
Pod discovery and exec are namespace-wide because deployment pods have generated
names. Those permissions, and deployment patch access, make this a trusted
application operator. They are not isolation from application secrets. No
cluster-wide role, Secret write/list permission or node permission is granted.
The projected service-account token uses kubeconfig `tokenFile` so client-go can
reload it after rotation. The sidecar has no service-account mount.

## Private release configuration

Copy [`release-config.example.json`](../tools/cd/release-config.example.json) to a
private working directory and reconcile every deployment/container and required
Secret reference against live configuration. Include dependencies such as ingress
credentials when they are required for acceptance. Reconcile the RBAC names too.
Keep `bootstrap_verified` false. No credential values belong in this repository.

Provision `wqs-release-private` in the runner namespace through the established
cluster secret-management process. Its keys are:

| Key | Mounted path | Content |
| --- | --- | --- |
| `release.json` | `/private/release.json` | Private release configuration |
| `registry-config.json` | `/private/registry/config.json` | Docker authentication config; `{}` only if the registry needs no authentication |
| `backup` | `/private/hooks/backup` | Executable backup hook |
| `verify` | `/private/hooks/verify` | Executable acceptance hook |
| `smoke-api-key` | `/private/auth/smoke-api-key` | Dedicated identity matching `smoke_owner`; provision separately, never log |

Hooks must have a shebang and use tools available in the image. If additional
dependencies are required, add and test them before installing the hooks. Backup
must verify a complete recoverable set; acceptance must exercise all eight cases
in [the release contract](releases.md#private-configuration-contract). Missing
hooks keep the runner stopped. Never install success stubs. LaminarAuth owns key
custody and backup work; WQS owns the release hook integration and application
acceptance. Existing credentials are not rotated by this bootstrap.

The acceptance implementation and its installation prerequisites are documented
in [release hooks](release-hooks.md). Source tests do not authorize a live smoke run.

## Installation and acceptance sequence

1. Merge the runner changes and verify the organization group plus the production
   environment policy. Keep CD disabled.
2. Install the private rendered namespace, PVC, RBAC and zero-replica deployment.
   Provision the private files and short-lived registration Secret with key `token`.
3. Scale to one after reviewing the privileged sidecar. Verify the registered
   runner belongs to the restricted organization group, then delete the registration
   Secret. On restart the persistent `.runner` configuration avoids registration.
   Protect the PVC as credential-bearing storage; do not copy it into test runners.
4. Under the actual deployment identity, verify named Secret reads succeed and
   unlisted Secret reads, Secret listing/writes and cross-namespace operations fail.
   Confirm deployment inventory/watch and API exec permissions. Do not call
   `release.py deploy` as a permissions test: it can publish and mutate admission.
5. Set `WQS_RELEASE_CONFIG` to `/private/release.json`. Complete protocol bootstrap,
   private-overlay parity, backup restoration and interruption/recovery acceptance
   from [releases.md](releases.md#activation-and-bootstrap). Record evidence privately.
6. Only then mark `bootstrap_verified` true and enable `WQS_CD_ENABLED`. Observe
   the first qualifying main CI promotion through all engine checks and restored
   general admission. A green runner check alone is not production acceptance.

For backout before activation, leave CD disabled and scale the runner to zero.
Retain its PVC and release state. During an active release, first inspect the
admission/recovery record; stopping a runner does not reopen held admission.
