# F3C.4.2D — Preliminary OCI package reconciliation

The comparator records exact differences between coordinates (ecosystem, namespace,
name, version, and architecture) from two distinct sources, bound to the image ID and
platform expected by the caller. Version differences appear on both sides;
unsupported elements and explicit gaps are reported. Duplicate, invalid,
out-of-scope, or over-limit data is rejected. The result remains
`COVERAGE_PARTIAL`, even when inventories match.

The adapters read `var/lib/dpkg/status`, `lib/apk/db/installed`, and explicit
`site-packages/*.dist-info/METADATA` and `node_modules/*/package.json` files
from validated OCI layers in memory; they do not follow host symlinks. The PyPI
reader normalizes names and accepts a restricted version profile. Trivy purls
support restricted Debian, Alpine, PyPI, and npm profiles; known `arch`, `distro`,
and `epoch` values are preserved or compared with the expected distribution.
Unsupported qualifiers and formats are gaps. Implicit directories, unrecognized
files, npm bundles, other databases, and alternative installations are not covered.
The absence of entries does not establish that packages do not exist.

The SBOM is checked against an expected hash, the pinned CycloneDX 1.7 schema,
Trivy version metadata, graph, and image identity. Expected hashes, descriptors,
layer order, logical name, ID, distribution, and platform must come from
independent sources. Distinct identifiers do not prove independence; hash checks
and root identity do not authenticate the producer or bind the platform.

For trusted integration, the proposal is to verify the evidence package signature
with the existing Cosign verifier and a key/policy pinned through an independent
channel, verify hashes, and compare the full statements; obtain the platform from
authenticated OCI config/manifest data and compare it with the expected scope.
An ephemeral key or unsigned manifest establishes only integrity within the
described scope, not builder identity or a SLSA level. That authenticated link
and integration with the verifier have not yet been implemented.

Preserved historical SBOMs from run 41238810850 were inspected. They exercise
`distro`, `epoch`, Alpine versions with underscores, and PyPI. These files are
compatibility evidence, not an independent source of authenticity. The real
images have layers above the current USTAR reader limits, so they were not
reconstructed in this validation. Pending work includes fixtures derived from
real layers within limits, broader support, source authentication, independent
platform binding, and security review.
