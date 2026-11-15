"""Local Cosign signing and offline trust verification for F3B.1 statements."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import provenance
import sbom

ROOT = Path(__file__).resolve().parent.parent
COSIGN_LOCK = ROOT / "security/cosign-tools.json"
MAX_POLICY_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024
MAX_STATEMENT_BYTES = 16 * 1024 * 1024
MAX_KEY_BYTES = 64 * 1024


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _open_regular(path: Path, limit: int) -> tuple[int, bytes]:
    """Open and snapshot one regular file through no-follow directory descriptors."""
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Input path must be absolute and normalized")
    parts = path.parts[1:]
    if not parts:
        raise ValueError("Expected a regular file")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    directory_fd = os.open("/", flags)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("Input is not an acceptable regular file")
            chunks = bytearray()
            while len(chunks) <= limit:
                block = os.read(fd, min(65536, limit + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
            after = os.fstat(fd)
            current = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
            if (
                len(chunks) > limit
                or (before.st_dev, before.st_ino)
                != (
                    after.st_dev,
                    after.st_ino,
                )
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise ValueError("Input changed or exceeded its size limit")
            return fd, bytes(chunks)
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(directory_fd)


def _read_regular(path: Path, limit: int) -> bytes:
    fd, data = _open_regular(path, limit)
    os.close(fd)
    return data


def _json_file(path: Path, limit: int) -> tuple[bytes, dict[str, Any]]:
    raw = _read_regular(path, limit)
    try:
        document = json.loads(raw, object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed JSON input") from exc
    if not isinstance(document, dict):
        raise TypeError("JSON input must be an object")
    return raw, document


def _tool_lock() -> dict[str, Any]:
    lock = json.loads(COSIGN_LOCK.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, dict)
        or lock.get("schema_version") != 1
        or lock.get("name") != "cosign"
        or lock.get("platform") != "linux-amd64"
        or not re.fullmatch(r"v?\d+\.\d+\.\d+", str(lock.get("version", "")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(lock.get("sha256", "")))
    ):
        raise ValueError("Invalid Cosign tool lock")
    return lock


def _run_cosign(tool: Path, arguments: list[str], timeout: int = 120) -> bytes:
    lock = _tool_lock()
    fd, raw = _open_regular(tool.absolute(), 256 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != lock["sha256"]:
        os.close(fd)
        raise ValueError("Cosign SHA-256 mismatch")
    try:
        executable = f"/proc/self/fd/{fd}"
        result = subprocess.run(
            [executable, "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "COSIGN_EXPERIMENTAL": "0"},
            pass_fds=(fd,),
        )
        match = re.search(r"(?m)^GitVersion:\s*v?(\d+\.\d+\.\d+)\s*$", result.stdout)
        if not match or match.group(1) != lock["version"].removeprefix("v"):
            raise ValueError("Cosign version mismatch")
        if not arguments:
            return result.stdout.encode()
        operation = subprocess.run(
            [executable, *arguments],
            check=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "COSIGN_EXPERIMENTAL": "0"},
            pass_fds=(fd,),
        )
        return operation.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Cosign operation failed") from exc
    finally:
        os.close(fd)


def verify_cosign(tool: Path) -> dict[str, Any]:
    _run_cosign(tool, [])
    return _tool_lock()


def sign_blob_bytes(
    payload: bytes,
    bundle_path: Path,
    private_key: Path,
    cosign: Path,
    protected_inputs: list[Path] | None = None,
) -> None:
    """Sign a validated byte snapshot; callers own semantic validation."""
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_STATEMENT_BYTES
    ):
        raise ValueError("Blob payload is empty or exceeds the size limit")
    verify_cosign(cosign)
    key_fd, key_bytes = _open_regular(private_key.absolute(), MAX_KEY_BYTES)
    os.close(key_fd)
    if not key_bytes:
        raise ValueError("Signing key is empty")
    inputs = [private_key, cosign, *(protected_inputs or [])]
    sbom.check_output(bundle_path, inputs)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=bundle_path.parent, prefix=".cosign-blob-"
    ) as temporary:
        temporary_path = Path(temporary) / "bundle.sigstore.json"
        sbom.check_output(temporary_path, inputs)
        with tempfile.TemporaryDirectory(prefix="infrapulse-cosign-blob-") as work:
            snapshot = Path(work) / "payload.bin"
            private = Path(work) / "private.key"
            snapshot.write_bytes(payload)
            private.write_bytes(key_bytes)
            snapshot.chmod(0o600)
            private.chmod(0o600)
            _run_cosign(
                cosign,
                [
                    "sign-blob",
                    "--yes",
                    # Explicitly avoid Rekor/TUF service access for local signing.
                    "--tlog-upload=false",
                    "--use-signing-config=false",
                    "--key",
                    str(private),
                    "--bundle",
                    str(temporary_path),
                    str(snapshot),
                ],
            )
        _, bundle = _json_file(temporary_path, MAX_BUNDLE_BYTES)
        if not bundle:
            raise ValueError("Cosign produced an empty bundle")
        sbom.check_output(bundle_path, inputs)
        os.replace(temporary_path, bundle_path)


def verify_blob_bytes(
    payload: bytes,
    bundle_bytes: bytes,
    policy_path: Path,
    policy_sha256: str,
    key_id: str,
    cosign: Path,
) -> dict[str, str]:
    """Verify exact blob bytes offline against the independently pinned policy."""
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_STATEMENT_BYTES
    ):
        raise ValueError("Blob payload is empty or exceeds the size limit")
    if (
        not isinstance(bundle_bytes, bytes)
        or not bundle_bytes
        or len(bundle_bytes) > MAX_BUNDLE_BYTES
    ):
        raise ValueError("Signature bundle is empty or exceeds the size limit")
    verify_cosign(cosign)
    try:
        bundle = json.loads(bundle_bytes, object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed signature bundle") from exc
    if not isinstance(bundle, dict) or not bundle:
        raise ValueError("Signature bundle must be a non-empty object")
    public_key = _trusted_key(policy_path, policy_sha256, key_id)
    with tempfile.TemporaryDirectory(prefix="infrapulse-cosign-blob-verify-") as work:
        directory = Path(work)
        key_path = directory / "trusted.pub"
        payload_path = directory / "payload.bin"
        bundle_path = directory / "bundle.sigstore.json"
        for path, data in (
            (key_path, public_key),
            (payload_path, payload),
            (bundle_path, bundle_bytes),
        ):
            path.write_bytes(data)
            path.chmod(0o600)
        _run_cosign(
            cosign,
            [
                "verify-blob",
                "--offline",
                # Self-managed keys are not logged; retain signature checking
                # while explicitly omitting Rekor inclusion verification.
                "--insecure-ignore-tlog",
                "--key",
                str(key_path),
                "--bundle",
                str(bundle_path),
                str(payload_path),
            ],
        )
    return {"signature": "SIGNATURE_VALID", "signer": "TRUSTED_SIGNER"}


def _validate_provenance(
    statement_path: Path,
    sbom_path: Path,
    manifest_path: Path,
    trivy_path: Path,
    expected_image_id: str | None,
    expected_image_name: str | None = None,
) -> bytes:
    if expected_image_id is not None:
        if expected_image_name not in {"backend-image", "nginx-image"}:
            raise ValueError("A trusted logical image name is required")
    elif expected_image_name is not None:
        raise ValueError("Logical image name requires an independent image ID")
    raw, document = _json_file(statement_path, MAX_STATEMENT_BYTES)
    provenance.verify(
        document,
        sbom_path,
        manifest_path,
        trivy_path,
        expected_image_id,
        expected_image_name,
    )
    return raw


def sign_statement(
    statement_path: Path,
    bundle_path: Path,
    private_key: Path,
    cosign: Path,
    sbom_path: Path,
    manifest_path: Path,
    trivy_path: Path,
    expected_image_id: str | None = None,
    expected_image_name: str | None = None,
) -> None:
    """Sign exact statement bytes after F3B.1 integrity validation."""
    verify_cosign(cosign)
    statement_bytes = _validate_provenance(
        statement_path,
        sbom_path,
        manifest_path,
        trivy_path,
        expected_image_id,
        expected_image_name,
    )
    inputs = [statement_path, private_key, cosign, sbom_path, manifest_path, trivy_path]
    sbom.check_output(bundle_path, inputs)
    key_fd, key_data = _open_regular(private_key.absolute(), MAX_KEY_BYTES)
    os.close(key_fd)
    if not key_data:
        raise ValueError("Signing key is empty")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=bundle_path.parent, prefix=".cosign-bundle-"
    ) as work:
        temporary = Path(work) / "bundle.sigstore.json"
        sbom.check_output(temporary, inputs)
        with tempfile.TemporaryDirectory(
            prefix="infrapulse-cosign-sign-"
        ) as snapshot_dir:
            snapshot = Path(snapshot_dir) / "statement.json"
            private = Path(snapshot_dir) / "private.key"
            snapshot.write_bytes(statement_bytes)
            private.write_bytes(key_data)
            snapshot.chmod(0o600)
            private.chmod(0o600)
            _run_cosign(
                cosign,
                [
                    "sign-blob",
                    "--yes",
                    "--key",
                    str(private),
                    "--bundle",
                    str(temporary),
                    str(snapshot),
                ],
            )
        _, bundle = _json_file(temporary, MAX_BUNDLE_BYTES)
        if not bundle:
            raise ValueError("Cosign produced an empty bundle")
        sbom.check_output(bundle_path, inputs)
        os.replace(temporary, bundle_path)


def _trusted_key(policy_path: Path, expected_policy_sha256: str, key_id: str) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_policy_sha256):
        raise ValueError("Expected policy SHA-256 is required")
    raw, policy = _json_file(policy_path, MAX_POLICY_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected_policy_sha256:
        raise ValueError("Trust policy SHA-256 mismatch")
    if policy.get("schema_version") != 1 or set(policy) != {"schema_version", "keys"}:
        raise ValueError("Invalid trust policy schema")
    keys = policy.get("keys")
    if not isinstance(keys, dict) or key_id not in keys:
        raise ValueError("Signer is not authorized by trust policy")
    key = keys[key_id]
    if not isinstance(key, dict) or set(key) != {"public_key", "sha256", "status"}:
        raise ValueError("Invalid trusted key entry")
    if key["status"] != "ACTIVE":
        raise ValueError("Signing key is revoked or inactive")
    pem = key["public_key"]
    if not isinstance(pem, str) or not pem.startswith("-----BEGIN PUBLIC KEY-----"):
        raise ValueError("Invalid trusted public key")
    key_bytes = pem.encode("ascii", errors="strict")
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    if key["sha256"] != key_hash or key_id != f"sha256:{key_hash}":
        raise ValueError("Trusted key fingerprint mismatch")
    return key_bytes


def verify_signature(
    statement_path: Path,
    bundle_path: Path,
    policy_path: Path,
    policy_sha256: str,
    key_id: str,
    cosign: Path,
    sbom_path: Path,
    manifest_path: Path,
    trivy_path: Path,
    expected_image_id: str | None = None,
    expected_image_name: str | None = None,
) -> dict[str, str]:
    """Verify integrity, then the signature against independently pinned policy."""
    verify_cosign(cosign)
    statement_bytes = _validate_provenance(
        statement_path,
        sbom_path,
        manifest_path,
        trivy_path,
        expected_image_id,
        expected_image_name,
    )
    bundle_bytes, bundle = _json_file(bundle_path, MAX_BUNDLE_BYTES)
    if not bundle:
        raise ValueError("Signature bundle is empty")
    public_key = _trusted_key(policy_path, policy_sha256, key_id)
    with tempfile.TemporaryDirectory(prefix="infrapulse-trusted-key-") as directory:
        key_path = Path(directory) / "trusted.pub"
        statement_snapshot = Path(directory) / "statement.json"
        bundle_snapshot = Path(directory) / "bundle.sigstore.json"
        key_path.write_bytes(public_key)
        key_path.chmod(0o600)
        statement_snapshot.write_bytes(statement_bytes)
        bundle_snapshot.write_bytes(bundle_bytes)
        for snapshot in (statement_snapshot, bundle_snapshot):
            snapshot.chmod(0o600)
        _run_cosign(
            cosign,
            [
                "verify-blob",
                "--offline",
                # The independently pinned key remains authoritative; these
                # signatures intentionally have no transparency-log entry.
                "--insecure-ignore-tlog",
                "--key",
                str(key_path),
                "--bundle",
                str(bundle_snapshot),
                str(statement_snapshot),
            ],
        )
    return {
        "integrity": "INTEGRITY_VERIFIED",
        "signature": "SIGNATURE_VALID",
        "signer": "TRUSTED_SIGNER",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("sign", "verify"):
        command = subparsers.add_parser(action)
        command.add_argument("--statement", type=Path, required=True)
        command.add_argument("--sbom", type=Path, required=True)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--trivy", type=Path, required=True)
        command.add_argument("--cosign", type=Path, required=True)
        command.add_argument("--expected-image-id")
        command.add_argument(
            "--expected-image-name", choices=("backend-image", "nginx-image")
        )
        if action == "sign":
            command.add_argument("--private-key", type=Path, required=True)
            command.add_argument("--bundle", type=Path, required=True)
        else:
            command.add_argument("--bundle", type=Path, required=True)
            command.add_argument("--policy", type=Path, required=True)
            command.add_argument("--policy-sha256", required=True)
            command.add_argument("--key-id", required=True)
    args = parser.parse_args()
    try:
        if args.action == "sign":
            sign_statement(
                args.statement,
                args.bundle,
                args.private_key,
                args.cosign,
                args.sbom,
                args.manifest,
                args.trivy,
                args.expected_image_id,
                args.expected_image_name,
            )
            result = {"status": "SIGNED", "integrity": "INTEGRITY_VERIFIED"}
        else:
            result = verify_signature(
                args.statement,
                args.bundle,
                args.policy,
                args.policy_sha256,
                args.key_id,
                args.cosign,
                args.sbom,
                args.manifest,
                args.trivy,
                args.expected_image_id,
                args.expected_image_name,
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
        provenance.ValidationError,
        provenance.RefResolutionError,
    ) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
