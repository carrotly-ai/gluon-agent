"""Tests for RateLimiter - API call and cost limiting."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from gluon.rate_limiter import RateLimiter, RateLimiterConfig


class TestRateLimiterBasics:
    """Test basic rate limiter behavior."""

    def test_initial_state_allows_calls(self):
        """Fresh rate limiter allows calls."""
        rl = RateLimiter()
        allowed, reason = rl.can_make_call()
        assert allowed
        assert reason == ""

    def test_record_call_increments_counter(self):
        """Recording a call increments the counter."""
        rl = RateLimiter()
        assert rl.calls_this_hour == 0
        rl.record_call()
        assert rl.calls_this_hour == 1
        rl.record_call()
        assert rl.calls_this_hour == 2

    def test_record_call_with_cost(self):
        """Recording a call with cost adds to total."""
        rl = RateLimiter()
        assert rl.total_cost_usd == 0.0
        rl.record_call(cost_usd=0.05)
        assert rl.total_cost_usd == pytest.approx(0.05)
        rl.record_call(cost_usd=0.10)
        assert rl.total_cost_usd == pytest.approx(0.15)


class TestRateLimiterCallLimit:
    """Test call-per-hour limits."""

    def test_blocks_at_limit(self):
        """Blocks calls when limit is reached."""
        config = RateLimiterConfig(max_calls_per_hour=3)
        rl = RateLimiter(config)

        # Make 3 calls - should all work
        for _ in range(3):
            allowed, _ = rl.can_make_call()
            assert allowed
            rl.record_call()

        # 4th call should be blocked
        allowed, reason = rl.can_make_call()
        assert not allowed
        assert "Rate limit" in reason
        assert "3/3" in reason

    def test_allows_before_limit(self):
        """Allows calls before reaching limit."""
        config = RateLimiterConfig(max_calls_per_hour=10)
        rl = RateLimiter(config)

        for i in range(9):
            rl.record_call()
            allowed, _ = rl.can_make_call()
            assert allowed, f"Should allow call {i+2} of 10"


class TestRateLimiterCostCap:
    """Test cost cap enforcement."""

    def test_blocks_at_cost_cap(self):
        """Blocks calls when cost cap is exceeded."""
        config = RateLimiterConfig(max_calls_per_hour=100, max_cost_usd=1.00)
        rl = RateLimiter(config)

        # Spend 90 cents - still allowed
        rl.record_call(cost_usd=0.90)
        allowed, _ = rl.can_make_call()
        assert allowed

        # Spend another 20 cents - exceeds cap
        rl.record_call(cost_usd=0.20)
        allowed, reason = rl.can_make_call()
        assert not allowed
        assert "Cost cap" in reason
        assert "$1.10" in reason or "1.1" in reason

    def test_no_cost_cap_by_default(self):
        """No cost cap when not configured."""
        config = RateLimiterConfig(max_cost_usd=None)
        rl = RateLimiter(config)

        # Spend a lot - should still be allowed (calls permitting)
        rl.record_call(cost_usd=100.00)
        allowed, _ = rl.can_make_call()
        assert allowed


class TestRateLimiterHourlyReset:
    """Test hourly reset behavior."""

    def test_resets_after_hour(self):
        """Counter resets after an hour passes."""
        config = RateLimiterConfig(max_calls_per_hour=5)
        rl = RateLimiter(config)

        # Max out calls
        for _ in range(5):
            rl.record_call()
        allowed, _ = rl.can_make_call()
        assert not allowed

        # Simulate time passing (more than 1 hour)
        rl.hour_start = datetime.now(UTC) - timedelta(hours=1, minutes=1)

        # Should be allowed again
        allowed, _ = rl.can_make_call()
        assert allowed
        assert rl.calls_this_hour == 0

    def test_cost_not_reset_with_hour(self):
        """Total cost is NOT reset with hourly reset."""
        rl = RateLimiter()
        rl.record_call(cost_usd=0.50)
        assert rl.total_cost_usd == 0.50

        # Simulate hour passing
        rl.hour_start = datetime.now(UTC) - timedelta(hours=1, minutes=1)
        rl._maybe_reset_hour()

        # Calls reset but cost doesn't
        assert rl.calls_this_hour == 0
        assert rl.total_cost_usd == 0.50


class TestRateLimiterTimeUntilReset:
    """Test time calculation methods."""

    def test_time_until_reset(self):
        """Calculate time until hourly reset."""
        rl = RateLimiter()
        # Set hour_start to 30 minutes ago
        rl.hour_start = datetime.now(UTC) - timedelta(minutes=30)

        remaining = rl.time_until_reset()
        # Should be about 30 minutes left
        assert 25 * 60 <= remaining.total_seconds() <= 35 * 60

    def test_seconds_until_reset(self):
        """Get seconds until reset."""
        rl = RateLimiter()
        rl.hour_start = datetime.now(UTC) - timedelta(minutes=45)

        seconds = rl.seconds_until_reset()
        # Should be about 15 minutes = 900 seconds
        assert 800 <= seconds <= 1000

    def test_time_until_reset_never_negative(self):
        """Time until reset is never negative."""
        rl = RateLimiter()
        # Set hour_start to 2 hours ago
        rl.hour_start = datetime.now(UTC) - timedelta(hours=2)

        remaining = rl.time_until_reset()
        assert remaining.total_seconds() >= 0


class TestRateLimiterStatus:
    """Test status reporting."""

    def test_get_status(self):
        """Get comprehensive status dict."""
        config = RateLimiterConfig(max_calls_per_hour=10, max_cost_usd=5.00)
        rl = RateLimiter(config)

        rl.record_call(cost_usd=0.25)
        rl.record_call(cost_usd=0.25)

        status = rl.get_status()
        assert status["calls_this_hour"] == 2
        assert status["max_calls_per_hour"] == 10
        assert status["calls_remaining"] == 8
        assert status["total_cost_usd"] == 0.50
        assert status["max_cost_usd"] == 5.00
        assert status["at_limit"] is False
        assert status["at_cost_cap"] is False

    def test_status_at_limit(self):
        """Status shows at_limit when limit reached."""
        config = RateLimiterConfig(max_calls_per_hour=2)
        rl = RateLimiter(config)

        rl.record_call()
        rl.record_call()

        status = rl.get_status()
        assert status["at_limit"] is True
        assert status["calls_remaining"] == 0

    def test_status_at_cost_cap(self):
        """Status shows at_cost_cap when exceeded."""
        config = RateLimiterConfig(max_cost_usd=1.00)
        rl = RateLimiter(config)

        rl.record_call(cost_usd=1.50)

        status = rl.get_status()
        assert status["at_cost_cap"] is True


class TestRateLimiterSerialization:
    """Test state serialization and restoration."""

    def test_to_dict(self):
        """Serialize state to dictionary."""
        rl = RateLimiter()
        rl.calls_this_hour = 5
        rl.total_cost_usd = 1.23
        rl.hour_start = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)

        data = rl.to_dict()
        assert data["calls_this_hour"] == 5
        assert data["total_cost_usd"] == 1.23
        assert "2024-01-15" in data["hour_start"]

    def test_from_dict(self):
        """Restore state from dictionary."""
        data = {
            "calls_this_hour": 7,
            "total_cost_usd": 2.50,
            "hour_start": "2024-01-15T10:30:00+00:00",
        }

        rl = RateLimiter.from_dict(data)
        assert rl.calls_this_hour == 7
        assert rl.total_cost_usd == 2.50
        assert rl.hour_start.year == 2024
        assert rl.hour_start.month == 1

    def test_from_dict_with_config(self):
        """from_dict respects provided config."""
        config = RateLimiterConfig(max_calls_per_hour=50, max_cost_usd=10.00)
        data = {"calls_this_hour": 3}

        rl = RateLimiter.from_dict(data, config)
        assert rl.config.max_calls_per_hour == 50
        assert rl.config.max_cost_usd == 10.00

    def test_from_dict_defaults(self):
        """from_dict uses defaults for missing keys."""
        data = {}
        rl = RateLimiter.from_dict(data)
        assert rl.calls_this_hour == 0
        assert rl.total_cost_usd == 0.0


class TestRateLimiterConfig:
    """Test configuration."""

    def test_custom_config(self):
        """Custom config is respected."""
        config = RateLimiterConfig(max_calls_per_hour=50, max_cost_usd=25.00)
        rl = RateLimiter(config)
        assert rl.config.max_calls_per_hour == 50
        assert rl.config.max_cost_usd == 25.00

    def test_default_config(self):
        """Default config has sensible values."""
        config = RateLimiterConfig()
        assert config.max_calls_per_hour == 100
        assert config.max_cost_usd is None
