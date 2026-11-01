"""Offline tests for separate OCI archive canonicalization."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_evidence_signature as signature
import oci_canonicalizer as canonicalizer
from test_ci_evidence_signature import (
    _docker_archive,
    _oci_direct_manifest_archive,
    _oci_docker_archive,
    _tar_bytes,
)


def _legacy_metadata_blob(identity: str) -> tuple[str, bytes]:
    raw = json.dumps(
        {
            "id": identity,
            "os": "linux",
            "created": "2026-01-01T00:00:00Z",
            "container_config": {},
        },
        separators=(",", ":"),
    ).encode()
    return f"blobs/sha256/{hashlib.sha256(raw).hexdigest()}", raw


def _with_members(archive_bytes: bytes, extra: list[tuple[str, bytes]]) -> bytes:
    entries: list[tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                entries.append((member.name.rstrip("/") + "/", b""))
            else:
                stream = archive.extractfile(member)
                if stream is None:
                    raise AssertionError("fixture member unavailable")
                entries.append((member.name, stream.read()))
    return _tar_bytes(entries + extra)


class OciCanonicalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _run(self, archive: bytes, image_id: str) -> tuple[Path, Path, bytes]:
        source = self.root / "source.tar"
        destination = self.root / "canonical.tar"
        source.write_bytes(archive)
        result = canonicalizer.canonicalize_oci_archive(source, destination, image_id)
        self.assertEqual(result.source_sha256, hashlib.sha256(archive).hexdigest())
        self.assertEqual(result.source_bytes, len(archive))
        self.assertEqual(
            result.canonical_sha256,
            hashlib.sha256(destination.read_bytes()).hexdigest(),
        )
        self.assertEqual(result.canonical_bytes, destination.stat().st_size)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(source.read_bytes(), archive)
        fd = os.open(destination, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            signature._verify_docker_archive(fd, image_id)
        finally:
            os.close(fd)
        return source, destination, archive

    def test_direct_manifest_profile_removes_only_legacy_json(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-direct")
        orphan_path, orphan = _legacy_metadata_blob("a" * 64)
        original = _with_members(direct, [(orphan_path, orphan)])
        source, destination, _ = self._run(original, image_id)
        result = canonicalizer.canonicalize_oci_archive(
            source, self.root / "second.tar", image_id
        )
        self.assertEqual(result.profile, "oci-direct-manifest-linux-amd64")
        self.assertEqual(result.removed_blobs, (orphan_path,))
        self.assertNotEqual(result.source_sha256, result.canonical_sha256)
        self.assertTrue(destination.is_file())

    def test_nested_index_profile_removes_only_legacy_json(self) -> None:
        image_id, nested, _ = _oci_docker_archive("canonical-nested")
        orphan_path, orphan = _legacy_metadata_blob("b" * 64)
        original = _with_members(nested, [(orphan_path, orphan)])
        _source, destination, _ = self._run(original, image_id)
        result = canonicalizer.canonicalize_oci_archive(
            self.root / "source.tar", self.root / "second.tar", image_id
        )
        self.assertEqual(result.profile, "oci-nested-index-linux-amd64")
        self.assertEqual(result.removed_blobs, (orphan_path,))
        self.assertTrue(destination.is_file())

    def test_rejects_arbitrary_unreferenced_blob_without_publishing(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-invalid")
        raw = b'{"untrusted":"unknown schema"}'
        path = f"blobs/sha256/{hashlib.sha256(raw).hexdigest()}"
        source = self.root / "source.tar"
        source.write_bytes(_with_members(direct, [(path, raw)]))
        destination = self.root / "canonical.tar"
        with self.assertRaisesRegex(ValueError, "legacy Docker layer metadata"):
            canonicalizer.canonicalize_oci_archive(source, destination, image_id)
        self.assertFalse(destination.exists())
        self.assertEqual({item.name for item in self.root.iterdir()}, {"source.tar"})

    def test_rejects_unreferenced_non_blob_file(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-extra")
        source = self.root / "source.tar"
        source.write_bytes(_with_members(direct, [("unknown.json", b"{}")]))
        with self.assertRaisesRegex(ValueError, "Unrecognized auxiliary"):
            canonicalizer.canonicalize_oci_archive(
                source, self.root / "canonical.tar", image_id
            )

    def test_rejects_wrong_independent_image_id_and_classic_format(self) -> None:
        _image_id, direct, _ = _oci_direct_manifest_archive("canonical-id")
        source = self.root / "source.tar"
        source.write_bytes(direct)
        with self.assertRaisesRegex(ValueError, "config digest differs"):
            canonicalizer.canonicalize_oci_archive(
                source, self.root / "wrong-id.tar", "sha256:" + "0" * 64
            )
        _classic_id, classic = _docker_archive("not-oci")
        source.write_bytes(classic)
        with self.assertRaisesRegex(ValueError, "supported OCI profile"):
            canonicalizer.canonicalize_oci_archive(
                source, self.root / "classic.tar", "sha256:" + "0" * 64
            )

    def test_existing_destination_is_never_overwritten(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-no-overwrite")
        source = self.root / "source.tar"
        destination = self.root / "canonical.tar"
        source.write_bytes(direct)
        destination.write_bytes(b"preserve existing content")
        with self.assertRaises(FileExistsError):
            canonicalizer.canonicalize_oci_archive(source, destination, image_id)
        self.assertEqual(destination.read_bytes(), b"preserve existing content")

    def test_publication_failure_cleans_only_owned_temporary_file(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-publish-fail")
        orphan_path, orphan = _legacy_metadata_blob("c" * 64)
        source = self.root / "source.tar"
        source.write_bytes(_with_members(direct, [(orphan_path, orphan)]))
        destination = self.root / "canonical.tar"
        with (
            patch.object(
                signature,
                "_rename_noreplace",
                side_effect=OSError("simulated publish failure"),
            ),
            self.assertRaisesRegex(OSError, "simulated publish failure"),
        ):
            canonicalizer.canonicalize_oci_archive(source, destination, image_id)
        self.assertFalse(destination.exists())
        self.assertEqual({item.name for item in self.root.iterdir()}, {"source.tar"})

    def test_post_publication_failure_removes_only_owned_destination(self) -> None:
        image_id, direct, _ = _oci_direct_manifest_archive("canonical-post-publish")
        source = self.root / "source.tar"
        destination = self.root / "canonical.tar"
        source.write_bytes(direct)
        with (
            patch.object(os, "fsync", side_effect=[None, OSError("simulated fsync")]),
            self.assertRaisesRegex(OSError, "simulated fsync"),
        ):
            canonicalizer.canonicalize_oci_archive(source, destination, image_id)
        self.assertFalse(destination.exists())
        self.assertEqual({item.name for item in self.root.iterdir()}, {"source.tar"})


if __name__ == "__main__":
    unittest.main()
