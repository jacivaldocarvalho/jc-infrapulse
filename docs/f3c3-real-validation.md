# F3C.3.2 — Disposable runner integration check

This manually dispatched workflow checks one fixed historical run; it does not
publish signatures or artifacts and does not change `ci-required`. It requests
only `contents: read` and `actions: read`, runs on a fresh GitHub-hosted
`ubuntu-24.04` VM, has a 75-minute job timeout, and serializes runs without
cancelling an active one. Explicit step limits total 61 minutes; the remaining
14 minutes are reserved for checkout, runtime setup, and controller verification,
with the job timeout bounding the complete run. It has no OIDC permission,
repository secret, custom input, or privileged workflow trigger.

## Dispatch prerequisite

GitHub only offers `workflow_dispatch` after this workflow file is present on
the repository's default branch. The repository default branch is `main`; this
implementation does not change it. A change first landed only on `develop`
cannot be dispatched. Promotion to `main` requires the project's normal
separately authorized review/publication process.

## Fixed source materials and trust checks

The workflow accepts no run, attempt, commit, artifact-name, or image-ID inputs.
It pins F3C.2 run `41238810850`, attempt `1`, commit
`9f237be45ccd6e7977f50f85d37ebcc32d81a2bb`, and the approved F3C.3.1 code
commit `09a281533864c1c7c582a14ba08992ffa4976da4`. The controller is checked
out at the immutable `github.sha` for the dispatch event, and its checked-out
HEAD is compared with `GITHUB_SHA` before execution. GitHub API metadata must
show the expected successful `develop` push and exact artifact names from that
same run. The `docker-image-validation` report must identify `api` and
`frontend` as Linux/amd64 and its full image IDs must exactly match the IDs in
the handoff manifest. This cross-artifact check is corroboration, not an
independent cryptographic identity authority: artifacts from the same workflow
run are unsigned. The F3C.2 commit is checked out at the normal workspace root
so F3B.1 absolute lock-material paths retain the layout used when the
statements were produced; controller and validator checkouts live in sibling
subdirectories.

The helper checks GitHub's artifact SHA-256 digest when the API supplies one,
always checks downloaded byte counts and bounded archive sizes, requires exact
ZIP member allowlists, rejects duplicate names, links, traversal and malformed
members, and extracts with no-follow exclusive file creation into a private
`RUNNER_TEMP` directory. It checks available space before extraction and image
import. Any missing, expired, ambiguous, or divergent material fails closed.
The image archives are validated by the existing F3C.3.1 OCI parser and
canonicalizer before `docker load`. The workflow only loads and inspects images;
it never starts a container. Docker parsing of untrusted archives still exposes
the disposable runner daemon to parser risk, which is why no self-hosted or
shared local daemon is accepted.

## Validation and signatures

Hash-locked Python dependencies come from `security/sbom-tools.lock`; Trivy
0.76.1 is installed by the existing pinned installer and Cosign 3.1.3 is
checked against `security/cosign-tools.json`. The existing F3B.1 verifier
regenerates image SBOMs against the loaded local image IDs. A fresh Cosign test
key is created only in the private runner workspace. The workflow signs and
verifies the exact F3C.2 manifest, four provenance statements, and the OCI
canonicalization receipt with the existing offline trusted-key policy. Negative
checks reject modified bytes, a malformed bundle, a cryptographically valid
signature from a second test key that is unauthorized by the original policy,
a divergent fingerprint, and a modified SBOM. Test keys, policies, signatures,
and bundles are never uploaded and are removed with the workspace cleanup; the
hosted VM is disposable.

`INTEGRITY_VERIFIED`, `SIGNATURE_VALID`, and `TRUSTED_SIGNER` are reported as
separate checks. The ephemeral signer is trusted only by a temporary local
policy for this test. This proves neither builder authenticity nor producer
identity. No SLSA level, complete SBOM coverage, absence of vulnerabilities, or
public transparency-log evidence is claimed. Image coverage remains
`COVERAGE_PARTIAL`; Docker image import and F3B.1 inventory matching do not
prove scanner completeness or authenticate the original CI builder. The
informational vulnerability-audit result is not used as evidence that images
are vulnerability-free.

## Availability and cost limits

The historical handoff artifact has a short retention period. At implementation
time the recorded expiry is 2026-11-01; the workflow checks live API metadata and
will stop if it is expired. It never substitutes another run or attempt. The
runner checks current disk capacity against ZIP expansion, canonical copies,
and Docker image storage before using those resources; insufficient space is a
controlled failure. GitHub-hosted runner capacity and included private-repository
Actions minutes/storage vary by plan and account. This one-run check may consume
substantial disk and several minutes for artifact transfer, extraction, Trivy,
and image import. No artifacts are uploaded, so the workflow adds no artifact
retention usage. A successful run would validate this fixed package only; it
would not make the process an authenticated or reproducible builder.
