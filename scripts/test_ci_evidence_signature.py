"""Offline F3C.3 artifact-signing orchestration regressions."""

import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import ci_evidence_signature as evidence_signature
import oci_canonicalizer
import provenance
import provenance_signature as signature
import sbom
import vulnerability_ci

COMMIT = "a" * 40
RUN_ID = "123456"
ATTEMPT = 2
REPOSITORY = "jacivaldocarvalho/jc-infrapulse"


def _tar_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as archive:
        for name, contents in entries:
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            if name.endswith("/") and not contents:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                archive.addfile(member, io.BytesIO(contents))
    return stream.getvalue()


def _docker_archive(
    label: str,
    *,
    extra: bool = False,
    alternate_config: bool = False,
    duplicate_alias: bool = False,
) -> tuple[str, bytes]:
    layer = _tar_bytes([(f"{label}.txt", label.encode())])
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {
                "Labels": {"fixture": "alternate" if alternate_config else label}
            },
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
            },
        },
        separators=(",", ":"),
    ).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    config_path = image_id.split(":", 1)[1] + ".json"
    docker_manifest = json.dumps(
        [
            {
                "Config": config_path,
                "RepoTags": [f"infrapulse/{label}:test"],
                "Layers": ["layer/layer.tar"],
            }
        ],
        separators=(",", ":"),
    ).encode()
    entries = [
        ("manifest.json", docker_manifest),
        (config_path, config),
        ("layer/layer.tar", layer),
    ]
    if extra:
        entries.append(("extra.txt", b"harmless archive metadata"))
    if duplicate_alias:
        entries.extend([("collision", b"file"), ("collision/", b"")])
    return image_id, _tar_bytes(entries)


def _oci_docker_archive(
    label: str,
    *,
    duplicate_platform: bool = False,
    external_layer: bool = False,
    wrong_diff_id: bool = False,
) -> tuple[str, bytes, str]:
    layer_tar = _tar_bytes([(f"{label}.txt", label.encode())])
    layer_blob = gzip.compress(layer_tar, mtime=0)
    layer_digest = "sha256:" + hashlib.sha256(layer_blob).hexdigest()
    diff_id = "sha256:" + hashlib.sha256(layer_tar).hexdigest()
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"Labels": {"fixture": label}},
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + "0" * 64 if wrong_diff_id else diff_id],
            },
        },
        separators=(",", ":"),
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    config_descriptor = {
        "mediaType": evidence_signature.OCI_CONFIG_MEDIA_TYPE,
        "digest": config_digest,
        "size": len(config),
    }
    layer_descriptor = {
        "mediaType": evidence_signature.OCI_GZIP_LAYER_MEDIA_TYPE,
        "digest": layer_digest,
        "size": len(layer_blob),
    }
    if external_layer:
        layer_descriptor["urls"] = ["https://example.invalid/layer"]
    image_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
            "config": config_descriptor,
            "layers": [layer_descriptor],
        },
        separators=(",", ":"),
    ).encode()
    image_manifest_digest = "sha256:" + hashlib.sha256(image_manifest).hexdigest()
    image_descriptor = {
        "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
        "digest": image_manifest_digest,
        "size": len(image_manifest),
        "platform": {"os": "linux", "architecture": "amd64"},
    }
    empty_config = b"{}"
    empty_digest = "sha256:" + hashlib.sha256(empty_config).hexdigest()
    attestation_payload = b'{"fixture":"docker-attestation"}'
    attestation_layer_digest = (
        "sha256:" + hashlib.sha256(attestation_payload).hexdigest()
    )
    attestation_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
            "artifactType": evidence_signature.DOCKER_ATTESTATION_MEDIA_TYPE,
            "config": {
                "mediaType": evidence_signature.OCI_EMPTY_MEDIA_TYPE,
                "digest": empty_digest,
                "size": len(empty_config),
                "data": "e30=",
            },
            "layers": [
                {
                    "mediaType": evidence_signature.IN_TOTO_MEDIA_TYPE,
                    "digest": attestation_layer_digest,
                    "size": len(attestation_payload),
                    "annotations": {
                        "in-toto.io/predicate-type": "https://slsa.dev/provenance/v1"
                    },
                }
            ],
            "subject": {
                "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
                "digest": image_manifest_digest,
                "size": len(image_manifest),
            },
        },
        separators=(",", ":"),
    ).encode()
    attestation_digest = "sha256:" + hashlib.sha256(attestation_manifest).hexdigest()
    attestation_descriptor = {
        "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
        "digest": attestation_digest,
        "size": len(attestation_manifest),
        "platform": {"os": "unknown", "architecture": "unknown"},
        "annotations": {
            "vnd.docker.reference.digest": image_manifest_digest,
            "vnd.docker.reference.type": "attestation-manifest",
        },
    }
    nested_descriptors = [image_descriptor, attestation_descriptor]
    if duplicate_platform:
        nested_descriptors.append(image_descriptor)
    nested_index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_INDEX_MEDIA_TYPE,
            "manifests": nested_descriptors,
        },
        separators=(",", ":"),
    ).encode()
    image_id = "sha256:" + hashlib.sha256(nested_index).hexdigest()
    root_index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_INDEX_MEDIA_TYPE,
            "manifests": [
                {
                    "mediaType": evidence_signature.OCI_INDEX_MEDIA_TYPE,
                    "digest": image_id,
                    "size": len(nested_index),
                    "annotations": {
                        "io.containerd.image.name": f"infrapulse/{label}:test",
                        "org.opencontainers.image.ref.name": f"infrapulse/{label}:test",
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    docker_manifest = json.dumps(
        [
            {
                "Config": f"blobs/sha256/{config_digest.split(':', 1)[1]}",
                "RepoTags": [f"infrapulse/{label}:test"],
                "Layers": [f"blobs/sha256/{layer_digest.split(':', 1)[1]}"],
            }
        ],
        separators=(",", ":"),
    ).encode()
    blobs = {
        image_id: nested_index,
        image_manifest_digest: image_manifest,
        config_digest: config,
        layer_digest: layer_blob,
        attestation_digest: attestation_manifest,
        empty_digest: empty_config,
        attestation_layer_digest: attestation_payload,
    }
    entries = [
        ("blobs/", b""),
        ("blobs/sha256/", b""),
        ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ("index.json", root_index),
        ("manifest.json", docker_manifest),
        *(
            (f"blobs/sha256/{digest.split(':', 1)[1]}", contents)
            for digest, contents in blobs.items()
        ),
    ]
    return image_id, _tar_bytes(entries), config_digest


def _oci_direct_manifest_archive(
    label: str,
    *,
    root_platform: bool = False,
    external_layer: bool = False,
    wrong_diff_id: bool = False,
    extra_blob: bool = False,
    wrong_layer_source: bool = False,
    wrong_repository: bool = False,
) -> tuple[str, bytes, str]:
    layer_blob = _tar_bytes([(f"{label}.txt", label.encode())])
    layer_digest = "sha256:" + hashlib.sha256(layer_blob).hexdigest()
    diff_id = "sha256:" + "0" * 64 if wrong_diff_id else layer_digest
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
        },
        separators=(",", ":"),
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    config_descriptor = {
        "mediaType": evidence_signature.OCI_CONFIG_MEDIA_TYPE,
        "digest": config_digest,
        "size": len(config),
    }
    layer_descriptor = {
        "mediaType": evidence_signature.OCI_TAR_LAYER_MEDIA_TYPE,
        "digest": layer_digest,
        "size": len(layer_blob),
    }
    if external_layer:
        layer_descriptor["urls"] = ["https://example.invalid/layer"]
    image_manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
            "config": config_descriptor,
            "layers": [layer_descriptor],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = "sha256:" + hashlib.sha256(image_manifest).hexdigest()
    root_descriptor = {
        "mediaType": evidence_signature.OCI_MANIFEST_MEDIA_TYPE,
        "digest": manifest_digest,
        "size": len(image_manifest),
        "annotations": {
            "io.containerd.image.name": f"infrapulse/{label}:test",
            "org.opencontainers.image.ref.name": f"infrapulse/{label}:test",
        },
    }
    if root_platform:
        root_descriptor["platform"] = {"os": "linux", "architecture": "amd64"}
    root_index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": evidence_signature.OCI_INDEX_MEDIA_TYPE,
            "manifests": [root_descriptor],
        },
        separators=(",", ":"),
    ).encode()
    legacy_layer_source = {
        "mediaType": evidence_signature.OCI_TAR_LAYER_MEDIA_TYPE,
        "digest": layer_digest,
        "size": len(layer_blob) + (1 if wrong_layer_source else 0),
    }
    docker_manifest = json.dumps(
        [
            {
                "Config": f"blobs/sha256/{config_digest.split(':', 1)[1]}",
                "RepoTags": [f"infrapulse/{label}:test"],
                "Layers": [f"blobs/sha256/{layer_digest.split(':', 1)[1]}"],
                "LayerSources": {layer_digest: legacy_layer_source},
            }
        ],
        separators=(",", ":"),
    ).encode()
    repositories = json.dumps(
        {
            f"infrapulse/{label}": {
                "test": "0" * 64 if wrong_repository else layer_digest.split(":", 1)[1]
            }
        },
        separators=(",", ":"),
    ).encode()
    blobs = {
        manifest_digest: image_manifest,
        config_digest: config,
        layer_digest: layer_blob,
    }
    if extra_blob:
        extra = b"unreferenced"
        blobs["sha256:" + hashlib.sha256(extra).hexdigest()] = extra
    entries = [
        ("blobs/", b""),
        ("blobs/sha256/", b""),
        ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ("index.json", root_index),
        ("manifest.json", docker_manifest),
        ("repositories", repositories),
        *(
            (f"blobs/sha256/{digest.split(':', 1)[1]}", contents)
            for digest, contents in blobs.items()
        ),
    ]
    return config_digest, _tar_bytes(entries), manifest_digest


