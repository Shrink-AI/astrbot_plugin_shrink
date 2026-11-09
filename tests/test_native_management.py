import pytest

from shrink.adapters.native_management import preserve_secrets, redact


def test_secret_roundtrip_preserves_existing_values():
    original = {"url": "https://example.test", "headers": {"Authorization": "secret"}, "key": ["abc"]}
    shown = redact(original)
    assert "secret" not in str(shown) and "abc" not in str(shown)
    shown["url"] = "https://new.example.test"
    result = preserve_secrets(shown, original)
    assert result["headers"] == original["headers"]
    assert result["key"] == ["abc"]
    assert result["url"] == "https://new.example.test"
    with pytest.raises(ValueError):
        preserve_secrets({"key": "[已隐藏]"}, {})
