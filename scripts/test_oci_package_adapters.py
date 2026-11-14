"""Tests for restricted Debian and CycloneDX package adapters."""

import hashlib
import json
import unittest

import oci_package_adapters as adapters
import oci_package_reconciliation as reconciliation
import oci_virtual_layers as virtual
from jsonschema.exceptions import ValidationError
from test_oci_virtual_layers import layer, tar_bytes

IMAGE = "sha256:" + "a" * 64
PLATFORM = "linux/amd64"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dpkg(raw: bytes) -> reconciliation.Inventory:
    return adapters.debian_status(
        raw, expected_sha256=digest(raw), image_id=IMAGE, platform=PLATFORM
    )


def make_sbom(raw: bytes) -> bytes:
    document = json.loads(raw)
    document.update(specVersion="1.7", version=1)
    document["metadata"] = {
        "component": {
            "type": "container",
            "name": IMAGE,
            "bom-ref": "root",
            "purl": f"pkg:oci/backend-image@{IMAGE}",
        },
        "tools": {
            "components": [
                {"type": "application", "name": "trivy", "version": "0.76.1"}
            ]
        },
    }
    for index, component in enumerate(document["components"]):
        component.update(
            {"type": "library", "name": f"component-{index}", "bom-ref": f"c-{index}"}
        )
    document["dependencies"] = [
        {
            "ref": "root",
            "dependsOn": [component["bom-ref"] for component in document["components"]],
        }
    ]
    return json.dumps(document).encode()


def trivy(raw: bytes) -> reconciliation.Inventory:
    raw = make_sbom(raw)
    return adapters.trivy_cyclonedx(
        raw,
        expected_sha256=digest(raw),
        image_id=IMAGE,
        platform=PLATFORM,
        image_name="backend-image",
        expected_distro="debian-13.7",
    )