DOCKER_FIXTURES = {
    "backend-image": _docker_archive("backend"),
    "nginx-image": _docker_archive("nginx"),
}
IMAGE_IDS = {name: fixture[0] for name, fixture in DOCKER_FIXTURES.items()}


class EvidenceSignatureTests(unittest.TestCase):
    def test_sign_and_verify_pass_fixed_image_names_and_independent_ids(self) -> None:
        with (
            self.signing_mocks(),
            patch.object(signature, "_validate_provenance") as validate,
        ):
            evidence_signature.sign_evidence(*self.arguments())
            calls = validate.call_args_list
        self.assertEqual(
            [(call.args[4], call.args[5]) for call in calls],
            [
                (None, None),
                (None, None),
                (IMAGE_IDS["backend-image"], "backend-image"),
                (IMAGE_IDS["nginx-image"], "nginx-image"),
            ],
        )
        with (
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            ),
            patch.object(signature, "verify_blob_bytes", side_effect=self.fake_verify),
            patch.object(
                signature, "verify_signature", side_effect=self.fake_statement_verify
            ) as verify,
        ):
            evidence_signature.verify_evidence(
                self.evidence,
                self.handoff,
                self.bundles,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
            self.assertEqual(
                [(call.args[9], call.args[10]) for call in verify.call_args_list],
                [
                    (None, None),
                    (None, None),
                    (IMAGE_IDS["backend-image"], "backend-image"),
                    (IMAGE_IDS["nginx-image"], "nginx-image"),
                ],
            )

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.handoff = self.root / "handoff"
        self.handoff.mkdir()
        self.bundles = self.root / "bundles"
        self.private_key = self.root / "test-only.key"
        self.private_key.write_text("test-only; never an operational key")
        self.cosign = self.root / "cosign"
        self.trivy = self.root / "trivy"
        self.cosign.write_text("mocked executable")
        self.trivy.write_text("mocked executable")
        self.policy = self.root / "trust-policy.json"
        self.policy, self.policy_hash, self.key_id = self.create_policy()
        self.local_identity = vulnerability_ci.identity()
        self.write_package()

    def handoff_identity(self) -> dict:
        identity = dict(self.local_identity)
        identity.update(
            commit=COMMIT,
            run_id=RUN_ID,
            run_attempt=str(ATTEMPT),
        )
        return identity

    def create_policy(self, revoked: bool = False) -> tuple[Path, str, str]:
        private = self.root / "ephemeral-test.pem"
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "EC",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-out",
                str(private),
            ],
            check=True,
            capture_output=True,
        )
        public = subprocess.run(
            ["openssl", "pkey", "-in", str(private), "-pubout"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        key_hash = hashlib.sha256(public.encode()).hexdigest()
        key_id = f"sha256:{key_hash}"
        policy = {
            "schema_version": 1,
            "keys": {
                key_id: {
                    "public_key": public,
                    "sha256": key_hash,
                    "status": "REVOKED" if revoked else "ACTIVE",
                }
            },
        }
        raw = json.dumps(policy, separators=(",", ":")).encode()
        self.policy.write_bytes(raw)
        return self.policy, hashlib.sha256(raw).hexdigest(), key_id

    def write_package(self) -> None:
        components = []
        file_records = []
        component_manifests = {}
        for name, (kind, coverage_status) in evidence_signature.COMPONENTS.items():
            is_image = kind == "image"
            if is_image:
                root = {
                    "type": "container",
                    "name": IMAGE_IDS[name],
                    "purl": f"pkg:oci/{name}@{IMAGE_IDS[name]}",
                }
                source = "docker-build image archive"
                archive_bytes = DOCKER_FIXTURES[name][1]
                material = {
                    "path": f"docker-build/{name}.tar",
                    "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                    "bytes": len(archive_bytes),
                }
                coverage = None
            else:
                root = {"type": "application", "name": f"{name}-root"}
                source = (
                    "backend/requirements.lock"
                    if name == "python"
                    else "frontend/package-lock.json"
                )
                material = {
                    "path": source,
                    "sha256": hashlib.sha256(name.encode()).hexdigest(),
                    "bytes": len(name),
                }
                expected_count = 49 if name == "python" else 392
                coverage = {
                    "status": "COVERAGE_PASS",
                    "expected": expected_count,
                    "found": expected_count,
                    "missing": [],
                    "unexpected": [],
                    "unclassified": [],
                    "version_mismatches": [],
                }

            sbom_name = f"{name}.cdx.json"
            manifest_name = f"{name}.manifest.json"
            statement_name = f"{name}.provenance.json"
            document = {
                "bomFormat": "CycloneDX",
                "specVersion": "1.7",
                "metadata": {"component": root},
                "components": [{"type": "library", "name": f"{name}-pkg"}],
            }
            sbom_bytes = json.dumps(document, separators=(",", ":")).encode()
            manifest_doc = {
                "generation_status": "SUCCESS",
                "commit": COMMIT,
                "status": coverage_status,
                "sha256": hashlib.sha256(sbom_bytes).hexdigest(),
                "bytes": len(sbom_bytes),
                "coverage": coverage,
            }
            if is_image:
                manifest_doc["image_id"] = IMAGE_IDS[name]
                manifest_doc["components"] = 1
            else:
                manifest_doc["source_sha256"] = material["sha256"]
            manifest_bytes = json.dumps(manifest_doc, separators=(",", ":")).encode()
            parameters = {
                "commit": COMMIT,
                "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "coverageStatus": coverage_status,
                "context": "local-untrusted",
            }
            if is_image:
                parameters["imageId"] = IMAGE_IDS[name]
            statement = {
                "_type": provenance.TYPE,
                "subject": [
                    {
                        "name": sbom_name,
                        "digest": {"sha256": hashlib.sha256(sbom_bytes).hexdigest()},
                    }
                ],
                "predicateType": provenance.PREDICATE,
                "predicate": {
                    "buildDefinition": {"externalParameters": parameters},
                    "runDetails": {"builder": {}},
                },
            }
            statement_bytes = json.dumps(statement, separators=(",", ":")).encode()
            for filename, contents in (
                (sbom_name, sbom_bytes),
                (manifest_name, manifest_bytes),
                (statement_name, statement_bytes),
            ):
                (self.evidence / filename).write_bytes(contents)
                file_records.append(
                    {
                        "path": filename,
                        "sha256": hashlib.sha256(contents).hexdigest(),
                        "bytes": len(contents),
                    }
                )
            component_manifests[name] = manifest_doc
            components.append(
                {
                    "name": name,
                    "kind": kind,
                    "inventory_source": source,
                    "coverage_status": coverage_status,
                    "coverage": coverage,
                    "image_identity": (
                        {"kind": "docker-local-image-id", "value": IMAGE_IDS[name]}
                        if is_image
                        else None
                    ),
                    "materials": [material],
                    "artifacts": [
                        record
                        for record in file_records
                        if record["path"] in {sbom_name, manifest_name, statement_name}
                    ],
                }
            )

        payload = {
            "schema_version": 1,
            "repository": REPOSITORY,
            "workflow": "CI",
            "workflow_run_id": RUN_ID,
            "run_attempt": ATTEMPT,
            "commit": COMMIT,
            "generated_at": "2026-10-31T00:00:00Z",
            "signature_status": "NOT_GENERATED",
            "authenticity": "NOT_VERIFIED",
            "builder_identity": "NOT_ASSERTED",
            "slsa_level": "NOT_CLAIMED",
            "tools": {
                "trivy": {
                    "version": sbom.PINS["trivy"],
                    "sha256": sbom.PINS["trivy_linux_x64_binary_sha256"],
                },
                "sbom_tools_lock_sha256": sbom.digest(
                    sbom.ROOT / "security/sbom-tools.lock"
                ),
            },
            "overall_coverage": "COVERAGE_PARTIAL",
            "components": components,
            "files": file_records,
            "duration_seconds": 1.2,
            "bytes": sum(record["bytes"] for record in file_records),
            "limitations": evidence_signature.F3C2_LIMITATIONS,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        (self.evidence / "evidence-manifest.json").write_bytes(raw)
        (self.evidence / "evidence-manifest.sha256").write_text(
            f"{hashlib.sha256(raw).hexdigest()}  evidence-manifest.json\n"
        )
        images = {}
        for name, (image_id, archive_bytes) in DOCKER_FIXTURES.items():
            filename = f"{name}.tar"
            (self.handoff / filename).write_bytes(archive_bytes)
            images[name] = {
                "file": filename,
                "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                "bytes": len(archive_bytes),
                "image_id": image_id,
            }
        handoff_manifest = self.handoff_identity() | {
            "images": images,
            "export_seconds": 0.01,
            "archive_bytes": sum(item["bytes"] for item in images.values()),
        }
        (self.handoff / "manifest.json").write_text(
            json.dumps(handoff_manifest, separators=(",", ":"))
        )

    def arguments(self) -> tuple:
        return (
            self.evidence,
            self.handoff,
            self.bundles,
            self.private_key,
            self.cosign,
            self.trivy,
            self.policy,
            self.policy_hash,
            self.key_id,
            COMMIT,
            RUN_ID,
            ATTEMPT,
            REPOSITORY,
            IMAGE_IDS,
        )

    def fake_sign(self, payload, bundle_path, *_args, **_kwargs) -> None:
        bundle_path.write_bytes(
            json.dumps(
                {
                    "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    "key_id": self.key_id,
                }
            ).encode()
        )

    def fake_verify(self, payload, bundle_bytes, _policy, policy_hash, key_id, _cosign):
        public = signature._trusted_key(self.policy, policy_hash, key_id)
        bundle = json.loads(bundle_bytes)
        if (
            bundle.get("payload_sha256") != hashlib.sha256(payload).hexdigest()
            or bundle.get("key_id") != key_id
            or not public
        ):
            raise ValueError("Signature payload or trusted key mismatch")
        return {"signature": "SIGNATURE_VALID", "signer": "TRUSTED_SIGNER"}

    def signing_mocks(self):
        stack = ExitStack()
        stack.enter_context(
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            )
        )
        stack.enter_context(patch.object(signature, "_validate_provenance"))
        stack.enter_context(
            patch.object(signature, "sign_blob_bytes", side_effect=self.fake_sign)
        )
        stack.enter_context(
            patch.object(signature, "verify_blob_bytes", side_effect=self.fake_verify)
        )
        return stack

    def test_round_trip_signs_manifest_and_four_exact_statements(self) -> None:
        with self.signing_mocks():
            signed = evidence_signature.sign_evidence(*self.arguments())
        self.assertEqual(signed["signature"], "SIGNATURE_VALID")
        self.assertEqual(signed["signer"], "TRUSTED_SIGNER")
        self.assertEqual(
            set(
                evidence_signature._read_exact_directory(
                    self.bundles, evidence_signature._bundle_names()
                )
            ),
            evidence_signature._bundle_names(),
        )
        verify_patches = (
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            ),
            patch.object(signature, "verify_blob_bytes", side_effect=self.fake_verify),
            patch.object(
                signature, "verify_signature", side_effect=self.fake_statement_verify
            ),
        )
        with verify_patches[0], verify_patches[1], verify_patches[2]:
            verified = evidence_signature.verify_evidence(
                self.evidence,
                self.handoff,
                self.bundles,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
        self.assertEqual(verified["signature"], "SIGNATURE_VALID")
        self.assertEqual(verified["integrity"], "INTEGRITY_VERIFIED")

    def test_verified_sbom_snapshot_survives_source_replacement(self) -> None:
        with self.signing_mocks():
            evidence_signature.sign_evidence(*self.arguments())
        path = self.evidence / "backend-image.cdx.json"
        original = path.read_bytes()

        def verify_then_mutate(payload, bundle, policy, policy_hash, key_id, cosign):
            result = self.fake_verify(
                payload, bundle, policy, policy_hash, key_id, cosign
            )
            path.write_bytes(b"changed after snapshot")
            return result

        with (
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            ),
            patch.object(
                signature, "verify_blob_bytes", side_effect=verify_then_mutate
            ),
            patch.object(
                signature, "verify_signature", side_effect=self.fake_statement_verify
            ),
        ):
            verification, snapshots = evidence_signature.verify_evidence_with_sboms(
                self.evidence,
                self.handoff,
                self.bundles,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
        self.assertEqual(verification["signature"], "SIGNATURE_VALID")
        self.assertEqual(snapshots["backend-image"], original)
        self.assertNotEqual(path.read_bytes(), original)

    def _canonicalizer_mock(self, wrong_source_digest: bool = False):
        def canonicalize(source: Path, destination: Path, image_id: str):
            original = source.read_bytes()
            canonical = b"canonical-oci-fixture:" + hashlib.sha256(original).digest()
            destination.write_bytes(canonical)
            destination.chmod(0o600)
            source_digest = hashlib.sha256(original).hexdigest()
            if wrong_source_digest:
                source_digest = "0" * 64
            return oci_canonicalizer.CanonicalArchiveResult(
                source=source.absolute(),
                canonical=destination.absolute(),
                image_id=image_id,
                profile="oci-direct-manifest-linux-amd64",
                source_sha256=source_digest,
                source_bytes=len(original),
                canonical_sha256=hashlib.sha256(canonical).hexdigest(),
                canonical_bytes=len(canonical),
                removed_blobs=("blobs/sha256/" + "f" * 64,),
            )

        return canonicalize

    def test_canonicalization_receipt_is_signed_and_revalidated(self) -> None:
        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(),
            ),
        ):
            signed = evidence_signature.sign_evidence(
                *self.arguments(), canonicalize_oci=True
            )
        self.assertEqual(signed["canonicalization"], "SIGNED_AND_REVALIDATED")
        output = evidence_signature._read_exact_directory(
            self.bundles, evidence_signature._bundle_names(canonicalize_oci=True)
        )
        receipt_bytes = output[evidence_signature.OCI_TRANSFORM_RECEIPT]
        receipt = json.loads(receipt_bytes)
        self.assertEqual(receipt["commit"], COMMIT)
        self.assertEqual(receipt["workflow_run_id"], RUN_ID)
        self.assertEqual(receipt["run_attempt"], ATTEMPT)
        self.assertEqual(
            receipt["evidence_manifest_sha256"],
            hashlib.sha256(
                (self.evidence / "evidence-manifest.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(len(receipt["images"]), 2)
        original_handoff = json.loads((self.handoff / "manifest.json").read_text())
        self.assertEqual(
            receipt["handoff_manifest_sha256"],
            hashlib.sha256((self.handoff / "manifest.json").read_bytes()).hexdigest(),
        )
        for image in receipt["images"]:
            handoff_record = original_handoff["images"][image["image"]]
            self.assertEqual(image["original"]["sha256"], handoff_record["sha256"])
            self.assertEqual(image["original"]["bytes"], handoff_record["bytes"])
            self.assertEqual(image["image_id"], IMAGE_IDS[image["image"]])
            self.assertEqual(image["profile"], "oci-direct-manifest-linux-amd64")
            self.assertEqual(
                image["transformation"], evidence_signature.OCI_TRANSFORM_ID
            )
            self.assertEqual(
                image["canonical"]["sha256"],
                hashlib.sha256(
                    b"canonical-oci-fixture:"
                    + hashlib.sha256(
                        (self.handoff / handoff_record["file"]).read_bytes()
                    ).digest()
                ).hexdigest(),
            )

        with (
            self.signing_mocks(),
            self.assertRaisesRegex(ValueError, "missing or unexpected files"),
        ):
            evidence_signature.verify_evidence(
                *self.arguments()[:3],
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )

        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(),
            ),
            patch.object(
                signature,
                "verify_signature",
                side_effect=self.fake_statement_verify,
            ),
        ):
            verified = evidence_signature.verify_evidence(
                *self.arguments()[:3],
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
                canonicalize_oci=True,
            )
        self.assertEqual(
            verified["canonicalization"], "REVALIDATED_AND_SIGNATURE_VERIFIED"
        )

    def test_canonicalization_rejects_altered_receipt_and_wrong_source_binding(
        self,
    ) -> None:
        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(),
            ),
        ):
            evidence_signature.sign_evidence(*self.arguments(), canonicalize_oci=True)
        receipt_path = self.bundles / evidence_signature.OCI_TRANSFORM_RECEIPT
        receipt_original = receipt_path.read_bytes()
        receipt_path.write_bytes(receipt_original + b" ")
        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(),
            ),
            self.assertRaisesRegex(ValueError, "transformation receipt mismatch"),
        ):
            evidence_signature.verify_evidence(
                *self.arguments()[:3],
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
                canonicalize_oci=True,
            )
        receipt_path.write_bytes(receipt_original)

        receipt_signature = self.bundles / evidence_signature.OCI_TRANSFORM_SIGNATURE
        signature_original = receipt_signature.read_bytes()
        receipt_signature.write_bytes(b"{}")
        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(),
            ),
            self.assertRaisesRegex(ValueError, "payload or trusted key mismatch"),
        ):
            evidence_signature.verify_evidence(
                *self.arguments()[:3],
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
                canonicalize_oci=True,
            )
        receipt_signature.write_bytes(signature_original)

        failed_arguments = list(self.arguments())
        failed_arguments[2] = self.root / "failed-signatures"
        with (
            self.signing_mocks(),
            patch.object(
                oci_canonicalizer,
                "canonicalize_oci_archive",
                side_effect=self._canonicalizer_mock(wrong_source_digest=True),
            ),
            self.assertRaisesRegex(ValueError, "does not match original material"),
        ):
            evidence_signature.sign_evidence(*failed_arguments, canonicalize_oci=True)
        self.assertFalse(failed_arguments[2].exists())

    def test_canonicalization_rejects_changed_or_missing_original_archive(self) -> None:
        archive = self.handoff / "backend-image.tar"
        original = archive.read_bytes()
        archive.write_bytes(original + b"tampered")
        with (
            self.signing_mocks(),
            self.assertRaisesRegex(ValueError, "bytes differ from declared materials"),
        ):
            evidence_signature.sign_evidence(*self.arguments(), canonicalize_oci=True)
        archive.write_bytes(original)

        (self.handoff / "nginx-image.tar").unlink()
        with (
            self.signing_mocks(),
            self.assertRaisesRegex(ValueError, "missing or unexpected files"),
        ):
            evidence_signature.sign_evidence(*self.arguments(), canonicalize_oci=True)

    def fake_statement_verify(
        self, statement_path, bundle_path, policy_path, policy_hash, key_id, *_args
    ):
        return self.fake_verify(
            statement_path.read_bytes(),
            bundle_path.read_bytes(),
            policy_path,
            policy_hash,
            key_id,
            self.cosign,
        )

    def test_changed_bytes_and_swapped_manifest_or_statement_are_rejected(self) -> None:
        with self.signing_mocks():
            evidence_signature.sign_evidence(*self.arguments())
        target = self.evidence / "python.provenance.json"
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "digest or size mismatch"):
            evidence_signature.verify_evidence(
                self.evidence,
                self.handoff,
                self.bundles,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
        target.write_bytes(original)
        original_manifest = (self.evidence / "evidence-manifest.json").read_bytes()
        (self.evidence / "evidence-manifest.json").write_bytes(
            (self.evidence / "python.manifest.json").read_bytes()
        )
        with self.assertRaisesRegex(ValueError, "Unsupported evidence manifest schema"):
            evidence_signature._load_and_validate(
                self.evidence, COMMIT, RUN_ID, ATTEMPT, REPOSITORY, IMAGE_IDS
            )
        (self.evidence / "evidence-manifest.json").write_bytes(original_manifest)

        python_bundle = self.bundles / "python.provenance.sigstore.json"
        npm_bundle = self.bundles / "npm.provenance.sigstore.json"
        python_bytes = python_bundle.read_bytes()
        python_bundle.write_bytes(npm_bundle.read_bytes())
        with (
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            ),
            patch.object(signature, "verify_blob_bytes", side_effect=self.fake_verify),
            patch.object(
                signature, "verify_signature", side_effect=self.fake_statement_verify
            ),
            self.assertRaisesRegex(ValueError, "payload or trusted key mismatch"),
        ):
            evidence_signature.verify_evidence(
                self.evidence,
                self.handoff,
                self.bundles,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
        python_bundle.write_bytes(python_bytes)

    def test_run_attempt_and_trust_policy_mismatch_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "run identity mismatch"):
            evidence_signature._load_and_validate(
                self.evidence, COMMIT, RUN_ID, ATTEMPT + 1, REPOSITORY, IMAGE_IDS
            )
        with self.assertRaisesRegex(ValueError, "not authorized"):
            signature._trusted_key(self.policy, self.policy_hash, "sha256:" + "0" * 64)
        revoked_path, revoked_hash, revoked_id = self.create_policy(revoked=True)
        with self.assertRaisesRegex(ValueError, "revoked or inactive"):
            signature._trusted_key(revoked_path, revoked_hash, revoked_id)
        with self.assertRaisesRegex(ValueError, "policy SHA-256"):
            signature._trusted_key(self.policy, "0" * 64, self.key_id)

    def _validate_handoff(self) -> None:
        data, manifest = evidence_signature._load_and_validate(
            self.evidence, COMMIT, RUN_ID, ATTEMPT, REPOSITORY, IMAGE_IDS
        )
        with patch.object(
            vulnerability_ci, "identity", side_effect=self.handoff_identity
        ):
            evidence_signature._validate_handoff_archives(
                self.handoff,
                manifest,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                IMAGE_IDS,
                evidence_manifest_bytes=data["evidence-manifest.json"],
            )

    def test_handoff_archives_require_actual_hash_size_and_image_id(self) -> None:
        archive = self.handoff / "backend-image.tar"
        original = archive.read_bytes()
        archive.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            self._validate_handoff()
        archive.write_bytes(original)

        archive.write_bytes(original + b"x")
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            self._validate_handoff()
        archive.write_bytes(original)

        handoff_manifest = self.handoff / "manifest.json"
        original_manifest = json.loads(handoff_manifest.read_bytes())
        altered = json.loads(json.dumps(original_manifest))
        altered["images"]["backend-image"]["bytes"] += 1
        handoff_manifest.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            self._validate_handoff()
        altered["images"]["backend-image"]["bytes"] -= 1
        altered["images"]["backend-image"]["image_id"] = "sha256:" + "0" * 64
        handoff_manifest.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            self._validate_handoff()
        handoff_manifest.write_text(json.dumps(original_manifest))

        wrong_id = "sha256:" + "0" * 64
        wrong_ids = dict(IMAGE_IDS, **{"backend-image": wrong_id})
        altered = json.loads(handoff_manifest.read_text())
        altered["images"]["backend-image"]["image_id"] = wrong_id
        handoff_manifest.write_text(json.dumps(altered))
        data, manifest = evidence_signature._load_and_validate(
            self.evidence, COMMIT, RUN_ID, ATTEMPT, REPOSITORY, IMAGE_IDS
        )
        with (
            patch.object(
                vulnerability_ci, "identity", side_effect=self.handoff_identity
            ),
            self.assertRaisesRegex(ValueError, "config differs from expected image ID"),
        ):
            evidence_signature._validate_handoff_archives(
                self.handoff,
                manifest,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                wrong_ids,
                evidence_manifest_bytes=data["evidence-manifest.json"],
            )

    def _verify_archive_bytes(
        self, contents: bytes, expected_image_id: str | None = None
    ) -> None:
        path = self.root / "docker-save.tar"
        path.write_bytes(contents)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            evidence_signature._verify_docker_archive(
                fd, expected_image_id or IMAGE_IDS["backend-image"]
            )
        finally:
            os.close(fd)

    def test_oci_docker_archive_validates_index_config_and_diffids(self) -> None:
        image_id, archive, config_digest = _oci_docker_archive("oci-backend")
        self.assertNotEqual(image_id, config_digest)
        self._verify_archive_bytes(archive, image_id)

    def test_oci_direct_manifest_archive_binds_image_id_to_config(self) -> None:
        image_id, archive, manifest_digest = _oci_direct_manifest_archive(
            "oci-direct-backend"
        )
        self.assertNotEqual(image_id, manifest_digest)
        self._verify_archive_bytes(archive, image_id)

    def test_oci_direct_manifest_rejects_identity_and_layout_ambiguity(self) -> None:
        image_id, valid, _ = _oci_direct_manifest_archive("oci-direct-backend")
        with self.assertRaisesRegex(ValueError, "config digest differs"):
            self._verify_archive_bytes(valid, "sha256:" + "0" * 64)

        _image_id, platform, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", root_platform=True
        )
        with self.assertRaisesRegex(ValueError, "must omit platform"):
            self._verify_archive_bytes(platform, image_id)

        _image_id, external, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", external_layer=True
        )
        with self.assertRaisesRegex(ValueError, "OCI descriptor"):
            self._verify_archive_bytes(external, image_id)

        wrong_diff_id, wrong_diff, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", wrong_diff_id=True
        )
        with self.assertRaisesRegex(ValueError, "DiffID mismatch"):
            self._verify_archive_bytes(wrong_diff, wrong_diff_id)

        _image_id, extra_blob, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", extra_blob=True
        )
        with self.assertRaisesRegex(ValueError, "unreferenced or missing"):
            self._verify_archive_bytes(extra_blob, image_id)

        _image_id, wrong_source, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", wrong_layer_source=True
        )
        with self.assertRaisesRegex(ValueError, "compatibility manifest"):
            self._verify_archive_bytes(wrong_source, image_id)

        _image_id, wrong_repositories, _ = _oci_direct_manifest_archive(
            "oci-direct-backend", wrong_repository=True
        )
        with self.assertRaisesRegex(ValueError, "repositories metadata"):
            self._verify_archive_bytes(wrong_repositories, image_id)

    def test_oci_archive_rejects_identity_ambiguity_and_unverified_layers(
        self,
    ) -> None:
        _image_id, valid, _ = _oci_docker_archive("oci-backend")
        with self.assertRaisesRegex(ValueError, "root index digest"):
            self._verify_archive_bytes(valid, "sha256:" + "0" * 64)

        duplicate_id, duplicate, _ = _oci_docker_archive(
            "oci-backend", duplicate_platform=True
        )
        with self.assertRaisesRegex(ValueError, "nested OCI index"):
            self._verify_archive_bytes(duplicate, duplicate_id)

        external_id, external, _ = _oci_docker_archive(
            "oci-backend", external_layer=True
        )
        with self.assertRaisesRegex(ValueError, "OCI descriptor"):
            self._verify_archive_bytes(external, external_id)

        wrong_diff_id, wrong_diff, _ = _oci_docker_archive(
            "oci-backend", wrong_diff_id=True
        )
        with self.assertRaisesRegex(ValueError, "DiffID mismatch"):
            self._verify_archive_bytes(wrong_diff, wrong_diff_id)

    def test_tar_prevalidation_rejects_extensions_truncation_and_bad_offsets(
        self,
    ) -> None:
        valid = DOCKER_FIXTURES["backend-image"][1]
        extension = bytearray(valid)
        extension[156] = ord("x")
        extension[148:156] = b" " * 8
        checksum = sum(extension[:512])
        extension[148:156] = f"{checksum:06o}\0 ".encode("ascii")
        with (
            patch.object(tarfile, "open", side_effect=AssertionError("parsed")),
            self.assertRaisesRegex(ValueError, "extension or special"),
        ):
            self._verify_archive_bytes(bytes(extension))

        with self.assertRaisesRegex(ValueError, "size or TAR block alignment"):
            self._verify_archive_bytes(valid[:100])
        end_offset = 0
        while any(valid[end_offset : end_offset + 512]):
            header = valid[end_offset : end_offset + 512]
            member_size = int(header[124:136].strip(b" \0") or b"0", 8)
            end_offset += 512 + ((member_size + 511) // 512) * 512
        with self.assertRaisesRegex(ValueError, "end marker is missing or truncated"):
            self._verify_archive_bytes(valid[:end_offset])

        bad_checksum = bytearray(valid)
        bad_checksum[148:156] = b"0000000\0"
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self._verify_archive_bytes(bytes(bad_checksum))

        bad_offset = bytearray(valid)
        bad_offset[124:136] = f"{100_000:011o}\0".encode("ascii")
        bad_offset[148:156] = b" " * 8
        checksum = sum(bad_offset[:512])
        bad_offset[148:156] = f"{checksum:06o}\0 ".encode("ascii")
        with self.assertRaisesRegex(ValueError, "Truncated Docker TAR member data"):
            self._verify_archive_bytes(bytes(bad_offset))

        duplicate_id, duplicate = _docker_archive("backend", duplicate_alias=True)
        self.assertEqual(duplicate_id, IMAGE_IDS["backend-image"])
        with self.assertRaisesRegex(ValueError, "Duplicate Docker TAR path"):
            self._verify_archive_bytes(duplicate)

    def test_docker_or_trivy_unavailable_fails_before_publication(self) -> None:
        for unavailable in (
            "Docker daemon unavailable",
            "Trivy executable unavailable",
        ):
            with (
                self.subTest(unavailable=unavailable),
                self.signing_mocks(),
                patch.object(
                    signature,
                    "_validate_provenance",
                    side_effect=FileNotFoundError(unavailable),
                ),
                self.assertRaisesRegex(FileNotFoundError, unavailable),
            ):
                evidence_signature.sign_evidence(*self.arguments())
            self.assertFalse(self.bundles.exists())

    def test_atomic_publication_rejects_parent_substitution_and_cleans_up(self) -> None:
        parent = self.root / "publish-parent"
        parent.mkdir(mode=0o700)
        parent_fd = evidence_signature._directory_fd(parent)
        moved_parent = self.root / "publish-parent-old"
        original_rename = evidence_signature._rename_noreplace
        substituted = False

        def substitute(parent_fd_arg, source, destination):
            nonlocal substituted
            if substituted:
                original_rename(parent_fd_arg, source, destination)
                return
            substituted = True
            parent.rename(moved_parent)
            parent.symlink_to(moved_parent, target_is_directory=True)
            original_rename(parent_fd_arg, source, destination)

        with (
            patch.object(
                evidence_signature, "_rename_noreplace", side_effect=substitute
            ),
            self.assertRaisesRegex(ValueError, "parent was substituted"),
        ):
            evidence_signature._publish_bundle_directory(
                parent_fd,
                "bundles",
                {name: b"bundle" for name in evidence_signature._bundle_names()},
                parent,
            )
        os.close(parent_fd)
        self.assertFalse((moved_parent / "bundles").exists())
        self.assertEqual(list(moved_parent.iterdir()), [])

    def test_atomic_publication_restores_concurrently_replaced_source(self) -> None:
        parent = self.root / "source-race-parent"
        parent.mkdir(mode=0o700)
        parent_fd = evidence_signature._directory_fd(parent, require_trusted=True)
        original_rename = evidence_signature._rename_noreplace
        substituted = False
        parked_name = "parked-original-temp"
        source_marker = "operator-owned"

        def substitute(parent_fd_arg, source, destination):
            nonlocal substituted
            if substituted:
                original_rename(parent_fd_arg, source, destination)
                return
            substituted = True
            os.rename(
                source, parked_name, src_dir_fd=parent_fd_arg, dst_dir_fd=parent_fd_arg
            )
            os.mkdir(source, mode=0o700, dir_fd=parent_fd_arg)
            replacement_fd = os.open(
                source, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd_arg
            )
            try:
                marker_fd = os.open(
                    source_marker,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=replacement_fd,
                )
                try:
                    os.write(marker_fd, b"preserve third-party file")
                finally:
                    os.close(marker_fd)
            finally:
                os.close(replacement_fd)
            original_rename(parent_fd_arg, source, destination)

        with (
            patch.object(
                evidence_signature, "_rename_noreplace", side_effect=substitute
            ),
            self.assertRaisesRegex(
                ValueError, "Published signature directory was substituted"
            ),
        ):
            evidence_signature._publish_bundle_directory(
                parent_fd,
                "bundles",
                {name: b"bundle" for name in evidence_signature._bundle_names()},
                parent,
            )
        os.close(parent_fd)
        self.assertFalse((parent / "bundles").exists())
        self.assertEqual(list((parent / parked_name).iterdir()), [])
        replacement_dirs = [
            path for path in parent.iterdir() if path.name != parked_name
        ]
        self.assertEqual(len(replacement_dirs), 1)
        self.assertEqual(
            (replacement_dirs[0] / source_marker).read_bytes(),
            b"preserve third-party file",
        )
        self.assertFalse(
            any(
                path.name in evidence_signature._bundle_names()
                for path in replacement_dirs[0].iterdir()
            )
        )

    def test_signature_output_rejects_group_or_world_writable_parent(self) -> None:
        parent = self.root / "writable-parent"
        parent.mkdir(mode=0o700)
        parent.chmod(0o777)
        try:
            with self.assertRaisesRegex(ValueError, "untrusted directory"):
                evidence_signature._directory_fd(parent, require_trusted=True)
        finally:
            parent.chmod(0o700)

    def test_atomic_publication_write_failure_leaves_no_partial_output(self) -> None:
        parent = self.root / "write-parent"
        parent.mkdir(mode=0o700)
        parent_fd = evidence_signature._directory_fd(parent)

        def fail_write(_fd, _contents):
            raise OSError("injected write failure")

        with (
            patch.object(evidence_signature.os, "write", side_effect=fail_write),
            self.assertRaisesRegex(OSError, "injected write failure"),
        ):
            evidence_signature._publish_bundle_directory(
                parent_fd,
                "bundles",
                {name: b"bundle" for name in evidence_signature._bundle_names()},
                parent,
            )
        os.close(parent_fd)
        self.assertEqual(list(parent.iterdir()), [])

    def test_atomic_publication_never_overwrites_existing_destination(self) -> None:
        parent = self.root / "occupied-parent"
        parent.mkdir(mode=0o700)
        destination = parent / "bundles"
        destination.mkdir()
        marker = destination / "operator-owned"
        marker.write_text("preserve")
        parent_fd = evidence_signature._directory_fd(parent)
        with self.assertRaises(OSError):
            evidence_signature._publish_bundle_directory(
                parent_fd,
                "bundles",
                {name: b"bundle" for name in evidence_signature._bundle_names()},
                parent,
            )
        os.close(parent_fd)
        self.assertEqual(marker.read_text(), "preserve")
        self.assertEqual(list(parent.iterdir()), [destination])

    def test_cosign_local_signing_disables_tlog_and_remote_signing_config(self) -> None:
        bundle = self.root / "one-blob.sigstore.json"
        observed: list[str] = []

        def fake_cosign(_tool: Path, args: list[str], timeout: int = 120) -> bytes:
            observed.extend(args)
            temporary_bundle = Path(args[args.index("--bundle") + 1])
            temporary_bundle.write_text('{"test":true}')
            return b""

        with (
            patch.object(signature, "verify_cosign"),
            patch.object(signature.sbom, "check_output"),
            patch.object(signature, "_run_cosign", side_effect=fake_cosign),
        ):
            signature.sign_blob_bytes(
                b"exact signed bytes", bundle, self.private_key, self.cosign
            )
        self.assertIn("--tlog-upload=false", observed)
        self.assertIn("--use-signing-config=false", observed)

    def test_missing_unexpected_and_symlinked_evidence_files_are_rejected(self) -> None:
        missing = self.evidence / "npm.cdx.json"
        saved = missing.read_bytes()
        missing.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            evidence_signature._read_exact_directory(
                self.evidence, evidence_signature._expected_evidence_files()
            )
        missing.write_bytes(saved)
        extra = self.evidence / "unexpected.json"
        extra.write_text("{}")
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            evidence_signature._read_exact_directory(
                self.evidence, evidence_signature._expected_evidence_files()
            )
        extra.unlink()
        link = self.evidence / "python.cdx.json"
        saved = link.read_bytes()
        link.unlink()
        link.symlink_to(self.evidence / "npm.cdx.json")
        with self.assertRaises(OSError):
            evidence_signature._read_exact_directory(
                self.evidence, evidence_signature._expected_evidence_files()
            )
        link.unlink()
        link.write_bytes(saved)

    def test_output_cannot_overlap_evidence_or_follow_symlink_parent(self) -> None:
        nested_output = self.evidence / "signatures"
        with (
            self.assertRaisesRegex(
                ValueError, "separate from the checkout and evidence"
            ),
            self.signing_mocks(),
        ):
            evidence_signature.sign_evidence(
                self.evidence,
                self.handoff,
                nested_output,
                self.private_key,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )
        real_parent = self.root / "safe-parent"
        real_parent.mkdir()
        parent_link = self.root / "parent-link"
        parent_link.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(OSError), self.signing_mocks():
            evidence_signature.sign_evidence(
                self.evidence,
                self.handoff,
                parent_link / "signatures",
                self.private_key,
                self.cosign,
                self.trivy,
                self.policy,
                self.policy_hash,
                self.key_id,
                COMMIT,
                RUN_ID,
                ATTEMPT,
                REPOSITORY,
                IMAGE_IDS,
            )

    def test_invalid_material_coverage_image_identity_and_statement_rejected(
        self,
    ) -> None:
        manifest_path = self.evidence / "evidence-manifest.json"
        original_manifest = json.loads(manifest_path.read_bytes())
        for mutate in (
            lambda doc: doc["components"][0].update(
                inventory_source="frontend/package-lock.json"
            ),
            lambda doc: doc["components"][0].update(
                coverage={"status": "COVERAGE_PASS"}
            ),
            lambda doc: doc["components"][2].update(
                image_identity={
                    "kind": "registry-digest",
                    "value": IMAGE_IDS["backend-image"],
                }
            ),
        ):
            doc = json.loads(json.dumps(original_manifest))
            mutate(doc)
            raw = json.dumps(doc, separators=(",", ":")).encode()
            manifest_path.write_bytes(raw)
            (self.evidence / "evidence-manifest.sha256").write_text(
                f"{hashlib.sha256(raw).hexdigest()}  evidence-manifest.json\n"
            )
            with self.assertRaises(ValueError):
                evidence_signature._load_and_validate(
                    self.evidence, COMMIT, RUN_ID, ATTEMPT, REPOSITORY, IMAGE_IDS
                )
        manifest_path.write_bytes(
            json.dumps(original_manifest, separators=(",", ":")).encode()
        )
        # Restore the exact original manifest bytes and sidecar after mutation tests.
        self.write_package()
        statement = self.evidence / "backend-image.provenance.json"
        statement.write_text('{"_type":"wrong"}')
        record = next(
            item
            for item in original_manifest["files"]
            if item["path"] == "backend-image.provenance.json"
        )
        changed = statement.read_bytes()
        record["sha256"] = hashlib.sha256(changed).hexdigest()
        record["bytes"] = len(changed)
        original_manifest["bytes"] = sum(
            item["bytes"] for item in original_manifest["files"]
        )
        for component in original_manifest["components"]:
            for artifact in component["artifacts"]:
                if artifact["path"] == record["path"]:
                    artifact.update(record)
        raw = json.dumps(original_manifest, separators=(",", ":")).encode()
        manifest_path.write_bytes(raw)
        (self.evidence / "evidence-manifest.sha256").write_text(
            f"{hashlib.sha256(raw).hexdigest()}  evidence-manifest.json\n"
        )
        with self.assertRaisesRegex(TypeError, "Malformed provenance statement"):
            evidence_signature._load_and_validate(
                self.evidence, COMMIT, RUN_ID, ATTEMPT, REPOSITORY, IMAGE_IDS
            )


if __name__ == "__main__":
    unittest.main()
