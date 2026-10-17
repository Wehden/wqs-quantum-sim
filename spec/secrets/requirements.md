# WQS secret management requirements

Status: approved with the design on 2026-10-16.

- S1: Reuse existing Kubernetes Secret consumers and resource names. Add no secret-manager dependency unless discovery establishes a concrete unmet need.
- S2: Keep secret values, production hostnames and private deployment inventory out of public source, images, CI output and release artifacts.
- S3: Application CD must reference pre-provisioned secrets, validate required references without exposing values, and fail before rollout when a required dependency is missing. It must not generate, overwrite or rotate credentials.
- S4: Document each credential's owner, consumers, provisioning authority, rotation behavior and recovery needs in a private inventory. Limit workload permissions to its actual requirements.
- S5: Preserve API authentication, invitation delivery and encrypted pending-delivery recovery across changes. Explicitly test revocation propagation; do not promise instant propagation through projected Secret volumes.
- S6: Establish evidence for storage encryption, encrypted backups and a restore exercise before calling the secret-management posture complete.
- S7: Require owner review before implementation and normal PR/merge and deployment gates thereafter. Changes to running credentials require a specific rotation plan.
