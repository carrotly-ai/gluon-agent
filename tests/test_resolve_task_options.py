"""Tests for resolve_task_options() in models.py."""

import pytest

from gluon.models import (
    TASK_PROFILES,
    THINKING_BUDGET_TOKENS,
    TaskProfile,
    ThinkingBudget,
    resolve_task_options,
)


class TestDefaultProfile:
    """Tests for default profile behavior."""

    def test_no_args_returns_standard(self):
        result = resolve_task_options()
        standard = TASK_PROFILES[TaskProfile.STANDARD]
        assert result["model"] == standard["model"]
        assert result["max_thinking_tokens"] == standard["max_thinking_tokens"]
        assert result["effort"] == standard["effort"]

    def test_none_profile_returns_standard(self):
        result = resolve_task_options(profile=None)
        assert result["model"] == "sonnet"


class TestProfileResolution:
    """Tests for each profile returning correct base config."""

    @pytest.mark.parametrize("profile", list(TaskProfile))
    def test_each_profile_returns_correct_config(self, profile: TaskProfile):
        result = resolve_task_options(profile=profile)
        expected = TASK_PROFILES[profile]
        assert result["model"] == expected["model"]
        assert result["max_thinking_tokens"] == expected["max_thinking_tokens"]
        assert result["force_planning"] == expected["force_planning"]
        assert result["effort"] == expected["effort"]

    def test_quick_profile(self):
        result = resolve_task_options(profile=TaskProfile.QUICK)
        assert result["model"] == "haiku"
        assert result["max_thinking_tokens"] == -1
        assert result["effort"] == "low"
        assert result["force_planning"] is False

    def test_standard_profile(self):
        result = resolve_task_options(profile=TaskProfile.STANDARD)
        assert result["model"] == "sonnet"
        assert result["max_thinking_tokens"] == -1  # adaptive
        assert result["effort"] == "medium"

    def test_deep_profile(self):
        result = resolve_task_options(profile=TaskProfile.DEEP)
        assert result["model"] == "opus-4.6"
        assert result["max_thinking_tokens"] == -1  # adaptive
        assert result["effort"] == "high"

    def test_planning_profile(self):
        result = resolve_task_options(profile=TaskProfile.PLANNING)
        assert result["model"] == "opus-4.6"
        assert result["force_planning"] is True
        assert result["effort"] == "high"


class TestProfileFromString:
    """Tests for profile resolution from string values."""

    @pytest.mark.parametrize("name", ["quick", "standard", "deep", "planning"])
    def test_valid_string_profiles(self, name: str):
        result = resolve_task_options(profile=name)
        expected = TASK_PROFILES[TaskProfile(name)]
        assert result["model"] == expected["model"]

    def test_invalid_profile_string_falls_back_to_standard(self):
        result = resolve_task_options(profile="nonexistent")
        standard = TASK_PROFILES[TaskProfile.STANDARD]
        assert result["model"] == standard["model"]

    def test_uppercase_profile_string(self):
        result = resolve_task_options(profile="QUICK")
        assert result["model"] == "haiku"

    def test_mixed_case_profile_string(self):
        result = resolve_task_options(profile="Deep")
        assert result["model"] == "opus-4.6"


class TestModelOverride:
    """Tests for model override behavior."""

    def test_model_override_replaces_profile_model(self):
        result = resolve_task_options(profile=TaskProfile.QUICK, model="opus-4.6")
        assert result["model"] == "opus-4.6"

    def test_model_override_with_none_profile(self):
        result = resolve_task_options(model="haiku")
        assert result["model"] == "haiku"


class TestThinkingTokensOverride:
    """Tests for max_thinking_tokens and thinking_budget overrides."""

    def test_direct_max_thinking_tokens_override(self):
        result = resolve_task_options(max_thinking_tokens=5000)
        assert result["max_thinking_tokens"] == 5000

    def test_max_thinking_tokens_takes_precedence_over_budget(self):
        result = resolve_task_options(
            max_thinking_tokens=5000,
            thinking_budget=ThinkingBudget.HIGH,
        )
        assert result["max_thinking_tokens"] == 5000

    @pytest.mark.parametrize("budget", list(ThinkingBudget))
    def test_each_thinking_budget_resolves_correctly(self, budget: ThinkingBudget):
        result = resolve_task_options(thinking_budget=budget)
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[budget]

    def test_thinking_budget_adaptive_resolves_to_sentinel(self):
        result = resolve_task_options(thinking_budget=ThinkingBudget.ADAPTIVE)
        assert result["max_thinking_tokens"] == -1

    def test_thinking_budget_string_none(self):
        result = resolve_task_options(thinking_budget="none")
        assert result["max_thinking_tokens"] == 0

    def test_thinking_budget_string_low(self):
        result = resolve_task_options(thinking_budget="low")
        assert result["max_thinking_tokens"] == 4000

    def test_thinking_budget_string_medium(self):
        result = resolve_task_options(thinking_budget="medium")
        assert result["max_thinking_tokens"] == 10000

    def test_thinking_budget_string_high(self):
        result = resolve_task_options(thinking_budget="high")
        assert result["max_thinking_tokens"] == 16000

    def test_thinking_budget_string_ultrathink(self):
        result = resolve_task_options(thinking_budget="ultrathink")
        assert result["max_thinking_tokens"] == 32000

    def test_thinking_budget_string_adaptive(self):
        result = resolve_task_options(thinking_budget="adaptive")
        assert result["max_thinking_tokens"] == -1

    def test_invalid_thinking_budget_string_falls_back_to_medium(self):
        result = resolve_task_options(thinking_budget="invalid_budget")
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[ThinkingBudget.MEDIUM]


