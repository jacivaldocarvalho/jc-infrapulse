"""Synthetic tests for the isolated virtual OCI layer reader."""

import gzip
import hashlib
import io
import tarfile
import unittest
from unittest.mock import patch

import oci_virtual_layers as virtual


def tar_bytes(entries: list[tuple[str, str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, kind, data in entries:
            item = tarfile.TarInfo(name)
            if kind == "file":
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            elif kind == "directory":
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            else:
                item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                item.linkname = data.decode("ascii")
                archive.addfile(item)
    return stream.getvalue()


def layer(data: bytes) -> virtual.Layer:
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return virtual.Layer(data, digest, digest, len(data))


def replace_numeric(data: bytes, start: int, end: int, value: bytes) -> bytes:
    payload = bytearray(data)
    if len(value) != end - start:
        raise ValueError("Field width mismatch")
    payload[start:end] = value
    payload[148:156] = b" " * 8
    payload[148:156] = f"{sum(payload[:512]):06o}\0 ".encode("ascii")
    return bytes(payload)


class VirtualLayersTests(unittest.TestCase):
    def test_leading_nul_size_cannot_disagree_with_tarfile(self) -> None:
        raw = tar_bytes([("a", "file", b"X")])
        ambiguous = replace_numeric(raw, 124, 136, b"\0" + b"00000000001")
        with tarfile.open(fileobj=io.BytesIO(ambiguous), mode="r:") as archive:
            self.assertEqual(archive.next().size, 0)
        with self.assertRaisesRegex(ValueError, "numeric"):
            virtual.read_layers([layer(ambiguous)])

    def test_invalid_numeric_fields_with_valid_header_checksum(self) -> None:
        raw = tar_bytes([("a", "file", b"X")])
        for start, end in (
            (100, 108),
            (108, 116),
            (116, 124),
            (124, 136),
            (136, 148),
            (329, 337),
            (337, 345),
        ):
            for value in (
                b"\0" + b"0" * (end - start - 1),
                b"0" * (end - start - 1) + b"8",
            ):
                with (
                    self.subTest(start=start, value=value),
                    self.assertRaisesRegex(ValueError, "numeric"),
                ):
                    virtual.read_layers(
                        [layer(replace_numeric(raw, start, end, value))]
                    )
        invalid_checksum = bytearray(raw)
        invalid_checksum[148:156] = b"\0" + raw[148:155]
        with self.assertRaisesRegex(ValueError, "numeric"):
            virtual.read_layers([layer(bytes(invalid_checksum))])

    def test_padding_trailing_garbage_and_gzip_rejected(self) -> None:
        data = tar_bytes([("a", "file", b"X")])
        bad_padding = bytearray(data)
        bad_padding[513] = 1
        garbage = bytearray(data)
        garbage[-1] = 1
        for payload in (
            bytes(bad_padding),
            bytes(garbage),
            data[:1024],
            gzip.compress(data),
        ):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                virtual.read_layers([layer(payload)])

    def test_mutated_payload_with_original_descriptors_fails(self) -> None:
        data = tar_bytes([("a", "file", b"X")])
        expected = layer(data)
        changed = bytearray(data)
        changed[512] = ord("Y")
        with self.assertRaisesRegex(ValueError, "digest or DiffID"):
            virtual.read_layers(
                [
                    virtual.Layer(
                        bytes(changed), expected.digest, expected.diff_id, expected.size
                    )
                ]
            )

    def test_aggregate_limits_are_enforced(self) -> None:
        item = layer(tar_bytes([("a", "file", b"X")]))
        with patch.object(virtual, "MAX_LAYERS", 1), self.assertRaises(ValueError):
            virtual.read_layers([item, item])
        with (
            patch.object(virtual, "MAX_TOTAL_BYTES", len(item.data) * 2 - 1),
            self.assertRaises(ValueError),
        ):
            virtual.read_layers([item, item])
        with (
            patch.object(virtual, "MAX_ENTRIES", 1),
            self.assertRaisesRegex(ValueError, "entry limit"),
        ):
            virtual.read_layers([item, item])

    def test_order_and_symlink_are_virtual(self) -> None:
        first = layer(tar_bytes([("app", "directory", b""), ("app/a", "file", b"one")]))
        second = layer(
            tar_bytes([("app/a", "file", b"two"), ("link", "symlink", b"app/a")])
        )
        result = virtual.read_layers([first, second])
        self.assertEqual(
            [[e.path for e in group] for group in result],
            [["app", "app/a"], ["app/a", "link"]],
        )
        self.assertEqual(result[1][1].link_target, "app/a")

    def test_invalid_paths_and_unsupported_operations(self) -> None:
        for name, kind, value in (
            ("/abs", "file", b""),
            ("a/../b", "file", b""),
            ("a//b", "file", b""),
            ("a/.wh.b", "file", b""),
            ("hard", "hardlink", b"file"),
            ("link", "symlink", b"../outside"),
            ("bad\\name", "file", b""),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                virtual.read_layers([layer(tar_bytes([(name, kind, value)]))])

    def test_duplicate_and_parent_collisions(self) -> None:
        for entries in (
            [("a", "file", b""), ("a", "file", b"")],
            [("a", "symlink", b"target"), ("a/b", "file", b"")],
            [("a/b", "file", b""), ("a", "file", b"")],
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                virtual.read_layers([layer(tar_bytes(entries))])

    def test_digests_sizes_and_limits(self) -> None:
        data = tar_bytes([("a", "file", b"data")])
        valid = layer(data)
        for candidate in (
            virtual.Layer(data, "sha256:" + "0" * 64, valid.diff_id, len(data)),
            virtual.Layer(data, valid.digest, "sha256:" + "0" * 64, len(data)),
            virtual.Layer(data, valid.digest, valid.diff_id, len(data) + 1),
        ):
            with (
                self.subTest(candidate=candidate.digest),
                self.assertRaises(ValueError),
            ):
                virtual.read_layers([candidate])
        with self.assertRaises(ValueError):
            virtual.read_layers([])
        with (
            patch.object(virtual, "MAX_LAYER_BYTES", len(data) - 1),
            self.assertRaises(ValueError),
        ):
            virtual.read_layers([valid])
        with patch.object(virtual, "MAX_ENTRIES", 1), self.assertRaises(ValueError):
            virtual.read_layers([valid, valid])

    def test_corrupt_headers_and_truncated_tar(self) -> None:
        data = tar_bytes([("a", "file", b"data")])
        broken = bytearray(data)
        broken[0] ^= 1
        for payload in (bytes(broken), data[:512], data[:1024] + b"x" * 512):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                virtual.read_layers([layer(payload)])


class VirtualRootfsTests(unittest.TestCase):
    def build(
        self, *groups: list[tuple[str, str, bytes]]
    ) -> dict[str, virtual.RootEntry]:
        return virtual.reconstruct_rootfs([layer(tar_bytes(group)) for group in groups])

    def test_whiteouts_apply_before_same_layer_entries(self) -> None:
        result = self.build(
            [("dir/old", "file", b"x"), ("dir/nested/a", "file", b"x")],
            [
                ("dir/new", "file", b"y"),
                ("dir/.wh..wh..opq", "file", b""),
                ("dir/old", "file", b"z"),
            ],
        )
        self.assertEqual(list(result), ["dir/new", "dir/old"])
        self.assertEqual(result["dir/old"].layer, 1)

    def test_whiteout_and_replacement_preserve_same_layer(self) -> None:
        result = self.build(
            [("a/old", "file", b"x"), ("b", "file", b"x")],
            [("a/new", "file", b"y"), (".wh.a", "file", b""), (".wh.b", "file", b"")],
        )
        self.assertEqual(list(result), ["a/new"])

    def test_hardlink_preserves_original_file_after_replacement(self) -> None:
        result = self.build(
            [("a", "file", b"x"), ("b", "hardlink", b"a")],
            [("a", "file", b"y"), ("c", "hardlink", b"b")],
        )
        self.assertEqual(result["b"].content, (0, "a"))
        self.assertEqual(result["c"].content, (0, "a"))
        self.assertEqual(result["a"].content, (1, "a"))

    def test_rejects_invalid_links_and_non_directory_parents(self) -> None:
        for groups in (
            ([("a", "hardlink", b"missing")],),
            ([("a", "hardlink", b"a")],),
            ([("a", "symlink", b"target")], [("a/b", "file", b"")]),
            ([("a", "symlink", b"target")], [("b", "hardlink", b"a")]),
            ([("a", "file", b"")], [("a/.wh.b", "file", b"")]),
        ):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                self.build(*groups)

    def test_directory_replaced_by_file_removes_descendants(self) -> None:
        result = self.build([("a/b", "file", b"x")], [("a", "file", b"y")])
        self.assertEqual(list(result), ["a"])

    def test_opaque_and_whiteout_order_is_deterministic(self) -> None:
        lower = [("d/old", "file", b"x"), ("d/sub/old", "file", b"y")]
        upper = [
            ("d/new", "file", b"z"),
            ("d/.wh..wh..opq", "file", b""),
            ("d/.wh.old", "file", b""),
        ]
        self.assertEqual(
            self.build(lower, upper), self.build(lower, list(reversed(upper)))
        )

    def test_root_opaque_and_whiteout_hardlink_lifetime(self) -> None:
        result = self.build(
            [("a", "file", b"x"), ("link", "hardlink", b"a")],
            [(".wh.a", "file", b""), ("copy", "hardlink", b"link")],
        )
        self.assertEqual(result["copy"].content, (0, "a"))
        self.assertNotIn("a", result)
        self.assertEqual(
            list(
                self.build(
                    [("a", "file", b"x")],
                    [("b", "file", b"y"), (".wh..wh..opq", "file", b"")],
                )
            ),
            ["b"],
        )

    def test_whiteout_parent_validation_does_not_depend_on_order(self) -> None:
        lower = [("a", "symlink", b"target")]
        upper = [(".wh.a", "file", b""), ("a/.wh.b", "file", b"")]
        for entries in (upper, list(reversed(upper))):
            with (
                self.subTest(entries=entries),
                self.assertRaisesRegex(ValueError, "Whiteout traverses non-directory"),
            ):
                self.build(lower, entries)

    def test_hardlink_cycles_and_forward_references_fail(self) -> None:
        for entries in (
            [("a", "hardlink", b"b"), ("b", "hardlink", b"a")],
            [("a", "hardlink", b"b"), ("b", "file", b"data")],
            [("a", "hardlink", b"../outside")],
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.build(entries)

    def test_rootfs_enforces_aggregate_entry_limit(self) -> None:
        with patch.object(virtual, "MAX_ENTRIES", 1), self.assertRaises(ValueError):
            self.build([("a", "file", b"")], [("b", "file", b"")])

    def test_invalid_whiteouts_fail_closed(self) -> None:
        for name, data in ((".wh.", b""), (".wh...", b""), (".wh.a", b"data")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.build([(name, "file", data)])


if __name__ == "__main__":
    unittest.main()
