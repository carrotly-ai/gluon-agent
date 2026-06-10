"""WS-5 regression: chat-agent model resolution accepts aliases like "opus".

The run_task/resume_session tools document "opus/sonnet/haiku", but
``ModelTier("opus")`` raises — aliases must be resolved first. Previously this
made `opus` an "invalid model" and silently aborted the task.
"""

from __future__ import annotations

from gluon.chat_agent import _resolve_model_tier
from gluon.models_config import ModelTier


def test_opus_alias_resolves():
    assert _resolve_model_tier("opus") == ModelTier.OPUS_48
    assert _resolve_model_tier("OPUS") == ModelTier.OPUS_48


def test_tier_names_resolve():
    assert _resolve_model_tier("sonnet") == ModelTier.SONNET
    assert _resolve_model_tier("haiku") == ModelTier.HAIKU
    assert _resolve_model_tier("opus-4.6") == ModelTier.OPUS_46


def test_ui_aliases_resolve():
    assert _resolve_model_tier("claude-opus-4.8") == ModelTier.OPUS_48
    assert _resolve_model_tier("claude-sonnet-4.6") == ModelTier.SONNET


def test_unknown_model_returns_none():
    assert _resolve_model_tier("gpt-4") is None
    assert _resolve_model_tier("nonsense") is None
