"""Tests for bounded package reconciliation and fail-closed inputs."""

import unittest
from unittest.mock import patch

import oci_package_reconciliation as reconciliation

IMAGE = "sha256:" + "a" * 64
PLATFORM = "linux/amd64"


def inv(
    source: str, packages: tuple[reconciliation.Package, ...] = (), **kwargs: object
) -> reconciliation.Inventory:
    return reconciliation.Inventory(IMAGE, PLATFORM, source, packages, **kwargs)


def compare(
    left: reconciliation.Inventory, right: reconciliation.Inventory
) -> reconciliation.Comparison:
    return reconciliation.reconcile(
        left, right, expected_image_id=IMAGE, expected_platform=PLATFORM
    )


class ReconciliationTests(unittest.TestCase):
    def test_missing_extra_and_version_changes(self) -> None:
        old = reconciliation.Package("deb", "openssl", "1")
        new = reconciliation.Package("deb", "openssl", "2")
        common = reconciliation.Package("pypi", "fastapi", "1")
        result = compare(inv("database", (old, common)), inv("trivy", (new, common)))
        self.assertEqual(result.independent_only, (old,))
        self.assertEqual(result.sbom_only, (new,))
        self.assertEqual(result.coverage, "COVERAGE_PARTIAL")

    def test_unknowns_and_unsupported_are_reported_even_if_equal(self) -> None:
        unknown = reconciliation.Package("unknown-ecosystem", "x", "1")
        result = compare(
            inv("database", (unknown,), unknown=("unsupported database",)),
            inv("trivy", (unknown,)),
        )
        self.assertEqual(result.independent_only, ())
        self.assertEqual(
            result.unknown,
            (
                "independent: unsupported database",
                "unsupported ecosystem: unknown-ecosystem",
            ),
        )
        self.assertEqual(result.coverage, "COVERAGE_PARTIAL")

    def test_scope_identity_duplicates_and_invalid_values_fail(self) -> None:
        pkg = reconciliation.Package("deb", "a", "1")
        invalid = (
            (inv("same"), inv("same")),
            (inv("a", (pkg, pkg)), inv("b")),
            (inv("a", (reconciliation.Package("deb", "bad\nname", "1"),)), inv("b")),
            (
                reconciliation.Inventory("sha256:" + "b" * 64, PLATFORM, "a", ()),
                inv("b"),
            ),
        )
        for left, right in invalid:
            with self.subTest(left=left), self.assertRaises(ValueError):
                compare(left, right)

    def test_limits_and_input_order(self) -> None:
        a = reconciliation.Package("deb", "a", "1")
        b = reconciliation.Package("deb", "b", "1")
        self.assertEqual(compare(inv("a", (b, a)), inv("b")).independent_only, (a, b))
        with (
            patch.object(reconciliation, "MAX_PACKAGES", 1),
            self.assertRaises(ValueError),
        ):
            compare(inv("a", (a, b)), inv("b"))


if __name__ == "__main__":
    unittest.main()
