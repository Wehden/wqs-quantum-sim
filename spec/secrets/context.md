# WQS secret management context

Status: design and implementation approved on 2026-10-16. Production changes remain separately gated.

The owner requested a System2 design after discussing whether the cluster needs a vault and noting an existing secret store. This packet interprets that request as improving WQS secret management using the existing Kubernetes Secrets facility. It does not authorize adopting a new backend.

Read-only discovery found four WQS Secrets: qsim-users, qsim-admin, qsim-smtp and cloudflared-qsim-token. The deployment and StatefulSet inventory contained no identifiable Vault service; CRDs contained no External Secrets or Sealed Secrets resources. This does not exclude an external store or host-managed tooling. Encryption at rest, backup encryption and upstream provisioning remain unverified. No secret values were retrieved for this discovery.

Source references: docs/deployment.md, qsim/auth.py and the existing release/CD workflow. Keep this packet separate from tenancy, welcome-provider and release/CD feature packets. Existing uncommitted work is preserved.
