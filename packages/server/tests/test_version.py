import pytest

from wuwa_story.version import (
    MIN_SUPPORTED_CLIENT_VERSION,
    is_caption_supported,
    is_supported_cutscene_version,
    is_supported_map_version,
    is_supported_version,
    parse_version_tuple,
)


def test_parse_version_tuple():
    assert parse_version_tuple("3.7.0") == (3, 7, 0)
    assert parse_version_tuple("3.8.1") == (3, 8, 1)
    assert parse_version_tuple("4.0.0") == (4, 0, 0)
    assert parse_version_tuple("3.8") == (3, 8)
    assert parse_version_tuple(None) is None
    assert parse_version_tuple("") is None
    assert parse_version_tuple("invalid") is None
    assert parse_version_tuple("3.a.1") is None


def test_is_supported_version():
    assert is_supported_version("3.7.0") is True
    assert is_supported_version("3.7.1") is True
    assert is_supported_version("3.8.0") is True
    assert is_supported_version("3.8.1") is True
    assert is_supported_version("3.8.2") is True
    assert is_supported_version("3.9.0") is True
    assert is_supported_version("4.0.0") is True
    assert is_supported_version("3.8") is True

    # Rejection of older incompatible versions (< 3.7.0)
    assert is_supported_version("3.6.0") is False
    assert is_supported_version("3.5.0") is False
    assert is_supported_version("2.1.0") is False
    assert is_supported_version("1.4") is False
    assert is_supported_version(None) is False
    assert is_supported_version("bad") is False


def test_capabilities_forward_compatible():
    # Future patch releases automatically work without code changes
    future_patch = "3.8.1"
    assert is_supported_map_version(future_patch) is True
    assert is_supported_cutscene_version(future_patch) is True
    assert is_caption_supported(future_patch) is True

    future_major = "4.0.0"
    assert is_supported_map_version(future_major) is True
    assert is_supported_cutscene_version(future_major) is True
    assert is_caption_supported(future_major) is True

    old = "3.6.0"
    assert is_supported_map_version(old) is False
    assert is_supported_cutscene_version(old) is False
    assert is_caption_supported(old) is False
