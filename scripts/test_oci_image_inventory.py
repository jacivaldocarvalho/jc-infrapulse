"""Security and reproducibility tests for the virtual OCI inventory."""

import hashlib
import io
import tarfile
import unittest
from unittest.mock import patch

import oci_image_inventory as inventory
import oci_virtual_layers as virtual
from test_oci_virtual_layers import layer, tar_bytes


class InventoryTests(unittest.TestCase):
    def test_hashes_metadata_order_and_no_content_disclosure(self) -> None:
        inputs = [
            layer(
                tar_bytes(
                    [
                        ("z", "file", b"secret"),
                        ("dir", "directory", b""),
                        ("a", "symlink", b"z"),
                    ]
                )
            )
        ]
        result = inventory.inventory(inputs)
        self.assertEqual([item.path for item in result], ["a", "dir", "z"])
        item = result[-1]
        self.assertEqual(item.sha256, hashlib.sha256(b"secret").hexdigest())
        self.assertEqual(item.size, 6)
        self.assertEqual((item.content_layer, item.content_path), (0, "z"))
        self.assertNotIn("secret", repr(result))
        self.assertEqual(result[0].link_target, "z")
        self.assertIsNone(result[0].sha256)
        self.assertEqual(result, inventory.inventory(inputs))

    def test_hardlink_retains_old_content_after_target_replacement(self) -> None:
        inputs = [
            layer(tar_bytes([("a", "file", b"old"), ("b", "hardlink", b"a")])),
            layer(tar_bytes([("a", "file", b"new")])),
        ]
        a, b = inventory.inventory(inputs)
        self.assertEqual(a.sha256, hashlib.sha256(b"new").hexdigest())
        self.assertEqual(b.sha256, hashlib.sha256(b"old").hexdigest())
        self.assertEqual(
            (b.kind, b.size, b.content_layer, b.content_path), ("hardlink", 3, 0, "a")
        )
        self.assertEqual(b.link_target, "a")

    def test_hardlink_uses_inode_metadata_and_empty_file_hash(self) -> None:
        stream = io.BytesIO()
        with tarfile.open(
            fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive:
            original = tarfile.TarInfo("original")
            original.mode, original.uid, original.gid = 0o640, 123, 456
            archive.addfile(original, io.BytesIO(b""))
            alias = tarfile.TarInfo("alias")
            alias.type, alias.linkname = tarfile.LNKTYPE, "original"
            alias.mode, alias.uid, alias.gid = 0o777, 1, 2
            archive.addfile(alias)
        alias, original = inventory.inventory([layer(stream.getvalue())])
        self.assertEqual((alias.mode, alias.uid, alias.gid), (0o640, 123, 456))
        self.assertEqual((original.mode, original.uid, original.gid), (0o640, 123, 456))
        self.assertEqual(alias.sha256, hashlib.sha256(b"").hexdigest())

    def test_whiteouts_exclude_deleted_entries_and_implicit_directories(self) -> None:
        inputs = [
            layer(tar_bytes([("d/old", "file", b"x")])),
            layer(tar_bytes([("d/.wh.old", "file", b""), ("d/new", "file", b"y")])),
        ]
        self.assertEqual([item.path for item in inventory.inventory(inputs)], ["d/new"])

    def test_invalid_descriptors_truncation_and_limits_fail(self) -> None:
        data = tar_bytes([("a", "file", b"x")])
        valid = layer(data)
        for invalid in (
            virtual.Layer(data, "sha256:" + "0" * 64, valid.diff_id, len(data)),
            layer(data[:1024]),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                inventory.inventory([invalid])
        with patch.object(virtual, "MAX_ENTRIES", 0), self.assertRaises(ValueError):
            inventory.inventory([valid])

    def test_unavailable_hardlink_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            inventory.inventory([layer(tar_bytes([("a", "hardlink", b"missing")]))])


if __name__ == "__main__":
    unittest.main()
