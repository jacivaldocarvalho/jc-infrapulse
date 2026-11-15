# F3C.4.3 and F3C.4.4 — Evidence preparation

The preliminary report in `scripts/oci_reconciliation_report.py` versions a
comparison summary and byte hashes supplied by the caller. It explicitly records
that the bytes were not bound to the comparison, source, independence, and platform
were not verified, and complete coverage was not proved. Hashes are labeled
unverified. The state is always `COVERAGE_PARTIAL`; this object does not change
the v1 coverage contract and is not evidence of origin, authenticated integrity,
or operational approval.

The report's suite runs in the existing `security-foundations` job. This validates
the summary's local behavior; it does not integrate the report into coverage gates.
Completing the phases still requires integration with the existing signed evidence
package, a reviewed contract and migration, end-to-end negative tests, and defined
retention and gates. None of those guarantees can be inferred from an object or
boolean supplied by the caller.

An experimental flow, `oci_reconciliation_pipeline.py`, reconstructs the
comparison from layer and SBOM bytes, verifying the configuration digest,
`linux/amd64` platform, and DiffID order. Its output has its own schema in
`security/oci-reconciliation-report-v1.schema.json`, with fixed unverified-source
and unverified-independence states and mandatory gaps. The schema does not prove
signatures or authenticity; independent sources remain responsible for the
manifest, distribution, and producer. The flow is tested in the security job,
but publishes no evidence and changes neither gates nor the v1 coverage contract.

`compare_signed_image` verifies Cosign signatures over the exact bytes of the
direct OCI manifest and SBOM, using a key, policy, and policy hash pinned outside
the package. It checks descriptors against configuration and layers and verifies
DiffIDs and platform. Separate bundles are required for the signatures on those
two blobs; the signed historical F3C.3.2 artifacts use different packaging and
are not yet integrated with this function. A signature proves key authorization
over bytes, not independence, builder identity, or completeness. Local tests of
this orchestration mock the cryptographic verifier; real validation with Cosign
and disposable keys remains pending.

Read-only inspection of historical OCI files from run 41238810850: the largest
candidate entries are 100.435.968 bytes (backend) and 41.925.632 bytes
(frontend), above the 16 MiB per-layer limit. Among candidate USTAR blobs up to
16 MiB, the reader accepted 8 in the backend and 8 in the frontend; it rejected
1 for an unsupported TAR type and 3 for non-normalized paths, respectively. This
does not validate the whole image or the files' origin, or replace integrated
testing with authenticated images and signatures.

The `security-foundations` job also runs `validate_signed_oci_fixture.py` on a
GitHub-hosted runner. The routine creates a minimal OCI image in memory and two
temporary keys, installs Cosign from the existing pin, and tests manifest and
SBOM signatures plus seven negative cases. No key or signature is published;
the temporary directory is removed at the end. This test does not change
coverage gates or replace F3C.3.2 package integration or real-image validation.
The result of that run should be checked in the PR's Actions page.
