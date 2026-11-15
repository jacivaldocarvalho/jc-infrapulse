# F3B.1 — Local SBOM provenance

## Local SBOM

The `scripts/provenance.py` runner emits JSON using the [in-toto Statement v1 and SLSA Provenance v1 predicate](https://slsa.dev/spec/v1.0/provenance). This implementation's build type is the URI in that section. The subject is the SBOM; the F3A manifest links the commit, hash, and source. The local inventory and Trivy executable are recorded as dependencies when available. For images, `imageId` is a local ID, not a remote manifest digest. The environment is `local-untrusted` and `builder` is empty. The F3A manifest does not provide an invocation ID, so none is invented.

In the isolated environment described by `security/sbom-tools.lock`, generate the SBOM manifest with the F3A runner and redirect its stdout to a file outside the checkout. Then run:

```
python scripts/provenance.py generate --sbom /tmp/sbom.json --manifest /tmp/manifest.json --trivy /path/to/trivy --statement /tmp/provenance.json
python scripts/provenance.py verify --sbom /tmp/sbom.json --manifest /tmp/manifest.json --trivy /path/to/trivy --statement /tmp/provenance.json
```

The verifier requires the same commit in the checkout, compares the manifest and local file hashes, and validates the SBOM and Trivy version and hash. `INTEGRITY_VERIFIED` means the available data is consistent; `NOT_VERIFIED` means authenticity has not been established. Verification fails if materials are unavailable or have changed.

For images, provide `--expected-image-id sha256:...` in **both** commands, obtained independently of the SBOM and manifest from a source trusted by the operator. The full ID must be available in local Docker. The verifier resolves that ID, regenerates the SBOM offline with the pinned Trivy using Docker only, and compares components and dependency graphs; it neither runs containers nor pulls images. A local image ID is not a registry manifest digest. The comparison preserves multiplicity, properties, and identifiers; random `bom-ref` values are replaced by each component's full identity, and relationships are mapped to those identities. List order, timestamps, and global metadata are excluded from the compared inventory. Ambiguous references, unsupported relationships, and differences cause failure. Each run incurs the cost of reading the image and generating another temporary SBOM; it is not a vulnerability scan.

A match means both inventories were detected by the same Trivy version in the expected local image. It does not prove complete detection, integrity of the ID's source, a signature, producer identity, or authenticity. Image results remain `COVERAGE_PARTIAL`. This comparison relies on a trustworthy local Docker daemon, Trivy binary, and environment.

For file inventories, only the exact absolute paths `backend/requirements.lock` and `frontend/package-lock.json` within the checkout are accepted. The verifier rejects symlinks and special files, limits size to 10 MiB, opens materials through file descriptors without following links, and recomputes F3A coverage; a mismatch in type, components, or coverage fails. Descriptor-based validation reduces path-substitution races, but does not make a checkout controlled by another party trustworthy. Other lockfiles and generic inventories are not supported at this stage.

The statement is produced after generation from an unsigned manifest. It does not prove the reported process occurred, that the verified binary was used during generation, that all materials were captured, or that the environment or builder is trustworthy. There is no signature, authenticated attestation, or SLSA-level claim. F3B.2 depends on verifiable identity and signature; F3C depends on CI integration and artifact management, outside this scope.
