# F3C.2 — CI evidence generation

The `ci-evidence (unsigned)` job runs only on `push` after `docker-build` succeeds. It is intentionally absent from `ci-required`; its failure is visible as a separate job and does not change the required gate or F2 vulnerability-audit states. It uses `contents: read`, does not receive signing keys, and does not create signatures or attestations.

The job creates CycloneDX 1.7 SBOMs for `backend/requirements.lock` and `frontend/package-lock.json`, plus the `backend-image` and `nginx-image` archives produced by the same workflow run. The image archives are selected by commit/run attempt, checked against their handoff manifest and hashes, loaded into the isolated runner's local Docker daemon, and compared to their recorded local image IDs. No container is started, and no pull or rebuild is performed. Other Compose images are outside this CI inventory.

The Python validator environment is installed from `security/sbom-tools.lock` with hashes. Trivy is installed by the existing F2 installer from its fixed inventory and verified before use. The existing F3A schema, lock coverage, image inventory, and F3B.1 statement validators run before publication. Statements remain `local-untrusted` with an empty builder identity; the consolidated manifest adds repository, commit, workflow run ID/attempt, per-file SHA-256 and byte counts, tool version/hash, durations, and coverage states. Python/npm must be `COVERAGE_PASS`; image records remain `COVERAGE_PARTIAL`.

Artifacts are named `ci-evidence-<commit>-<attempt>` and retained for 30 days. The evidence package is not signed. It does not establish authenticity, trusted builder identity, SLSA level, reproducible builds, or complete image inventories. The Trivy inventory is detected-only. The F3B.1 statement binds source paths as seen on the runner, so it is validated in the generating job; post-download checks below establish package consistency only.

## Download and check

Use the Actions run ID shown for the commit:

```sh
gh run download <run-id> --repo jacivaldocarvalho/jc-infrapulse \
  --name ci-evidence-<commit-sha>-<run-attempt>
sha256sum -c evidence-manifest.sha256
```

Then compare the downloaded commit/run metadata and each listed file hash in `evidence-manifest.json`. The sidecar and manifest are delivered in the same unsigned artifact: matching hashes detect accidental corruption, but do not authenticate the producer or prevent coordinated replacement. Do not treat `COVERAGE_PARTIAL` as a complete image inventory.
