#!/usr/bin/env bash
set -euo pipefail
umask 077

# A projected token file lets client-go refresh credentials without restarting.
mkdir -p /runner/.kube
cat > /runner/.kube/config <<'EOF'
apiVersion: v1
kind: Config
clusters:
- name: cluster
  cluster:
    server: https://kubernetes.default.svc
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
users:
- name: deployer
  user:
    tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
contexts:
- name: deployment
  context:
    cluster: cluster
    user: deployer
current-context: deployment
EOF
export KUBECONFIG=/runner/.kube/config
ready=false
for attempt in {1..30}; do
  if timeout 2 docker info >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo 'Deployment Docker daemon unavailable; runner will not accept jobs.' >&2
  exit 1
fi
cp -a --no-preserve=ownership /home/runner/. /runner/
cd /runner
if [[ ! -f .runner ]]; then
  : "${RUNNER_ORGANIZATION:?}" "${RUNNER_GROUP:?}"
  # Only a short-lived ORGANIZATION registration token; never a PAT.
  token=$(cat /registration/token)
  ./config.sh --unattended --url "$RUNNER_ORGANIZATION" \
    --token "$token" --runnergroup "$RUNNER_GROUP" --name wqs-deploy \
    --labels wqs-deploy --work /runner/_work --disableupdate
  unset token
fi
exec ./run.sh
