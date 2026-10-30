"""Versioned archival codec for verification reports.

JSON's strict encoder cannot represent NaN and infinities.  The archive format
uses explicit tagged values so scientific results round-trip without changing
the public API's existing ``null`` rendering behavior.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import io
import math
import zlib
from typing import Any

CODEC_VERSION = "verification-json-gzip-v2"


def _encode(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, dict):
        return {"$object": [[str(k), _encode(v)] for k, v in sorted(value.items())]}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$float"}:
        return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}[value["$float"]]
    if isinstance(value, dict) and set(value) == {"$object"}:
        return {k: _decode(v) for k, v in value["$object"]}
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def encode_report(report: Any) -> tuple[bytes, str, int, str]:
    raw = json.dumps(_encode(report), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return gzip.compress(raw, mtime=0), CODEC_VERSION, len(raw), digest


def decode_report(payload: bytes, *, encoding: str = CODEC_VERSION, max_decompressed_bytes: int = 64 * 1024 * 1024) -> Any:
    if encoding != CODEC_VERSION:
        raise ValueError(f"unsupported report codec: {encoding}")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as decompressor:
            raw = decompressor.read(max_decompressed_bytes + 1)
    except zlib.error as exc:
        raise ValueError("corrupt payload") from exc
    if len(raw) > max_decompressed_bytes:
        raise ValueError("verification report exceeds decompression limit")
    return _decode(json.loads(raw))


def checksum(payload: bytes, *, encoding: str = CODEC_VERSION) -> str:
    decoded = decode_report(payload, encoding=encoding)
    raw = json.dumps(_encode(decoded), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()
