import hashlib
import gzip
import json
from biosim_server.biosim_verify.models import VerifyWorkflowOutput


def encode_report(output: VerifyWorkflowOutput) -> tuple[bytes, int, str]:
    """Return (gzip-compressed JSON, uncompressed length, SHA-256 hex of the uncompressed JSON)."""
    json_data = json.dumps(
        output.model_dump(mode="json", exclude_none=True),
        allow_nan=True,
        ensure_ascii=False,
    )
    raw = json_data.encode("utf-8")
    compressed = gzip.compress(raw)
    return compressed, len(raw), hashlib.sha256(raw).hexdigest()


def decode_report(compressed: bytes, expected_sha256: str) -> VerifyWorkflowOutput:
    """Reverse encode_report. Raise ValueError for any bad input."""
    try:
        decompressed = gzip.decompress(compressed)
    except Exception as e:
        raise ValueError(f"Decompression failed: {e}") from e
    try:
        json_data = decompressed.decode("utf-8")
    except Exception as e:
        raise ValueError(f"JSON decoding failed: {e}") from e
    if hashlib.sha256(decompressed).hexdigest() != expected_sha256:
        raise ValueError("SHA-256 mismatch")
    try:
        output = VerifyWorkflowOutput.model_validate_json(json_data)
    except Exception as e:
        raise ValueError(f"JSON validation failed: {e}") from e
    return output