class AdapterTests(unittest.TestCase):
    def test_debian_and_trivy_version_comparison(self) -> None:
        raw = b"Package: openssl\nStatus: install ok installed\nVersion: 1:3.0-1\nArchitecture: amd64\n\nPackage: gone\nStatus: deinstall ok config-files\n"
        sbom = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [
                    {"purl": "pkg:deb/debian/openssl@1%3A3.0-2?arch=amd64"},
                    {"purl": "pkg:pypi/x@1"},
                ],
            }
        ).encode()
        result = reconciliation.reconcile(
            dpkg(raw), trivy(sbom), expected_image_id=IMAGE, expected_platform=PLATFORM
        )
        self.assertEqual(result.independent_only[0].version, "1:3.0-1")
        self.assertEqual(result.sbom_only[0].version, "1:3.0-2")
        self.assertTrue(
            any(package.ecosystem == "pypi" for package in result.sbom_only)
        )
        self.assertEqual(result.coverage, "COVERAGE_PARTIAL")

    def test_rejects_duplicate_critical_fields_and_continuations(self) -> None:
        for raw in (
            b"Package: a\nPackage: b\nStatus: install ok installed\n",
            b"Package: a\n extra\nStatus: install ok installed\n",
            b"Package: a\nStatus: install ok installed\nVersion: 1\n",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                dpkg(raw)

    def test_rejects_hash_mismatch_and_duplicate_json_keys(self) -> None:
        with self.assertRaises(ValueError):
            adapters.debian_status(
                b"x", expected_sha256="0" * 64, image_id=IMAGE, platform=PLATFORM
            )
        with self.assertRaises(ValueError):
            raw = b'{"bomFormat":"CycloneDX","components":[],"components":[]}'
            adapters.trivy_cyclonedx(
                raw,
                expected_sha256=digest(raw),
                image_id=IMAGE,
                platform=PLATFORM,
                image_name="backend-image",
                expected_distro="debian-13.7",
            )

    def test_status_read_from_verified_layers_and_held_packages(self) -> None:
        raw = (
            b"Package: a\nStatus: hold ok installed\nVersion: 1\nArchitecture: amd64\n"
        )
        base = layer(
            tar_bytes(
                [
                    ("source", "file", raw),
                    ("var/lib/dpkg/status", "hardlink", b"source"),
                ]
            )
        )
        update = layer(tar_bytes([("source", "file", b"changed")]))
        result = adapters.debian_from_layers(
            [base, update], image_id=IMAGE, platform=PLATFORM
        )
        self.assertEqual(result.packages[0].name, "a")
        damaged = bytearray(base.data)
        damaged[512] ^= 1
        with self.assertRaises(ValueError):
            adapters.debian_from_layers(
                [virtual.Layer(bytes(damaged), base.digest, base.diff_id, base.size)],
                image_id=IMAGE,
                platform=PLATFORM,
            )

    def test_missing_or_symlink_status_rejected(self) -> None:
        for entries in ([], [("var/lib/dpkg/status", "symlink", b"elsewhere")]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                adapters.debian_from_layers(
                    [layer(tar_bytes(entries))], image_id=IMAGE, platform=PLATFORM
                )

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dpkg(
                b"Package: a\nStatus: typo ok installed\nVersion: 1\nArchitecture: amd64\n"
            )

    def test_sbom_rejects_wrong_identity_bad_schema_and_wrong_tool(self) -> None:
        initial = make_sbom(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "components": [{"purl": "pkg:deb/debian/a@1?arch=amd64"}],
                }
            ).encode()
        )
        for field in ("identity", "name", "tool", "schema", "graph"):
            document = json.loads(initial)
            if field == "identity":
                document["metadata"]["component"]["name"] = "sha256:" + "b" * 64
            elif field == "name":
                document["metadata"]["component"]["purl"] = (
                    f"pkg:oci/nginx-image@{IMAGE}"
                )
            elif field == "tool":
                document["metadata"]["tools"]["components"][0]["version"] = "0"
            elif field == "schema":
                document["version"] = "invalid"
            else:
                document["dependencies"] = [{"ref": "root", "dependsOn": ["missing"]}]
            raw = json.dumps(document).encode()
            with (
                self.subTest(field=field),
                self.assertRaises((ValueError, ValidationError)),
            ):
                adapters.trivy_cyclonedx(
                    raw,
                    expected_sha256=digest(raw),
                    image_id=IMAGE,
                    platform=PLATFORM,
                    image_name="backend-image",
                    expected_distro="debian-13.7",
                )

    def test_alpine_verified_layers_and_cyclonedx_reconcile(self) -> None:
        raw = b"P:musl\nV:1.2.5-r2\nA:x86_64\nF:lib\nF:usr/lib\n\nP:busybox\nV:1.36.1-r1\nA:x86_64\n"
        image = layer(tar_bytes([("lib/apk/db/installed", "file", raw)]))
        independent = adapters.alpine_from_layers(
            [image], image_id=IMAGE, platform=PLATFORM
        )
        bom = trivy(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {"purl": "pkg:apk/alpine/musl@1.2.5-r2?arch=x86_64"},
                        {"purl": "pkg:apk/alpine/busybox@1.36.1-r2?arch=x86_64"},
                    ],
                }
            ).encode()
        )
        result = reconciliation.reconcile(
            independent, bom, expected_image_id=IMAGE, expected_platform=PLATFORM
        )
        self.assertEqual([p.version for p in result.independent_only], ["1.36.1-r1"])
        self.assertEqual([p.version for p in result.sbom_only], ["1.36.1-r2"])
        self.assertEqual(result.coverage, "COVERAGE_PARTIAL")

    def test_real_qualifiers_epoch_and_python_metadata(self) -> None:
        raw = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [
                    {
                        "purl": "pkg:deb/debian/bsdutils@2.41.5-0%2Bdeb13u1?arch=amd64&distro=debian-13.7&epoch=1"
                    },
                    {"purl": "pkg:pypi/My_Package@1.0.0"},
                ],
            }
        ).encode()
        found = trivy(raw)
        self.assertEqual(found.packages[0].version, "1:2.41.5-0+deb13u1")
        self.assertEqual(found.packages[1].name, "my-package")
        metadata = b"Metadata-Version: 2.3\nName: My_Package\nVersion: 1.0.0\n\n"
        image = layer(
            tar_bytes(
                [
                    (
                        "usr/local/lib/python3.12/site-packages/My_Package-1.0.0.dist-info/METADATA",
                        "file",
                        metadata,
                    )
                ]
            )
        )
        python = adapters.python_from_layers([image], image_id=IMAGE, platform=PLATFORM)
        self.assertEqual(python.packages[0], found.packages[1])
        self.assertTrue(python.unknown)

    def test_real_alpine_distro_qualifier_and_underscore_version(self) -> None:
        raw = make_sbom(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "purl": "pkg:apk/alpine/ncurses@6.5_p20251123-r0?arch=x86_64&distro=3.23.4"
                        }
                    ],
                }
            ).encode()
        )
        parsed = adapters.trivy_cyclonedx(
            raw,
            expected_sha256=digest(raw),
            image_id=IMAGE,
            platform=PLATFORM,
            image_name="backend-image",
            expected_distro="3.23.4",
        )
        self.assertEqual(parsed.packages[0].version, "6.5_p20251123-r0")
        mismatched = adapters.trivy_cyclonedx(
            raw,
            expected_sha256=digest(raw),
            image_id=IMAGE,
            platform=PLATFORM,
            image_name="backend-image",
            expected_distro="3.22.0",
        )
        self.assertEqual(mismatched.packages, ())
        self.assertTrue(mismatched.unknown)

    def test_alpine_malformed_duplicate_and_symlink_are_rejected(self) -> None:
        for raw in (
            b"P:a\nP:b\nV:1\nA:x86_64\n",
            b"P:a\nV:1\n",
            b"P:a\nV:1\nA:x86_64\n\nP:a\nV:1\nA:x86_64\n",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                adapters.alpine_installed(
                    raw, expected_sha256=digest(raw), image_id=IMAGE, platform=PLATFORM
                )
        image = layer(tar_bytes([("lib/apk/db/installed", "symlink", b"elsewhere")]))
        with self.assertRaises(ValueError):
            adapters.alpine_from_layers([image], image_id=IMAGE, platform=PLATFORM)

    def test_unknown_qualifiers_are_not_dropped(self) -> None:
        raw = json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [{"purl": "pkg:deb/debian/a@1?arch=amd64&distro=debian"}],
            }
        ).encode()
        parsed = trivy(raw)
        self.assertEqual(parsed.packages, ())
        self.assertTrue(parsed.unknown)


if __name__ == "__main__":
    unittest.main()
