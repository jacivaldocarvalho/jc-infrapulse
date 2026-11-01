# Local OCI archive canonicalization

`scripts/oci_canonicalizer.py` creates a separate canonical OCI archive for
local use. It does not change the F3C.2 handoff, source archives, CI workflow, or
publish artifacts. Call `canonicalize_oci_archive(source, destination,
expected_image_id)` with the full local image ID (`sha256:` plus 64 lowercase
hexadecimal characters). The exporter compares this supplied value with the
identity calculated from the OCI config; it does **not** authenticate how the
value was obtained. The caller must obtain it from an independent, trusted
image inspection source, separate from the archive and its metadata. Passing an
image ID copied only from the archive or its handoff manifest does not establish
independent identity.

The destination must be new and its parent must be a private, operator-owned
directory. Such permissions isolate files from other unprivileged UIDs, but do
not isolate processes running as the same UID or with equivalent privileges.
Those processes can access and modify temporary files and are part of the local
trust boundary. The exporter does not claim protection against concurrent
tampering by them.

The exporter accepts only the OCI layouts already validated by
`scripts/ci_evidence_signature.py`: a direct manifest for linux/amd64 or the
supported nested single-platform index and attestation profile. The shared
validator checks descriptor digests and sizes, manifest/config/layer links,
config-derived image ID, layer order and DiffIDs. The exporter additionally
checks source stability and TAR parser agreement before copying raw member
records. Retained member headers, payloads, and padding are copied byte for
byte. It removes only orphan `blobs/sha256/<digest>` JSON records matching the
bounded legacy Docker layer-metadata schema, with valid content-addressed names
and a coherent parent chain. Other extra files, blobs, malformed metadata,
classic Docker-save archives, and unsupported OCI profiles are rejected.

The result reports separate source and canonical SHA-256 values and byte sizes,
the local image ID, profile, and removed blob paths. The output is created with
mode 0600 in the private destination directory and published atomically without
overwriting an existing path. It is a derived copy; its hash must not be
substituted for the original handoff hash or reused as evidence for another
run.

No `docker load` compatibility is claimed or tested. Successful static
validation proves only that the supported OCI inventory and retained bytes
match the independently supplied local image ID under this parser contract; it
does not prove Docker daemon acceptance, registry identity, producer
authenticity, or inventory completeness.
