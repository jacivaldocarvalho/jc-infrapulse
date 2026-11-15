"""Regression tests for the offline coverage contract."""

import copy
import json
import unittest

import image_coverage_contract as contract
from jsonschema.exceptions import ValidationError

IDS = {"backend-image": "sha256:" + "a" * 64, "nginx-image": "sha256:" + "b" * 64}


def fixture() -> dict:
    return {
        "schema_version": 1,
        "targets": [
            {
                "name": name,
                "platform": "linux/amd64",
                "image_id": image_id,
                "dimensions": {
                    key: {"state": "UNKNOWN", "evidence": [], "gaps": []}
                    for key in ("files", "layers", "packages", "oci")
                },
            }
            for name, image_id in IDS.items()
        ],
    }


def positive() -> dict:
    return {
        "producer": "inventory-tool",
        "version": "1.0",
        "digest": "sha256:" + "c" * 64,
        "scope": {
            "target": "backend-image",
            "platform": "linux/amd64",
            "dimension": "files",
            "image_id": IDS["backend-image"],
        },
        "result": "POSITIVE",
    }


class CoverageContractTests(unittest.TestCase):
    def test_malformed_json_fails_closed(self) -> None:
        for raw in ('{"schema_version":', '{"targets": []} trailing', ""):
            with self.subTest(raw=raw), self.assertRaises(json.JSONDecodeError):
                contract.validate_contract(raw, IDS)

    def test_json_size_boundary_and_utf8_bytes(self) -> None:
        raw = json.dumps(fixture())
        at_limit = raw + " " * (contract.MAX_CONTRACT_BYTES - len(raw.encode("utf-8")))
        contract.validate_contract(at_limit, IDS)
        with self.assertRaises(ValueError):
            contract.validate_contract(at_limit + " ", IDS)
        with self.assertRaises(ValueError):
            contract.parse_contract(
                '{"x":"' + "é" * (contract.MAX_CONTRACT_BYTES // 2) + '"}'
            )

    def test_unknown_and_partial_are_valid(self) -> None:
        doc = fixture()
        contract.validate_contract(json.dumps(doc), IDS)
        claim = doc["targets"][0]["dimensions"]["files"]
        claim.update(state="PARTIAL", evidence=[positive()], gaps=["unexamined files"])
        contract.validate_contract(json.dumps(doc), IDS)
        claim.update(
            state="UNKNOWN",
            evidence=[{**positive(), "result": "INSUFFICIENT"}],
            gaps=[],
        )
        contract.validate_contract(json.dumps(doc), IDS)

    def test_partial_requires_positive_and_gaps(self) -> None:
        for evidence, gaps in (([], ["gap"]), ([positive()], [])):
            doc = fixture()
            doc["targets"][0]["dimensions"]["files"].update(
                state="PARTIAL", evidence=evidence, gaps=gaps
            )
            with (
                self.subTest(evidence=evidence, gaps=gaps),
                self.assertRaises(ValueError),
            ):
                contract.validate_contract(json.dumps(doc), IDS)

    def test_complete_always_rejected(self) -> None:
        doc = fixture()
        doc["targets"][0]["dimensions"]["files"]["state"] = "COMPLETE"
        with self.assertRaises(ValueError):
            contract.validate_contract(json.dumps(doc), IDS)

    def test_invalid_targets_and_independent_ids(self) -> None:
        for change in ("duplicate", "extra", "platform", "id", "missing_ids"):
            doc = fixture()
            ids = copy.copy(IDS)
            if change == "duplicate":
                doc["targets"][1] = copy.deepcopy(doc["targets"][0])
            elif change == "extra":
                doc["targets"][0]["name"] = "other"
            elif change == "platform":
                doc["targets"][0]["platform"] = "linux/arm64"
            elif change == "id":
                doc["targets"][0]["image_id"] = IDS["nginx-image"]
            else:
                ids.pop("nginx-image")
            with (
                self.subTest(change=change),
                self.assertRaises((ValueError, ValidationError)),
            ):
                contract.validate_contract(json.dumps(doc), ids)

    def test_scope_duplicate_and_unknown_fields_rejected(self) -> None:
        for change in (
            "scope",
            "duplicate",
            "field",
            "dimension",
            "digest",
            "positive_unknown",
        ):
            doc = fixture()
            claim = doc["targets"][0]["dimensions"]["files"]
            claim.update(state="PARTIAL", evidence=[positive()], gaps=["gap"])
            if change == "scope":
                claim["evidence"][0]["scope"]["target"] = "nginx-image"
            elif change == "duplicate":
                claim["evidence"].append(copy.deepcopy(claim["evidence"][0]))
            elif change == "field":
                claim["evidence"][0]["unexpected"] = True
            elif change == "dimension":
                doc["targets"][0]["dimensions"].pop("oci")
            elif change == "digest":
                claim["evidence"][0]["digest"] = "invalid"
            else:
                claim["state"] = "UNKNOWN"
            with (
                self.subTest(change=change),
                self.assertRaises((ValueError, ValidationError)),
            ):
                contract.validate_contract(json.dumps(doc), IDS)

    def test_duplicate_json_key_rejected(self) -> None:
        with self.assertRaises(ValueError):
            contract.parse_contract('{"schema_version":1,"schema_version":1}')

    def test_public_api_rejects_duplicate_json_and_preparsed_objects(self) -> None:
        raw = json.dumps(fixture())
        for original, duplicate in (
            ('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
            ('"files": {', '"files": {}, "files": {'),
            (
                '"name": "backend-image"',
                '"name": "backend-image", "name": "backend-image"',
            ),
        ):
            with self.subTest(original=original), self.assertRaises(ValueError):
                contract.validate_contract(raw.replace(original, duplicate, 1), IDS)
        with self.assertRaises(TypeError):
            contract.validate_contract(fixture(), IDS)

    def test_schema_complete_is_not_public_policy(self) -> None:
        doc = fixture()
        doc["targets"][0]["dimensions"]["files"]["state"] = "COMPLETE"
        schema = json.loads(contract.SCHEMA.read_text())
        contract.Draft7Validator(schema).validate(doc)
        with self.assertRaises(ValueError):
            contract.validate_contract(json.dumps(doc), IDS)

    def test_evidence_string_boundaries(self) -> None:
        values = {
            "digest": [
                "sha256:" + "c" * 64 + "\n",
                "xsha256:" + "c" * 64,
                "sha256:" + "c" * 64 + "x",
                "sha256:" + "G" * 64,
            ],
            "producer": ["inventory\n", " inventory", "inventory suffix", "-inventory"],
            "version": ["1.0\n", " 1.0", "1.0 suffix", "+1.0"],
        }
        for field, invalid_values in values.items():
            for invalid in invalid_values:
                doc = fixture()
                evidence = positive()
                evidence[field] = invalid
                doc["targets"][0]["dimensions"]["files"].update(
                    state="PARTIAL", evidence=[evidence], gaps=["gap"]
                )
                with (
                    self.subTest(field=field, invalid=invalid),
                    self.assertRaises(ValidationError),
                ):
                    contract.validate_contract(json.dumps(doc), IDS)

    def test_unknown_fields_and_types_at_each_level(self) -> None:
        for level in ("root", "target", "dimensions", "claim", "evidence", "scope"):
            for mode in ("extra", "type"):
                doc = fixture()
                claim = doc["targets"][0]["dimensions"]["files"]
                claim.update(state="PARTIAL", evidence=[positive()], gaps=["gap"])
                objects = {
                    "root": doc,
                    "target": doc["targets"][0],
                    "dimensions": doc["targets"][0]["dimensions"],
                    "claim": claim,
                    "evidence": claim["evidence"][0],
                    "scope": claim["evidence"][0]["scope"],
                }
                obj = objects[level]
                if mode == "extra":
                    obj["unexpected"] = 1
                else:
                    keys = {
                        "root": "targets",
                        "target": "name",
                        "dimensions": "files",
                        "claim": "state",
                        "evidence": "producer",
                        "scope": "image_id",
                    }
                    obj[keys[level]] = 42
                with (
                    self.subTest(level=level, mode=mode),
                    self.assertRaises(ValidationError),
                ):
                    contract.validate_contract(json.dumps(doc), IDS)

    def test_scope_fields_and_expected_ids_must_match(self) -> None:
        for field, value in (
            ("target", "nginx-image"),
            ("platform", "linux/arm64"),
            ("dimension", "layers"),
            ("image_id", IDS["nginx-image"]),
        ):
            doc = fixture()
            evidence = positive()
            evidence["scope"][field] = value
            doc["targets"][0]["dimensions"]["files"].update(
                state="PARTIAL", evidence=[evidence], gaps=["gap"]
            )
            with (
                self.subTest(field=field),
                self.assertRaises((ValueError, ValidationError)),
            ):
                contract.validate_contract(json.dumps(doc), IDS)
        with self.assertRaises(ValueError):
            contract.validate_contract(
                json.dumps(fixture()),
                {**IDS, "backend-image": "sha256:" + "a" * 64 + "\n"},
            )


if __name__ == "__main__":
    unittest.main()
