# CycloneDX 1.7 schemas

These files are from the [CycloneDX specification 1.7 schema directory](https://github.com/CycloneDX/specification/tree/1.7/schema). The SHA-256 values in `scripts/sbom.py` pin all four files and are checked before validation. Validation uses the local references only.

The isolated validation environment is specified by `security/sbom-tools.lock`; install with pip's `--require-hashes` in a separate Python 3.12 environment. Existing system packages used during local validation were not installed from this lock and their artifact hashes were not verified.

Run `python scripts/sbom.py --trivy /path/to/trivy --kind python --source backend/requirements.lock --output /tmp/python-sbom.json`, or use `--kind npm --source frontend/package-lock.json`. The JSON manifest includes the source and SBOM hashes and coverage details. npm includes development dependencies; repeated installations of the same name and version are counted once. `COVERAGE_PASS` describes only identities represented by the selected lock, not installed or image contents. Unsupported lock entries and empty inventories fail closed. Without `--kind`, coverage remains `COVERAGE_PENDING`.

For a local image use `--image IMAGE --output /tmp/image-sbom.json`. Docker resolves the reference locally, and Trivy scans that immutable local image ID using only the Docker source. A `COVERAGE_PARTIAL` result means packages were detected but completeness is not established. Docker's RepoDigests are included as unverified observations; they are not asserted to be manifest digests. Missing images return `COVERAGE_PENDING`. No vulnerability database is downloaded. Retain CI SBOMs for 30 days when CI integration is designed; do not commit generated SBOMs.

Outputs must be outside the checkout and cannot be symlinks or aliases of an input. They are validated in a temporary file before atomic replacement. Lock coverage checks each package's purl against its name and version; unclassified components prevent a coverage pass. The single Trivy lock-file wrapper is excluded only when its type, name and Trivy properties match the scanned inventory.
