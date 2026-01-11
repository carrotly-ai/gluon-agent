"""Rate limiting for API calls and cost control in ralph loops.

Provides hourly call limits and optional cost caps to prevent
runaway loops from burning through API credits.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class RateLimiterConfig:
    """Configuration for rate limiting."""

    max_calls_per_hour: int = 100  # Maximum API calls per hour
    max_cost_usd: float | None = None  # Optional cost cap (None = no limit)


class RateLimiter:
    """Enforces rate limits and cost caps.

    Tracks API calls and costs, resets hourly, and provides
    wait time calculations for rate limit recovery.
    """

    def __init__(self, config: RateLimiterConfig | None = None):
        self.config = config or RateLimiterConfig()
        self.calls_this_hour = 0
        self.hour_start = datetime.now(UTC)
        self.total_cost_usd = 0.0

    def can_make_call(self) -> tuple[bool, str]:
        """Check if another API call is allowed.

        Returns:
            Tuple of (allowed, reason_if_not_allowed)
        """
        self._maybe_reset_hour()

        if self.calls_this_hour >= self.config.max_calls_per_hour:
            return (
                False,
                f"Rate limit: {self.calls_this_hour}/{self.config.max_calls_per_hour} calls this hour",
            )

        if self.config.max_cost_usd and self.total_cost_usd >= self.config.max_cost_usd:
            return (
                False,
                f"Cost cap: ${self.total_cost_usd:.2f} >= ${self.config.max_cost_usd:.2f}",
            )

        return True, ""

    def record_call(self, cost_usd: float = 0.0) -> None:
        """Record an API call with optional cost.

        Args:
            cost_usd: Cost of this API call in USD
        """
        self._maybe_reset_hour()
        self.calls_this_hour += 1
        self.total_cost_usd += cost_usd

    def time_until_reset(self) -> timedelta:
        """Get time remaining until hourly counter resets."""
        next_hour = self.hour_start + timedelta(hours=1)
        now = datetime.now(UTC)
        return max(next_hour - now, timedelta(0))

    def seconds_until_reset(self) -> int:
        """Get seconds until hourly counter resets."""
        return int(self.time_until_reset().total_seconds())

    def get_status(self) -> dict:
        """Get current rate limiter status for display."""
        self._maybe_reset_hour()
        return {
            "calls_this_hour": self.calls_this_hour,
            "max_calls_per_hour": self.config.max_calls_per_hour,
            "calls_remaining": max(0, self.config.max_calls_per_hour - self.calls_this_hour),
            "total_cost_usd": self.total_cost_usd,
            "max_cost_usd": self.config.max_cost_usd,
            "seconds_until_reset": self.seconds_until_reset(),
            "at_limit": self.calls_this_hour >= self.config.max_calls_per_hour,
            "at_cost_cap": bool(self.config.max_cost_usd and self.total_cost_usd >= self.config.max_cost_usd),
        }

    def _maybe_reset_hour(self) -> None:
        """Reset counter if hour has passed."""
        now = datetime.now(UTC)
        if now - self.hour_start >= timedelta(hours=1):
            self.hour_start = now
            self.calls_this_hour = 0
            # Note: total_cost_usd is NOT reset - it tracks total run cost

    def to_dict(self) -> dict:
        """Serialize state for persistence."""
        return {
            "calls_this_hour": self.calls_this_hour,
            "hour_start": self.hour_start.isoformat(),
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict, config: RateLimiterConfig | None = None) -> "RateLimiter":
        """Restore state from serialized dict."""
        rl = cls(config)
        rl.calls_this_hour = data.get("calls_this_hour", 0)
        rl.total_cost_usd = data.get("total_cost_usd", 0.0)

        hour_start_str = data.get("hour_start")
        if hour_start_str:
            try:
                rl.hour_start = datetime.fromisoformat(hour_start_str)
                # Ensure timezone awareness
                if rl.hour_start.tzinfo is None:
                    rl.hour_start = rl.hour_start.replace(tzinfo=UTC)
            except ValueError:
                rl.hour_start = datetime.now(UTC)

        return rl
