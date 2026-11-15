"""Focused offline tests for the SBOM core."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sbom
from jsonschema.exceptions import ValidationError


class SbomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bom.json"
        self.document = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "version": 1,
            "metadata": {
                "component": {"type": "application", "name": "source"},
                "tools": {
                    "components": [
                        {"type": "application", "name": "trivy", "version": "0.76.1"}
                    ]
                },
            },
            "components": [{"type": "library", "name": "example", "version": "1"}],
        }

    def write(self) -> None:
        self.path.write_text(json.dumps(self.document))

    def test_valid_and_hash(self) -> None:
        self.write()
        self.assertEqual(
            sbom.validate(self.path, sbom.digest(self.path)), self.document
        )
        with self.assertRaises(ValueError):
            sbom.validate(self.path, "0" * 64)

    def test_invalid_json(self) -> None:
        self.path.write_text("{")
        with self.assertRaises(json.JSONDecodeError):
            sbom.validate(self.path)

    def test_invalid_schema(self) -> None:
        self.document["version"] = "wrong"
        self.write()
        with self.assertRaises(ValidationError):
            sbom.validate(self.path)

    def test_empty_components(self) -> None:
        self.document["components"] = []
        self.write()
        with self.assertRaises(ValueError):
            sbom.validate(self.path)

    def test_inconsistent_metadata(self) -> None:
        self.document["metadata"]["tools"]["components"][0]["version"] = "0"
        self.write()
        with self.assertRaises(ValueError):
            sbom.validate(self.path)

    def test_missing_and_modified_tool(self) -> None:
        with self.assertRaises(FileNotFoundError):
            sbom.verify_tool(self.path)
        self.path.write_text("not trivy")
        with self.assertRaises(ValueError):
            sbom.verify_tool(self.path)

    def test_subprocess_failure(self) -> None:
        self.write()
        with (
            patch.object(sbom, "verify_tool"),
            patch.object(
                sbom, "run", side_effect=subprocess.CalledProcessError(1, "trivy")
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            sbom.generate(self.path, self.path, self.path.parent / "out.json")

    def test_image_resolution_and_missing(self) -> None:
        image_id = "sha256:" + "a" * 64
        response = subprocess.CompletedProcess(
            [], 0, json.dumps([{"Id": image_id, "RepoDigests": []}]), ""
        )
        with patch.object(sbom, "run", return_value=response):
            self.assertEqual(sbom.resolve_image("image")["image_id"], image_id)
        response.stdout = json.dumps([{"Id": "bad"}])
        with (
            patch.object(sbom, "run", return_value=response),
            self.assertRaises(ValueError),
        ):
            sbom.resolve_image("image")
        missing = subprocess.CalledProcessError(
            1, "docker", stderr="Error: No such image: x"
        )
        with patch.object(sbom, "run", side_effect=missing):
            self.assertEqual(sbom.resolve_image("x")["status"], "COVERAGE_PENDING")
        daemon_error = subprocess.CalledProcessError(
            1, "docker", stderr="daemon unavailable"
        )
        with (
            patch.object(sbom, "run", side_effect=daemon_error),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            sbom.resolve_image("x")

    def test_image_partial_and_failures(self) -> None:
        image_id = "sha256:" + "a" * 64
        self.document["metadata"]["component"].update(
            name=image_id, type="container", **{"bom-ref": "image-root"}
        )
        self.document["components"][0]["bom-ref"] = "component-one"
        self.document["dependencies"] = [
            {"ref": "image-root", "dependsOn": ["component-one"]},
            {"ref": "component-one", "dependsOn": []},
        ]
        self.write()
        expected_components = json.loads(json.dumps(self.document["components"]))
        expected_dependencies = json.loads(json.dumps(self.document["dependencies"]))

        def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[0] == "git":
                return subprocess.CompletedProcess(command, 0, "a" * 40, "")
            Path(command[command.index("--output") + 1]).write_text(
                json.dumps(self.document)
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        identity = {
            "reference": "test",
            "image_id": image_id,
            "repo_digests_unverified": [],
        }
        with (
            patch.object(sbom, "verify_tool"),
            patch.object(sbom, "resolve_image", return_value=identity),
            patch.object(sbom, "run", side_effect=fake_run),
        ):
            outputs = {}
            for image_name in ("backend-image", "nginx-image"):
                output = self.path.parent / f"{image_name}.json"
                report = sbom.generate_image(
                    self.path, "test", output, image_name=image_name
                )
                generated = sbom.validate(output, report["sha256"])
                self.assertEqual(report["status"], "COVERAGE_PARTIAL")
                self.assertEqual(report["components"], 1)
                self.assertEqual(
                    generated["metadata"]["component"]["purl"],
                    f"pkg:oci/{image_name}@{image_id}",
                )
                self.assertEqual(generated["components"], expected_components)
                self.assertEqual(generated["dependencies"], expected_dependencies)
                outputs[image_name] = output
            self.document["metadata"]["component"]["name"] = "wrong"
            with self.assertRaises(ValueError):
                sbom.generate_image(
                    self.path,
                    "test",
                    outputs["backend-image"],
                    image_name="backend-image",
                )
            self.document["metadata"]["component"]["name"] = image_id
            for purl in (
                "pkg:docker/backend-image@" + image_id,
                "pkg:oci/other@" + image_id,
                "pkg:oci/backend-image@sha256:" + "b" * 64,
            ):
                self.document["metadata"]["component"]["purl"] = purl
                with (
                    self.subTest(purl=purl.split("@", 1)[0]),
                    self.assertRaises(ValueError),
                ):
                    sbom.generate_image(
                        self.path,
                        "test",
                        outputs["backend-image"],
                        image_name="backend-image",
                    )
            self.document["metadata"]["component"].pop("purl")
            self.document["components"] = []
            with self.assertRaises(ValueError):
                sbom.generate_image(
                    self.path,
                    "test",
                    outputs["backend-image"],
                    image_name="backend-image",
                )

    def test_existing_image_purl_syntax(self) -> None:
        image_id = "sha256:" + "a" * 64
        root = {
            "type": "container",
            "name": image_id,
            "bom-ref": "image-root",
        }
        document = {"metadata": {"component": root}}
        expected = f"pkg:oci/backend-image@{image_id}"

        for purl in (expected, expected + "?arch=amd64"):
            with self.subTest(purl=purl):
                root["purl"] = purl
                self.assertFalse(
                    sbom._normalize_image_root_purl(document, image_id, "backend-image")
                )
                self.assertEqual(root["purl"], purl)

        for purl in (
            expected + "?!!!",
            expected + "?arch=%ZZ",
            expected + "?arch=%C3%28",
            expected + "#sub/path",
            expected + "?arch=amd64&ARCH=arm64",
            f"pkg:oci/backend-image@sha256:{'b' * 64}",
            None,
            "",
            7,
        ):
            with self.subTest(purl=purl), self.assertRaises(ValueError):
                root["purl"] = purl
                sbom._normalize_image_root_purl(document, image_id, "backend-image")

    def test_image_trivy_failure(self) -> None:
        identity = {"reference": "test", "image_id": "sha256:" + "a" * 64}
        results = [
            subprocess.CompletedProcess([], 0, "a" * 40, ""),
            subprocess.CalledProcessError(1, "trivy"),
        ]
        with (
            patch.object(sbom, "verify_tool"),
            patch.object(sbom, "resolve_image", return_value=identity),
            patch.object(sbom, "run", side_effect=results),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            sbom.generate_image(self.path, "test", self.path.parent / "out.json")

    def test_python_inventory_and_invalid_locks(self) -> None:
        self.path.write_text("Example_Name==1.0 --hash=sha256:" + "a" * 64 + "\n")
        expected, excluded = sbom.inventory(self.path, "python")
        self.assertEqual(expected, {("example-name", "1.0")})
        self.assertEqual(excluded, [])
        with self.path.open("a") as stream:
            stream.write("example-name==2 --hash=sha256:" + "b" * 64 + "\n")
        with self.assertRaises(ValueError):
            sbom.inventory(self.path, "python")
        for text in ("", "example==1\n"):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                sbom.inventory(self.path, "python")

    def test_npm_inventory_and_invalid_locks(self) -> None:
        lock = {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/a": {"version": "1"},
                "node_modules/b/node_modules/a": {"version": "1"},
            },
        }
        self.path.write_text(json.dumps(lock))
        expected, excluded = sbom.inventory(self.path, "npm")
        self.assertEqual(expected, {("a", "1")})
        self.assertEqual(len(excluded), 2)
        for value in (
            {"lockfileVersion": 1},
            {"lockfileVersion": 3, "packages": {"node_modules/a": {}}},
            {"lockfileVersion": 3, "packages": {"": {}}},
        ):
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                sbom.inventory(self.path, "npm")

    def test_coverage_mismatch_and_duplicates(self) -> None:
        document = {
            "components": [{"name": "a", "version": "2", "purl": "pkg:npm/a@2"}]
        }
        report = sbom.coverage(document, {("a", "1")}, [], "npm")
        self.assertEqual(report["status"], "COVERAGE_INCOMPLETE")
        self.assertEqual(report["version_mismatches"], ["a"])
        self.assertEqual(
            sbom.coverage(document, {("a", "2")}, [], "npm")["status"], "COVERAGE_PASS"
        )
        document["components"].append(document["components"][0])
        with self.assertRaises(ValueError):
            sbom.coverage(document, {("a", "2")}, [], "npm")
        with self.assertRaises(ValueError):
            sbom.coverage({"components": []}, {("a", "2")}, [], "npm")

    def test_reject_inconsistent_and_unclassified_components(self) -> None:
        component = {"name": "a", "version": "1", "purl": "pkg:npm/a@1"}
        for field, wrong in (("name", "b"), ("version", "2")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                sbom.coverage(
                    {"components": [{**component, field: wrong}]},
                    {("a", "1")},
                    [],
                    "npm",
                )
        report = sbom.coverage(
            {"components": [component, {"name": "extra", "version": "1"}]},
            {("a", "1")},
            [],
            "npm",
        )
        self.assertEqual(report["status"], "COVERAGE_INCOMPLETE")
        self.assertEqual(report["unclassified"], ["extra"])

    def test_output_collision_and_atomic_failure_preserve_inputs(self) -> None:
        self.path.write_text("original inventory")
        original_hash = sbom.digest(self.path)
        link = self.path.parent / "link"
        link.symlink_to(self.path)
        hardlink = self.path.parent / "hardlink"
        hardlink.hardlink_to(self.path)
        for output in (self.path, link, hardlink, sbom.SCHEMAS / "bom-1.7.schema.json"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                sbom.check_output(output, [self.path])
            self.assertEqual(sbom.digest(self.path), original_hash)
        output = self.path.parent / "result.json"
        output.write_text("previous output")
        with (
            patch.object(sbom, "validate"),
            patch.object(sbom.os, "replace", side_effect=OSError("write failed")),
            self.assertRaises(OSError),
        ):
            sbom.atomic_write(output, b"new", [self.path])
        self.assertEqual(output.read_text(), "previous output")
        self.assertEqual(sbom.digest(self.path), original_hash)


if __name__ == "__main__":
    unittest.main()
