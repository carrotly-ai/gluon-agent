"""Circuit breaker pattern for preventing runaway ralph loops.

The circuit breaker has three states:
- CLOSED: Normal operation, execution allowed
- HALF_OPEN: Monitoring mode, watching for recovery
- OPEN: Execution halted, requires manual intervention

State transitions:
- CLOSED → HALF_OPEN: After 2 consecutive no-progress iterations
- CLOSED → OPEN: After threshold reached (no progress or repeated errors)
- HALF_OPEN → CLOSED: Progress detected (files changed)
- HALF_OPEN → OPEN: No recovery, threshold exceeded
"""

from dataclasses import dataclass
from hashlib import sha256

from gluon.models import CircuitState


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker thresholds."""

    no_progress_threshold: int = 5  # Open after N loops with no file changes (increased for patience)
    same_error_threshold: int = 5  # Open after N loops with same error
    half_open_threshold: int = 2  # Enter HALF_OPEN after N no-progress loops
    half_open_patience: int = 3  # Stay in HALF_OPEN for N additional loops before OPEN


class CircuitBreaker:
    """State machine for detecting and preventing runaway loops.

    Tracks progress across loop iterations and transitions between states
    based on file changes and error patterns.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.consecutive_no_progress = 0
        self.consecutive_same_error = 0
        self.last_error_hash: str | None = None
        self.last_progress_loop: int = 0
        self.half_open_iterations: int = 0  # Track iterations spent in HALF_OPEN

    def record_iteration(
        self,
        loop_number: int,
        files_changed: int,
        has_errors: bool,
        error_summary: str | None,
        output_length: int,
    ) -> CircuitState:
        """Record loop iteration result and update state.

        Args:
            loop_number: Current loop iteration number
            files_changed: Number of git file changes detected
            has_errors: Whether errors occurred in this iteration
            error_summary: First ~200 chars of error message
            output_length: Length of Claude's output

        Returns:
            New circuit state after processing this iteration
        """
        # Progress detection based on file changes
        if files_changed > 0:
            self.consecutive_no_progress = 0
            self.last_progress_loop = loop_number
            # Recovery: HALF_OPEN → CLOSED when progress detected
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.half_open_iterations = 0
        else:
            self.consecutive_no_progress += 1

        # Error repetition detection
        if has_errors and error_summary:
            error_hash = self._hash_error(error_summary)
            if error_hash == self.last_error_hash:
                self.consecutive_same_error += 1
            else:
                self.consecutive_same_error = 1
                self.last_error_hash = error_hash
        else:
            self.consecutive_same_error = 0
            self.last_error_hash = None

        # State transitions
        if self.state == CircuitState.CLOSED:
            # Repeated errors cause immediate OPEN
            if self.consecutive_same_error >= self.config.same_error_threshold:
                self.state = CircuitState.OPEN
            # No progress triggers warning state (HALF_OPEN) first
            elif self.consecutive_no_progress >= self.config.half_open_threshold:
                self.state = CircuitState.HALF_OPEN
                self.half_open_iterations = 0
        elif self.state == CircuitState.HALF_OPEN:
            # Track patience window in HALF_OPEN
            if files_changed == 0:
                self.half_open_iterations += 1
            # Exhaust patience window → OPEN
            if self.half_open_iterations >= self.config.half_open_patience:
                self.state = CircuitState.OPEN
            # Repeated same error also triggers OPEN
            elif self.consecutive_same_error >= self.config.same_error_threshold:
                self.state = CircuitState.OPEN

        return self.state

    def can_execute(self) -> bool:
        """Check if execution is allowed based on current state."""
        return self.state != CircuitState.OPEN

    def reset(self) -> None:
        """Manual reset to CLOSED state.

        Call this when manually intervening or retrying after fixing issues.
        """
        self.state = CircuitState.CLOSED
        self.consecutive_no_progress = 0
        self.consecutive_same_error = 0
        self.last_error_hash = None
        self.half_open_iterations = 0

    def get_open_reason(self) -> str:
        """Get human-readable reason why circuit is open."""
        if self.state != CircuitState.OPEN:
            return ""

        if self.consecutive_same_error >= self.config.same_error_threshold:
            return f"Same error repeated {self.consecutive_same_error} times"
        if self.half_open_iterations >= self.config.half_open_patience:
            return (
                f"No recovery during HALF_OPEN patience window "
                f"({self.half_open_iterations} iterations, "
                f"{self.consecutive_no_progress} total no-progress)"
            )
        if self.consecutive_no_progress >= self.config.no_progress_threshold:
            return f"No progress in {self.consecutive_no_progress} consecutive loops"
        return "Circuit manually opened"

    def to_dict(self) -> dict:
        """Serialize state for persistence or display."""
        return {
            "state": self.state.value,
            "consecutive_no_progress": self.consecutive_no_progress,
            "consecutive_same_error": self.consecutive_same_error,
            "last_progress_loop": self.last_progress_loop,
            "last_error_hash": self.last_error_hash,
            "half_open_iterations": self.half_open_iterations,
        }

    @classmethod
    def from_dict(cls, data: dict, config: CircuitBreakerConfig | None = None) -> "CircuitBreaker":
        """Restore state from serialized dict."""
        cb = cls(config)
        cb.state = CircuitState(data.get("state", "CLOSED"))
        cb.consecutive_no_progress = data.get("consecutive_no_progress", 0)
        cb.consecutive_same_error = data.get("consecutive_same_error", 0)
        cb.last_progress_loop = data.get("last_progress_loop", 0)
        cb.last_error_hash = data.get("last_error_hash")
        cb.half_open_iterations = data.get("half_open_iterations", 0)
        return cb

    @staticmethod
    def _hash_error(error_summary: str) -> str:
        """Create hash of error for comparison.

        Normalizes the error by taking first 200 chars to avoid
        minor variations triggering different hashes.
        """
        normalized = error_summary[:200].strip().lower()
        return sha256(normalized.encode()).hexdigest()[:16]
