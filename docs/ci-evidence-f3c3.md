# F3C.3.1 — Local signing of the CI package

`scripts/ci_evidence_signature.py` signs an already downloaded F3C.2 package. By
default, its output contains five independent Sigstore JSON bundles: one for the
exact bytes of `evidence-manifest.json` and one for each statement (`python`,
`npm`, `backend-image`, `nginx-image`). It does not rewrite the manifest, statements,
or SBOMs. Before signing, the CLI validates the exact file set, sidecar, hashes and
sizes, commit, repository, run ID/attempt, coverage, materials, local OCI identity,
and F3B.1 contracts. Each created bundle is checked against independent policy
before the output directory is published atomically. Verification repeats package
validation and verifies all five bundles; it requires the matching checkout and,
for images, Docker, pinned Trivy, and local images for F3B.1 regeneration.

For images, `--handoff-dir` points to the `docker-build` directory exported by the
F3C.2 job. The CLI requires exactly `manifest.json`, `backend-image.tar`, and
`nginx-image.tar`; it reads the actual bytes to calculate SHA-256 and size and
compares them with the handoff manifest and evidence package materials. Static
validation accepts classic Docker save USTAR headers and the OCI profile observed
in `docker image save` 29.9.2. A bounded pre-pass checks checksums, offsets,
sizes, alignment, and terminators before `tarfile`; it rejects PAX/GNU, links,
special types, ambiguous paths, and truncated files. TAR directories are compared
with the canonical names returned by `tarfile`, while file names remain
unnormalized. `tarfile` must interpret exactly the same prevalidated members.

In the supported OCI profile, `oci-layout`, `index.json`, and the Docker-compatible
`manifest.json` are at the root. Two observed forms are accepted. In the nested
profile, the root index contains exactly one nested OCI index descriptor; its
digest, checked against the blob, is the local image ID supplied independently
by the Docker daemon. The nested index contains exactly one image manifest for
one platform and one related Docker attestation manifest. In the direct profile,
the root index contains exactly one manifest descriptor without a `platform`
field; the descriptor is checked against the manifest bytes. In this profile,
the independent image ID must equal the config digest, not the index or manifest
digest. The config must declare `linux/amd64`. Both profiles validate config and
layer digests and sizes, DiffIDs in declared order, and agreement with the
Docker-compatible `manifest.json`. The direct profile accepts only uncompressed
OCI TAR layers and also validates `LayerSources`; the nested profile accepts
gzip layers and verifies their decompressed contents. The nested attestation
payload is checked by digest and size without treating it as provenance evidence.

The profiles are intentionally narrow: one image/platform, a root index with one
descriptor, the layer types defined above, and, in the nested profile, the
attestation in the observed layout. External URLs, unknown descriptors,
unreferenced blobs, and extra entries are rejected. Multi-platform OCI, other
layout variants, and unobserved formats fail closed. The flow does not run
`docker load`, start containers, or pull images. Limits are 2 GiB per archive,
4 GiB total, 100.000 members, 2 GiB of expanded content per archive, and 16 MiB
for config/manifest. Each archive is read for hashing and validation; this adds
I/O and CPU proportional to its size (up to two reads, with no additional
permanent copy).

F3B.1 validation still queries the local Docker daemon to resolve the expected
image ID and regenerate and compare the inventory with Trivy. The daemon and
operator's host are therefore trust boundaries: keep them under control and use
only expected local images. The received archive is untrusted data and is parsed
without loading it into the daemon; manifest hashes alone do not authorize its
contents.

