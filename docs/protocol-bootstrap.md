# Controlled legacy protocol bootstrap

The live API may predate protocol 1 while containing deployment customizations
that are not represented by current main. Replacing its complete image is a
separate migration. `tools/cd/bootstrap_protocol.py` prepares a minimal overlay:
only the admission check in `api.py`, the additive release-control table in
`store.py`, and the reviewed `release_control.py` module. It does not deploy.

Extract the application sources from the recorded immutable API image. Review
and record their hashes privately. Invoke the tool with the extracted `qsim`
directory, a new output directory, and `--api-sha256` / `--store-sha256` pins.
It rejects changed source, already-enabled protocol source, ambiguous anchors,
and reuse of an output directory. The tool leaves the source tree unchanged.
Tests verify that its additions match the merged protocol implementation.

Before rollout, run the admission tests against an isolated copy of those exact
legacy sources with the overlay applied. Build a derived image from the recorded
API digest, copying only the three overlay files into `/app/qsim/`. Preserve the
base image's user, command, dependencies, static content, and runtime settings.
Record both source and resulting image digests. This is a separately tested
bootstrap image, not the normal CI release artifact.

The operator must record the current deployment and database recovery point,
confirm no competing deployment writer, and retain the previous API digest.
Replace only the API image using the existing Recreate strategy; preserve the
PVC, Secret references, replica count, and configuration. Check readiness,
existing smoke authentication, and `qsim.release_control status` before an
explicit hold/smoke/open rehearsal. Never reopen another release's hold or force
open while jobs remain undrained. A failed rehearsal leaves its hold for operator
investigation; image rollback alone does not clear admission.

Protocol bootstrap does not establish full release acceptance. Keep
`bootstrap_verified=false` until runtime configuration parity, backup/restore,
interruption recovery, retained images, and application acceptance are proven.
Do not modify credentials or replace backup verification with a success stub.
