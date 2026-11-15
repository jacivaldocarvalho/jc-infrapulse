# F3B.2.2 — Local signing and offline verification

The `scripts/provenance_signature.py` module adds a cryptographic layer to F3B.1 provenance without changing its contract. Cosign 3.1.3 signs the exact statement bytes as a blob and writes a Sigstore JSON bundle. The verifier first revalidates the statement locally with F3B.1 (SBOM, manifest, commit, and materials), then checks the bundle with `cosign verify-blob --offline` and a public key from the local policy. The bundle is never a source of trust. The executable must match the version and SHA-256 in `security/cosign-tools.json`.

## Trust and policy

The policy is JSON outside the bundle, with this minimum structure:

```json
{
  "schema_version": 1,
  "keys": {
    "sha256:<SHA-256 dos bytes PEM>": {
      "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
      "sha256": "<mesmo SHA-256>",
      "status": "ACTIVE"
    }
  }
}
```

The operator must obtain the policy file's SHA-256 through an independent channel and provide it for every verification. Without that independent pin, a policy and key replaced together would not be detected. `ACTIVE` is the only accepted status; rotation adds a new key to the trusted policy, while revocation changes the old key to `REVOKED` and updates the independently distributed pin. In offline mode, an old pin cannot reflect new revocations: distributing the updated policy is an operational requirement.

## Usage

First generate the SBOM, manifest, and statement using the F3A/F3B.1 commands. Sign the statement file without changing it:

```sh
python scripts/provenance_signature.py sign \
  --statement /tmp/provenance.json --sbom /tmp/sbom.json \
  --manifest /tmp/manifest.json --trivy /path/to/trivy \
  --cosign /path/to/cosign --private-key /secure/path/cosign.key \
  --bundle /tmp/provenance.sigstore.json
```

Verify integrity and signature, supplying the policy pin obtained outside the evidence package:

```sh
python scripts/provenance_signature.py verify \
  --statement /tmp/provenance.json --sbom /tmp/sbom.json \
  --manifest /tmp/manifest.json --trivy /path/to/trivy \
  --cosign /path/to/cosign --bundle /tmp/provenance.sigstore.json \
  --policy /secure/path/trust-policy.json \
  --policy-sha256 <PIN_CONFIAVEL> --key-id sha256:<FINGERPRINT>
```

For an image, include `--expected-image-id sha256:...` in both commands. Create and keep the private key outside the repository, encrypted with a passphrase, under restricted access and backup controls. Never put the passphrase in arguments or logs; Cosign can receive it through secure input or `COSIGN_PASSWORD` in the process environment. A local signature alone does not identify a user, machine, or builder.

The states are independent: `INTEGRITY_VERIFIED` comes from F3B.1 validation; `SIGNATURE_VALID` means Cosign verified the signature over the exact bytes; `TRUSTED_SIGNER` means the key was active in the policy whose hash the operator pinned. Failure at any step ends with an error, without falling back to hashes alone. This does not prove SBOM completeness or trusted producer identity. No SLSA level is claimed. CI integration remains disabled; a future migration could move the key to KMS or use OIDC identity and workflow policy.

Inputs are opened without following symlinks and have size limits. The SBOM, manifest, and materials are revalidated before signing or verifying. Outputs are created in a restricted temporary location and replaced atomically; artifacts should be kept outside the checkout. The flow performs no downloads, network calls, scans, or container execution.
