# F3C.3.2 integration and OCI reconciliation

`reconcile_f3c3` verifies the F3C.2 package and F3C.3.2 bundles through
`verify_evidence_with_sboms`, which shares the existing verifier implementation.
Only after validating signatures, policy, materials, IDs, and provenance does it
pass SBOM bytes retained in the validated snapshot to reconciliation. Later
replacement of the original files does not change that snapshot. The earlier
`verify_evidence` API still returns only its summary.

The caller supplies policy and policy hash, key, commit, run, repository, image IDs,
platform, and distribution through independent channels. The existing verifier
requires the handoff, pinned Cosign and Trivy, and Docker and local images for
F3B.1 validation. Reconciliation confirms the supplied configuration and layer
DiffIDs against the image ID. The current profile requires the ID to match the
configuration digest; profiles whose ID identifies an OCI index are rejected
without fallback. The virtual reader accepts USTAR, the observed GNU header
variant, and local PAX only for `path`/`linkpath`. Limits: 128 MiB per layer,
256 MiB aggregate, 1 MiB per PAX payload, and 100.000 entries. Gzip, global PAX,
unknown PAX keys, and other TAR extensions remain rejected without fallback.

The result separates the trusted-key signature from the comparison report and
retains `COVERAGE_PARTIAL`. The signature does not prove builder identity,
independence of sources, authenticity of a platform obtained outside the image,
or completeness. The historical package was validated with disposable keys whose
bundles were not preserved; this orchestration has not yet been run with those
historical bundles. Local orchestration tests mock the verifiers; CI also exercises
real Cosign with disposable keys and negative cases.

Preserved image archives from run `41238810850` were checked by SHA-256 against
the handoff manifest and traversed to the final inventory without loading images
into Docker: backend, 12 layers, 224.294.912 bytes, 11.095 final entries, and
9.228 hashed files; nginx, 12 layers, 56.756.736 bytes, 2.214 entries, and
1.264 hashed files. Configuration hashes matched the handoff IDs and all DiffIDs
matched the configurations. This validates these images and observed formats,
without proving builder identity or complete coverage.

CI image archives are retained for one day; sanitized reports for seven days;
unsigned CI evidence for 30 days. The longer-lived evidence does not include
the layer handoff or disposable F3C.3.2 bundles. Coverage remains
`COVERAGE_PARTIAL`.