class TestEffortOverride:
    """Tests for effort override behavior."""

    def test_effort_override_replaces_profile_default(self):
        result = resolve_task_options(profile=TaskProfile.QUICK, effort="high")
        assert result["effort"] == "high"

    def test_profile_effort_defaults(self):
        assert resolve_task_options(profile=TaskProfile.QUICK)["effort"] == "low"
        assert resolve_task_options(profile=TaskProfile.STANDARD)["effort"] == "medium"
        assert resolve_task_options(profile=TaskProfile.DEEP)["effort"] == "high"
        assert resolve_task_options(profile=TaskProfile.PLANNING)["effort"] == "high"

    def test_effort_none_uses_profile_default(self):
        result = resolve_task_options(profile=TaskProfile.QUICK, effort=None)
        assert result["effort"] == "low"


class TestOtherOverrides:
    """Tests for force_planning, max_turns, max_budget_usd overrides."""

    def test_force_planning_override(self):
        result = resolve_task_options(profile=TaskProfile.STANDARD, force_planning=True)
        assert result["force_planning"] is True

    def test_force_planning_override_false_on_planning_profile(self):
        result = resolve_task_options(profile=TaskProfile.PLANNING, force_planning=False)
        assert result["force_planning"] is False

    def test_max_turns_override(self):
        result = resolve_task_options(max_turns=100)
        assert result["max_turns"] == 100

    def test_max_budget_usd_override(self):
        result = resolve_task_options(max_budget_usd=5.0)
        assert result["max_budget_usd"] == 5.0


class TestCombinedOverrides:
    """Tests for combining profile with multiple overrides."""

    def test_profile_plus_all_overrides(self):
        result = resolve_task_options(
            profile=TaskProfile.QUICK,
            model="opus-4.6",
            max_thinking_tokens=20000,
            max_turns=50,
            max_budget_usd=10.0,
            force_planning=True,
            effort="max",
        )
        assert result["model"] == "opus-4.6"
        assert result["max_thinking_tokens"] == 20000
        assert result["max_turns"] == 50
        assert result["max_budget_usd"] == 10.0
        assert result["force_planning"] is True
        assert result["effort"] == "max"


class TestReturnShape:
    """Tests for return dict structure."""

    def test_all_expected_keys_present(self):
        result = resolve_task_options()
        expected_keys = {"model", "max_thinking_tokens", "max_turns", "max_budget_usd", "force_planning", "effort"}
        assert set(result.keys()) == expected_keys

    def test_return_types(self):
        result = resolve_task_options()
        assert isinstance(result["model"], str)
        assert isinstance(result["max_thinking_tokens"], int)
        assert isinstance(result["force_planning"], bool)


# ===================================================================
# Role-based profiles (F3)
# ===================================================================


class TestRoleBasedProfiles:
    """Tests for new role-based task profiles."""

    def test_fix_profile(self):
        result = resolve_task_options(profile=TaskProfile.FIX)
        assert result["model"] == "sonnet"
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[ThinkingBudget.MEDIUM]
        assert result["effort"] == "medium"
        assert result["force_planning"] is False

    def test_review_profile(self):
        result = resolve_task_options(profile=TaskProfile.REVIEW)
        assert result["model"] == "haiku"
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[ThinkingBudget.LOW]
        assert result["effort"] == "low"

    def test_refactor_profile(self):
        result = resolve_task_options(profile=TaskProfile.REFACTOR)
        assert result["model"] == "sonnet"
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[ThinkingBudget.HIGH]
        assert result["effort"] == "high"

    def test_research_profile(self):
        result = resolve_task_options(profile=TaskProfile.RESEARCH)
        assert result["model"] == "opus-4.6"
        assert result["max_thinking_tokens"] == THINKING_BUDGET_TOKENS[ThinkingBudget.HIGH]
        assert result["effort"] == "high"

    @pytest.mark.parametrize(
        "name",
        ["fix", "review", "refactor", "research"],
    )
    def test_role_profiles_from_string(self, name: str):
        result = resolve_task_options(profile=name)
        expected = TASK_PROFILES[TaskProfile(name)]
        assert result["model"] == expected["model"]
        assert result["effort"] == expected["effort"]

    def test_role_profile_with_model_override(self):
        result = resolve_task_options(profile=TaskProfile.REVIEW, model="opus-4.6")
        assert result["model"] == "opus-4.6"

    def test_all_profiles_have_configs(self):
        for profile in TaskProfile:
            assert profile in TASK_PROFILES, f"Missing config for {profile}"
