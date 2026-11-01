"""Validate raw JSON and apply policy beyond the structural JSON Schema.

The caller must supply image IDs obtained independently of the contract.
Evidence metadata is checked; evidence bytes and origin are not verified.
"""

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

SCHEMA = (
    Path(__file__).resolve().parent.parent / "security/image-coverage-v1.schema.json"
)
TARGETS = frozenset({"backend-image", "nginx-image"})
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_CONTRACT_BYTES = 1024 * 1024


def parse_contract(raw: str) -> dict[str, Any]:
    """Parse bounded UTF-8 JSON without silently accepting duplicate keys."""
    if not isinstance(raw, str):
        raise TypeError("Raw JSON text is required")
    if len(raw) > MAX_CONTRACT_BYTES or len(raw.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise ValueError("Contract exceeds the byte limit")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    document = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(document, dict):
        raise TypeError("Contract must be an object")
    return document


def validate_contract(raw: str, expected_ids: dict[str, str]) -> None:
    """Parse without duplicate keys and enforce policy with external expected IDs."""
    if not isinstance(raw, str):
        raise TypeError("Raw JSON text is required")
    if not isinstance(expected_ids, dict) or set(expected_ids) != TARGETS:
        raise ValueError("Independent image IDs required for exactly two targets")
    if any(
        not isinstance(value, str) or not IMAGE_ID.fullmatch(value)
        for value in expected_ids.values()
    ):
        raise ValueError("Invalid independent image ID")
    document = parse_contract(raw)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(document)
    seen_targets: set[str] = set()
    seen_evidence: set[tuple[str, str, str, str, str]] = set()
    for target in document["targets"]:
        name = target["name"]
        if name in seen_targets or target["image_id"] != expected_ids[name]:
            raise ValueError("Duplicate target or independent image ID mismatch")
        seen_targets.add(name)
        for dimension, claim in target["dimensions"].items():
            if claim["state"] == "COMPLETE":
                raise ValueError("COMPLETE requires an unavailable independent proof")
            if claim["state"] == "PARTIAL" and (
                not claim["gaps"]
                or not any(e["result"] == "POSITIVE" for e in claim["evidence"])
            ):
                raise ValueError("PARTIAL requires positive evidence and explicit gaps")
            if claim["state"] == "UNKNOWN" and any(
                e["result"] == "POSITIVE" for e in claim["evidence"]
            ):
                raise ValueError("UNKNOWN cannot assert positive evidence")
            for evidence in claim["evidence"]:
                scope = evidence["scope"]
                if scope != {
                    "target": name,
                    "platform": target["platform"],
                    "dimension": dimension,
                    "image_id": expected_ids[name],
                }:
                    raise ValueError("Evidence scope mismatch")
                key = (
                    name,
                    dimension,
                    evidence["producer"],
                    evidence["version"],
                    evidence["digest"],
                )
                if key in seen_evidence:
                    raise ValueError("Duplicate evidence")
                seen_evidence.add(key)
    if seen_targets != TARGETS:
        raise ValueError("Missing target")
