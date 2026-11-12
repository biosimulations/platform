import gzip
import hashlib
import math
import pytest
from biosim_server.biosim_verify import ComparisonStatistics
from biosim_server.biosim_verify.report_store import ReportCorrupt
from biosim_server.biosim_verify.models import CompareSettings, GenerateStatisticsActivityOutput, VerifyWorkflowOutput, VerifyWorkflowStatus


# Fixtures
@pytest.fixture
def verify_workflow_output() -> VerifyWorkflowOutput:
    """Fixture for a VerifyWorkflowOutput object."""
    return VerifyWorkflowOutput(
        workflow_id="test_workflow_id",
        compare_settings=CompareSettings(
            user_description="",
            include_outputs=True,
            rel_tol=1e-6,
            abs_tol_min=1e-9,
            abs_tol_scale=1.0,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2026-11-11T00:00:00Z",
    )

# Tests
def test_nan_and_infinity_round_trip(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that NaN and infinity round trip correctly."""
    score = [float("nan"), float("inf"), float("-inf")]
    stats = ComparisonStatistics(
        dataset_name="dataset",
        simulator_version_i="amici:1.0",
        simulator_version_j="copasi:1.0",
        var_names=["x", "y", "z"],
        score=score,
    )
    
    verify_workflow_output.workflow_results = GenerateStatisticsActivityOutput(
        sims_run_info=[],
        comparison_statistics={"dataset": [[stats]]},
    )
    compressed, _length, sha256 = ReportCorrupt().encode_report(verify_workflow_output)
    decoded = ReportCorrupt().decode_report(compressed, sha256)
    assert decoded.workflow_results is not None
    back = decoded.workflow_results.comparison_statistics["dataset"][0][0].score
    assert back is not None
    assert math.isnan(back[0])
    assert math.isinf(back[1]) and back[1] > 0
    assert math.isinf(back[2]) and back[2] < 0
    
def test_encode_report(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is encoded correctly."""
    # non-ASCII forces len(str) != len(bytes); without it the length
    # assertion cannot distinguish the two
    verify_workflow_output.compare_settings.user_description = "café ✓"

    compressed, length, sha256 = ReportCorrupt().encode_report(verify_workflow_output)

    raw = gzip.decompress(compressed)
    assert raw.decode("utf-8").startswith("{")

    assert length == len(raw)

    assert len(sha256) == 64
    assert sha256 == hashlib.sha256(raw).hexdigest()

def test_decode_report(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is decoded correctly."""
    compressed, _length, sha256 = ReportCorrupt().encode_report(verify_workflow_output)

    decoded = ReportCorrupt().decode_report(compressed, sha256)

    assert decoded == verify_workflow_output

def test_decode_report_decompression_failure(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is rejected if the decompression fails."""
    bad_gzip = gzip.compress(b"bad-gzip")
    with pytest.raises(ReportCorrupt, match="bad-gzip"):
        ReportCorrupt().decode_report(bad_gzip, "unused-sha256")

def test_decode_report_json_decode_failure(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is rejected if the JSON decode fails."""
    compressed = gzip.compress(b"bad-json")
    with pytest.raises(ReportCorrupt, match="bad-json"):
        ReportCorrupt().decode_report(compressed, "unused-sha256")

def test_decode_report_json_validation_failure(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is rejected if the JSON validation fails."""
    compressed = gzip.compress(b"bad-json")
    with pytest.raises(ReportCorrupt, match="bad-json"):
        ReportCorrupt().decode_report(compressed, "unused-sha256")

def test_decode_report_rejects_mismatched_hash(verify_workflow_output: VerifyWorkflowOutput) -> None:
    """Test that the report is rejected if the hash is mismatched."""
    compressed, _length, _sha256 = ReportCorrupt().encode_report(verify_workflow_output)
    with pytest.raises(ReportCorrupt) as excinfo:
        ReportCorrupt().decode_report(compressed, "wrong-hash")
    assert excinfo.value.args[0] == "SHA-256 mismatch"
