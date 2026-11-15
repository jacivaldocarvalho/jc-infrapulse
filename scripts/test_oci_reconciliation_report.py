"""Contract tests for intentionally untrusted reconciliation summaries."""

import hashlib
import unittest
from unittest.mock import patch

import oci_reconciliation_report as reports
from oci_package_reconciliation import Comparison, Package


class ReportTests(unittest.TestCase):
    def test_hashes_differences_and_mandatory_gaps(self) -> None:
        package = Package("npm", "demo", "1.0.0")
        comparison = Comparison(
            "sha256:" + "a" * 64, "linux/amd64", (package,), (), ("unknown",)
        )
        result = reports.report(
            comparison, independent_bytes=b"independent", sbom_bytes=b"sbom"
        )
        self.assertEqual(
            result["unverified_independent_sha256"],
            hashlib.sha256(b"independent").hexdigest(),
        )
        self.assertEqual(result["independent_only"][0]["name"], "demo")
        self.assertEqual(result["source_authentication"], "UNVERIFIED")
        self.assertEqual(result["coverage"], "COVERAGE_PARTIAL")
        self.assertTrue(set(reports.MANDATORY_GAPS) <= set(result["gaps"]))
        self.assertIn("unknown", result["gaps"])

    def test_invalid_or_oversized_input_fails(self) -> None:
        comparison = Comparison("sha256:" + "a" * 64, "linux/amd64", (), (), ())
        with patch.object(reports, "MAX_INPUT", 2), self.assertRaises(ValueError):
            reports.report(comparison, independent_bytes=b"abc", sbom_bytes=b"")
        with self.assertRaises(TypeError):
            reports.report(None, independent_bytes=b"", sbom_bytes=b"")
        bad = Comparison("sha256:" + "a" * 64, "linux/amd64", (), (), (), "COMPLETE")
        with self.assertRaises(ValueError):
            reports.report(bad, independent_bytes=b"", sbom_bytes=b"")


if __name__ == "__main__":
    unittest.main()
