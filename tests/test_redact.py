from __future__ import annotations

from adjoint.config import DEFAULT_REDACT_PATTERNS
from adjoint.memory.redact import Redactor, from_config


def test_default_patterns_redact_known_secrets() -> None:
    r = from_config(DEFAULT_REDACT_PATTERNS)

    cases = {
        "anthropic: sk-ant-abc123XYZ-_": "anthropic_api_key",
        "slack: xoxb-12345-ABCDE": "slack_token",
        "github: ghp_" + "A" * 40: "github_pat",
        "openai: sk-" + "A" * 48: "openai_key",
        "openai proj: sk-proj-abc-_123": "openai_proj_key",
    }
    for text, expected_label in cases.items():
        sanitized = r.sanitize(text)
        assert f"[REDACTED:{expected_label}]" in sanitized, (text, sanitized)


def test_multiple_matches_in_one_line() -> None:
    r = from_config(DEFAULT_REDACT_PATTERNS)
    text = "two keys: sk-ant-foo and xoxb-1-2-3 done"
    out = r.sanitize(text)
    assert "sk-ant-" not in out
    assert "xoxb-" not in out
    assert out.count("[REDACTED:") == 2


def test_invalid_pattern_is_silently_dropped() -> None:
    r = Redactor(patterns=["[unterminated", r"sk-ant-\w+"])
    assert r.sanitize("sk-ant-abc").startswith("[REDACTED:")


def test_empty_input_is_untouched() -> None:
    r = from_config(DEFAULT_REDACT_PATTERNS)
    assert r.sanitize("") == ""
    assert r.sanitize("plain text, no secrets") == "plain text, no secrets"


def test_custom_pattern_gets_stable_label() -> None:
    r = from_config([r"INTERNAL-[A-Z]{4}-\d+"])
    out = r.sanitize("token=INTERNAL-ABCD-123 end")
    assert "[REDACTED:custom_" in out
