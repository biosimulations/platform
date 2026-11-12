import hashlib
import gzip
import json
import logging
from biosim_server.biosim_verify.models import VerifyWorkflowOutput

logger = logging.getLogger(__name__)

class ReportCorrupt(RuntimeError):

    def encode_report(self, output: VerifyWorkflowOutput) -> tuple[bytes, int, str]:
        """Return (gzip-compressed JSON, uncompressed length, SHA-256 hex of the uncompressed JSON)."""
        json_data = json.dumps(output.model_dump(mode="json", exclude_none=True), allow_nan=True, ensure_ascii=False)
        raw = json_data.encode("utf-8")
        compressed = gzip.compress(raw)
        return compressed, len(raw), hashlib.sha256(raw).hexdigest()

    def decode_report(self, compressed: bytes, expected_sha256: str) -> VerifyWorkflowOutput:
        """Reverse encode_report. Raise ReportCorrupt for any bad input."""
        try:
            decompressed = gzip.decompress(compressed)
        except Exception as e:
            raise ReportCorrupt(f"Decompression failed: {e}") from e
        try:
            json_data = decompressed.decode("utf-8")
        except Exception as e:
            raise ReportCorrupt(f"JSON decoding failed: {e}") from e
        try:
            output = VerifyWorkflowOutput.model_validate_json(json_data)
        except Exception as e:
            raise ReportCorrupt(f"JSON validation failed: {e}") from e
        if hashlib.sha256(json_data.encode("utf-8")).hexdigest() != expected_sha256:
            raise ReportCorrupt("SHA-256 mismatch")
        return output