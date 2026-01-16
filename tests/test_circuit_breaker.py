"""Tests for CircuitBreaker - runaway loop prevention."""

from gluon.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from gluon.models import CircuitState


class TestCircuitBreakerBasics:
    """Test basic circuit breaker behavior."""

    def test_initial_state_closed(self):
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute()

    def test_progress_resets_counters(self):
        """File changes reset no-progress counter."""
        cb = CircuitBreaker()
        # Simulate no progress
        cb.record_iteration(1, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.consecutive_no_progress == 1
        # File change resets counter
        cb.record_iteration(2, files_changed=3, has_errors=False, error_summary=None, output_length=100)
        assert cb.consecutive_no_progress == 0
        assert cb.last_progress_loop == 2

    def test_reset_method(self):
        """Manual reset clears all counters."""
        cb = CircuitBreaker()
        cb.state = CircuitState.OPEN
        cb.consecutive_no_progress = 5
        cb.consecutive_same_error = 3
        cb.half_open_iterations = 2
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_no_progress == 0
        assert cb.consecutive_same_error == 0
        assert cb.half_open_iterations == 0


class TestCircuitBreakerTransitions:
    """Test state transition logic."""

    def test_closed_to_half_open_on_no_progress(self):
        """CLOSED transitions to HALF_OPEN after threshold no-progress iterations."""
        config = CircuitBreakerConfig(half_open_threshold=2)
        cb = CircuitBreaker(config)

        # First no-progress: stay CLOSED
        cb.record_iteration(1, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.CLOSED

        # Second no-progress: transition to HALF_OPEN
        cb.record_iteration(2, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_recovers_to_closed_on_progress(self):
        """HALF_OPEN recovers to CLOSED when progress detected."""
        config = CircuitBreakerConfig(half_open_threshold=2)
        cb = CircuitBreaker(config)

        # Get to HALF_OPEN
        cb.record_iteration(1, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        cb.record_iteration(2, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.HALF_OPEN

        # Progress detected - recover to CLOSED
        cb.record_iteration(3, files_changed=1, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.CLOSED
        assert cb.half_open_iterations == 0

    def test_half_open_to_open_after_patience(self):
        """HALF_OPEN transitions to OPEN after patience window exhausted."""
        config = CircuitBreakerConfig(half_open_threshold=2, half_open_patience=2)
        cb = CircuitBreaker(config)

        # Get to HALF_OPEN
        cb.record_iteration(1, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        cb.record_iteration(2, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.HALF_OPEN

        # First patience iteration - still HALF_OPEN
        cb.record_iteration(3, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.half_open_iterations == 1

        # Second patience iteration - transition to OPEN
        cb.record_iteration(4, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.OPEN
        assert not cb.can_execute()

    def test_repeated_errors_cause_open(self):
        """Repeated same error causes direct transition to OPEN."""
        # Use high no_progress threshold so errors are the main factor
        config = CircuitBreakerConfig(same_error_threshold=3, half_open_threshold=10, no_progress_threshold=10)
        cb = CircuitBreaker(config)

        error_msg = "Connection timeout"
        # First error
        cb.record_iteration(1, files_changed=0, has_errors=True, error_summary=error_msg, output_length=0)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_same_error == 1

        # Second same error
        cb.record_iteration(2, files_changed=0, has_errors=True, error_summary=error_msg, output_length=0)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_same_error == 2

        # Third same error - OPEN
        cb.record_iteration(3, files_changed=0, has_errors=True, error_summary=error_msg, output_length=0)
        assert cb.state == CircuitState.OPEN

    def test_different_error_resets_counter(self):
        """Different error resets the same-error counter."""
        config = CircuitBreakerConfig(same_error_threshold=3)
        cb = CircuitBreaker(config)

        # First error
        cb.record_iteration(1, files_changed=0, has_errors=True, error_summary="Error A", output_length=0)
        assert cb.consecutive_same_error == 1

        # Different error - counter resets to 1
        cb.record_iteration(2, files_changed=0, has_errors=True, error_summary="Error B", output_length=0)
        assert cb.consecutive_same_error == 1

    def test_success_clears_error_counter(self):
        """Successful iteration clears error counter."""
        cb = CircuitBreaker()
        cb.record_iteration(1, files_changed=0, has_errors=True, error_summary="Error", output_length=0)
        assert cb.consecutive_same_error == 1

        cb.record_iteration(2, files_changed=1, has_errors=False, error_summary=None, output_length=100)
        assert cb.consecutive_same_error == 0
        assert cb.last_error_hash is None


class TestCircuitBreakerOpenReason:
    """Test human-readable open reason messages."""

    def test_open_reason_same_error(self):
        """Get reason for opening due to repeated errors."""
        config = CircuitBreakerConfig(same_error_threshold=2)
        cb = CircuitBreaker(config)

        cb.record_iteration(1, files_changed=0, has_errors=True, error_summary="Error", output_length=0)
        cb.record_iteration(2, files_changed=0, has_errors=True, error_summary="Error", output_length=0)
        assert cb.state == CircuitState.OPEN
        assert "Same error repeated" in cb.get_open_reason()

    def test_open_reason_half_open_exhausted(self):
        """Get reason for opening due to HALF_OPEN patience exhaustion."""
        config = CircuitBreakerConfig(half_open_threshold=1, half_open_patience=1)
        cb = CircuitBreaker(config)

        cb.record_iteration(1, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_iteration(2, files_changed=0, has_errors=False, error_summary=None, output_length=100)
        assert cb.state == CircuitState.OPEN
        assert "No recovery" in cb.get_open_reason()

    def test_no_reason_when_not_open(self):
        """Get empty string when circuit is not OPEN."""
        cb = CircuitBreaker()
        assert cb.get_open_reason() == ""


class TestCircuitBreakerSerialization:
    """Test state serialization and restoration."""

    def test_to_dict(self):
        """Serialize state to dictionary."""
        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN
        cb.consecutive_no_progress = 3
        cb.consecutive_same_error = 1
        cb.last_progress_loop = 5
        cb.half_open_iterations = 2
        cb.last_error_hash = "abc123"

        data = cb.to_dict()
        assert data["state"] == "HALF_OPEN"
        assert data["consecutive_no_progress"] == 3
        assert data["consecutive_same_error"] == 1
        assert data["last_progress_loop"] == 5
        assert data["half_open_iterations"] == 2
        assert data["last_error_hash"] == "abc123"

    def test_from_dict(self):
        """Restore state from dictionary."""
        data = {
            "state": "HALF_OPEN",
            "consecutive_no_progress": 4,
            "consecutive_same_error": 2,
            "last_progress_loop": 7,
            "half_open_iterations": 3,
            "last_error_hash": "def456",
        }

        cb = CircuitBreaker.from_dict(data)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.consecutive_no_progress == 4
        assert cb.consecutive_same_error == 2
        assert cb.last_progress_loop == 7
        assert cb.half_open_iterations == 3
        assert cb.last_error_hash == "def456"

    def test_from_dict_defaults(self):
        """from_dict uses defaults for missing keys."""
        data = {"state": "CLOSED"}
        cb = CircuitBreaker.from_dict(data)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_no_progress == 0
        assert cb.half_open_iterations == 0


class TestCircuitBreakerConfig:
    """Test configurable thresholds."""

    def test_custom_config(self):
        """Custom config is respected."""
        config = CircuitBreakerConfig(
            no_progress_threshold=10,
            same_error_threshold=8,
            half_open_threshold=4,
            half_open_patience=5,
        )
        cb = CircuitBreaker(config)
        assert cb.config.no_progress_threshold == 10
        assert cb.config.same_error_threshold == 8
        assert cb.config.half_open_threshold == 4
        assert cb.config.half_open_patience == 5

    def test_default_config(self):
        """Default config has sensible values."""
        config = CircuitBreakerConfig()
        assert config.no_progress_threshold == 5
        assert config.same_error_threshold == 5
        assert config.half_open_threshold == 2
        assert config.half_open_patience == 3
