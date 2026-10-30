import math

import pytest

from biosim_server.biosim_verify.report_codec import decode_report, encode_report

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


def test_report_codec_preserves_non_finite_values_and_dotted_paths() -> None:
    original = {
        "datasets.foo.bar": {"values": [1.0, float("nan"), float("inf"), float("-inf")]},
        "provenance": {"run_ids": ["one"], "shape": [2, 2]},
    }
    payload, encoding, _, _ = encode_report(original)
    restored = decode_report(payload, encoding=encoding)
    values = restored["datasets.foo.bar"]["values"]
    assert values[0] == 1.0
    assert math.isnan(values[1])
    assert values[2] == float("inf")
    assert values[3] == float("-inf")
    assert restored["provenance"] == original["provenance"]


def test_report_codec_rejects_oversized_decompression() -> None:
    payload, encoding, _, _ = encode_report({"value": "x" * 1000})
    try:
        decode_report(payload, encoding=encoding, max_decompressed_bytes=10)
    except ValueError as exc:
        assert "decompression limit" in str(exc)
    else:
        raise AssertionError("oversized payload was accepted")


def test_codec_tags_do_not_collide_with_user_dictionaries() -> None:
    original = {"$float": "not-a-number-tag", "nested": {"$float": "nan"}, "$object": [["a", "b"]]}
    encoded, version, _, _ = encode_report(original)
    assert decode_report(encoded, encoding=version) == original


def test_invalid_deflate_block_is_classified_as_corruption() -> None:
    import zlib
    payload, encoding, _, _ = encode_report({'data': list(range(20))})
    corrupt = bytearray(payload)
    corrupt[10] = (corrupt[10] & ~0x06) | 0x06  # reserved DEFLATE block type
    with pytest.raises(ValueError, match='corrupt payload') as error:
        decode_report(bytes(corrupt), encoding=encoding)
    assert isinstance(error.value.__cause__, zlib.error)