`sign-blob` is called with `--tlog-upload=false` and
`--use-signing-config=false`: it uses the local key without submitting an entry
to Rekor or fetching service TUF configuration. These flags appear in the
[Cosign v3.1.3 contract](https://github.com/sigstore/cosign/blob/v3.1.3/cmd/cosign/cli/options/sign.go#L742-L799).
During verification, `verify-blob` uses `--offline`, `--insecure-ignore-tlog`,
and the materialized public key from the independent policy whose fingerprint
was validated. The bypass is limited to proof of inclusion in the transparency
log: Cosign still verifies the cryptographic signature of the exact bytes with
that key. Because the signature is not submitted to Rekor, there is no inclusion
proof, timestamp/public transparency, or public verifiability; `TRUSTED_SIGNER`
means local trust in the authorized fingerprint, not trust in a log or external
operator identity.

## Optional local canonicalization

The default mode still requires original archives accepted by the strict OCI
parser. For historical handoffs containing only the orphaned legacy Docker JSON
recognized by the exporter, pass `--canonicalize-oci` to both `sign` and
`verify`. Do not replace or edit the handoff or F3C.2 files.

In this mode, each operation first checks the original archives' SHA-256 and sizes
against the handoff manifest and F3C.2 package materials. It then runs
`oci_canonicalizer` in private temporary locations, explicitly supplies expected
image IDs, revalidates canonical archives, and confirms their actual bytes. The
deterministic receipt `image-archive-transformation.json` records commit, run,
attempt, the exact evidence manifest hash, the original handoff manifest hash,
and, for each image, identity, profile, transformation, original and canonical
hashes/sizes, and removed blobs. The receipt and
`image-archive-transformation.sigstore.json` are published with the bundles,
without rewriting the manifest or historical statements.

Cosign signs the receipt's exact bytes with the same key and policy used for the
other objects. `verify --canonicalize-oci` repeats canonicalization from the
original archives, compares the stored receipt byte for byte, and verifies its
signature. A missing archive or a mismatch in hash/size, ID, profile, execution/
run identity, or receipt fails; there is no fallback to strict mode. Without the
option, the verifier rejects a signature directory containing the extra receipt.

```sh
python scripts/ci_evidence_signature.py sign ... --canonicalize-oci
python scripts/ci_evidence_signature.py verify ... --canonicalize-oci
```

This record binds the transformation to the original F3C.2 package, but neither
changes its historical materials nor proves compatibility with `docker load`.

## Preparation and usage

Obtain the `ci-evidence-<commit>-<attempt>` artifact from the intended run and
extract it into a dedicated directory. Use Cosign 3.1.3 verified against
`security/cosign-tools.json`, Trivy verified by the F2 inventory, and a checkout
of the exact expected commit. Image IDs must come from an independent local
source (for example, operator-run `docker image inspect`); do not copy them from
the SBOM or manifest. Do not pull or run the image. Image validation requires
the image ID to be available locally.

Provide the commit, repository, run ID, attempt, and both image IDs explicitly.
`--policy-sha256` is the trusted policy hash obtained through an independent
channel; `--key-id` is its fingerprint. Supply the private key only by an external
path controlled by the operator. The tool creates restricted temporary snapshots;
protect the host and remove those temporary files after the operation. Never place
the key, password, or policy only inside the signed package. Output must go to a
new path outside the input package. The verifier uses independent policy and
offline Cosign verification; it does not trust a key embedded in the bundle.

```sh
python scripts/ci_evidence_signature.py sign \
  --evidence-dir /secure/run/evidence \
  --handoff-dir /secure/run/docker-build \
  --signature-dir /secure/run/signatures \
  --private-key /secure/keys/cosign.key \
  --cosign /verified/cosign --trivy /verified/trivy \
  --policy /secure/trust/trust-policy.json \
  --policy-sha256 <PIN_INDEPENDENTE> --key-id sha256:<FINGERPRINT> \
  --expected-commit <40_HEX> --expected-run-id <RUN_ID> \
  --expected-run-attempt <ATTEMPT> \
  --expected-repository jacivaldocarvalho/jc-infrapulse \
  --expected-backend-image-id sha256:<64_HEX> \
  --expected-nginx-image-id sha256:<64_HEX>

python scripts/ci_evidence_signature.py verify \
  --evidence-dir /secure/run/evidence \
  --handoff-dir /secure/run/docker-build \
  --signature-dir /secure/run/signatures \
  --cosign /verified/cosign --trivy /verified/trivy \
  --policy /secure/trust/trust-policy.json \
  --policy-sha256 <PIN_INDEPENDENTE> --key-id sha256:<FINGERPRINT> \
  --expected-commit <40_HEX> --expected-run-id <RUN_ID> \
  --expected-run-attempt <ATTEMPT> \
  --expected-repository jacivaldocarvalho/jc-infrapulse \
  --expected-backend-image-id sha256:<64_HEX> \
  --expected-nginx-image-id sha256:<64_HEX>
```

Do not interpret a successful informational audit as absence of vulnerabilities.
Python/npm remain `COVERAGE_PASS`; image inventories remain `COVERAGE_PARTIAL`.
An equal regenerated inventory does not prove completeness. The states
`INTEGRITY_VERIFIED`, `SIGNATURE_VALID`, and `TRUSTED_SIGNER` are distinct:
signature and authorization do not establish completeness, producer identity,
a trusted builder, authenticity of the generation process, or a SLSA level.
F3C.2 remains unsigned automatically in CI.

## Trust, operations, and limits

Distribute the public policy and its SHA-256 through an independent channel.
Only `ACTIVE` keys are accepted; to revoke or rotate, update the policy and
redistribute its new pin. Offline verification cannot learn of a revocation that
has not been distributed. Keep the private key outside the repository with
access controls, backups, and operational rotation; this stage does not create
or protect operational keys. Signing requires five signing invocations and local
verifications; image validation also repeats F3B.1 inventory regeneration and
may consume CPU and time. No artifacts are published and no containers are run;
Docker is used only to inspect the local image during F3B.1 validation.

Evidence files are opened without following symlinks, limited to 64 MiB each and
128 MiB total, and copied into snapshots before use. The publication directory
and its ancestors must belong to root or the current user and must not be
group/world writable, except sticky directories such as `/tmp`; all are opened
without following symlinks, and descriptors remain open. The parent is
revalidated before and after publication. Temporary bundles use 0700/0600
permissions and are published together by atomic rename without overwriting.
Failures remove owned temporary files. Run in a controlled local directory
without concurrent writers: descriptor protection prevents redirection to
another directory after validation, but does not secure against processes with
the same UID or equivalent privileges; these remain within the operator's trust
boundary. Source substitution at rename time fails, the displaced object is
restored without overwriting entries, and files in the open temporary directory
are cleaned up. Image IDs are immutable local identifiers, not registry manifest
digests. A signature binds bytes and declared identity; it does not attest that
the scanner found every component.

## Validation status of this implementation

Ruff was unavailable in the local validation environment; running it remains
pending. Real end-to-end F3B.1 + Cosign integration also remains unvalidated
because no suitable isolated Docker/VM environment was available. The 40/40
local unit tests PASS do not replace that integration. This milestone is a
technical implementation, not an operational release.
