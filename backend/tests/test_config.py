"""Configuration boundary: semantic input size is validated here, not scattered through detectors."""
import pytest

from app.config import SEMANTIC_MAX_DIM_RANGE, Settings, validate_semantic_max_dim


@pytest.mark.parametrize("value", [448, 512, 640, "960", SEMANTIC_MAX_DIM_RANGE[0], SEMANTIC_MAX_DIM_RANGE[1]])
def test_valid_semantic_max_dim(value):
    assert validate_semantic_max_dim(value) == int(value)
    assert Settings(semantic_max_dim=value).semantic_max_dim == int(value)


@pytest.mark.parametrize("value", [0, 100, 2000, 512.5, "512.0", "", "huge", True, -1, None])
def test_invalid_semantic_max_dim_is_rejected(value):
    with pytest.raises(ValueError, match="TEACHBACK_SEMANTIC_MAX_DIM"):
        validate_semantic_max_dim(value)
    with pytest.raises(ValueError, match="TEACHBACK_SEMANTIC_MAX_DIM"):
        Settings(semantic_max_dim=value)


def test_default_semantic_max_dim_is_safe_640(monkeypatch):
    monkeypatch.delenv("TEACHBACK_SEMANTIC_MAX_DIM", raising=False)
    assert Settings().semantic_max_dim == 640
