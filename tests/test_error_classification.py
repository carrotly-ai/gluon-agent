"""Tests for _classify_api_error() in agent.py."""

from gluon.agent import (
    AuthenticationError,
    ContextOverflowError,
    ModelUnavailableError,
    RateLimitError,
    _classify_api_error,
)


class TestContextOverflowDetection:
    """Tests for context overflow error classification."""

    def test_400_input_too_long(self):
        error = Exception("400 input too long")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_400_input_long_separate_words(self):
        error = Exception("400 the input is very long")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_input_is_too_long_for_model(self):
        error = Exception("input is too long for model")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_context_window_exceeded(self):
        error = Exception("context window exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_token_limit_exceeded(self):
        error = Exception("token limit exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_preserves_error_message(self):
        msg = "400 input too long for this model context"
        result = _classify_api_error(Exception(msg))
        assert str(result) == msg


class TestRateLimitDetection:
    """Tests for rate limit error classification."""

    def test_429_too_many_requests(self):
        error = Exception("429 Too Many Requests")
        result = _classify_api_error(error)
        assert isinstance(result, RateLimitError)

    def test_rate_limit_exceeded(self):
        error = Exception("rate limit exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, RateLimitError)

    def test_request_throttled(self):
        error = Exception("request throttled")
        result = _classify_api_error(error)
        assert isinstance(result, RateLimitError)

    def test_throttling_variant(self):
        error = Exception("API throttling in effect")
        result = _classify_api_error(error)
        assert isinstance(result, RateLimitError)


class TestModelUnavailableDetection:
    """Tests for model unavailable error classification."""

    def test_model_not_found(self):
        error = Exception("model not found")
        result = _classify_api_error(error)
        assert isinstance(result, ModelUnavailableError)

    def test_model_not_available(self):
        error = Exception("model not available in this region")
        result = _classify_api_error(error)
        assert isinstance(result, ModelUnavailableError)

    def test_no_access_to_model(self):
        error = Exception("no access to model claude-opus-4.6")
        result = _classify_api_error(error)
        assert isinstance(result, ModelUnavailableError)


class TestAuthenticationErrorDetection:
    """Tests for authentication error classification."""

    def test_401_unauthorized(self):
        error = Exception("401 Unauthorized")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)

    def test_403_forbidden(self):
        error = Exception("403 Forbidden")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)

    def test_credentials_expired(self):
        error = Exception("credentials expired")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)

    def test_credentials_invalid(self):
        error = Exception("credentials invalid")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)

    def test_unauthorized_lowercase(self):
        error = Exception("unauthorized access")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)

    def test_forbidden_lowercase(self):
        error = Exception("forbidden resource")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)


class TestUnrecognisedErrors:
    """Tests for unrecognised error passthrough."""

    def test_generic_error_returns_original(self):
        error = Exception("something went wrong")
        result = _classify_api_error(error)
        assert result is error

    def test_runtime_error_returns_original(self):
        error = RuntimeError("internal failure")
        result = _classify_api_error(error)
        assert result is error
        assert isinstance(result, RuntimeError)

    def test_empty_error_message(self):
        error = Exception("")
        result = _classify_api_error(error)
        assert result is error

    def test_connection_error(self):
        error = Exception("Connection refused to server")
        result = _classify_api_error(error)
        assert result is error


class TestCaseInsensitivity:
    """Tests that classification is case-insensitive."""

    def test_mixed_case_rate_limit(self):
        error = Exception("Rate Limit Exceeded")
        result = _classify_api_error(error)
        assert isinstance(result, RateLimitError)

    def test_uppercase_context_overflow(self):
        error = Exception("400 INPUT TOO LONG")
        result = _classify_api_error(error)
        assert isinstance(result, ContextOverflowError)

    def test_mixed_case_model_not_found(self):
        error = Exception("Model Not Found in registry")
        result = _classify_api_error(error)
        assert isinstance(result, ModelUnavailableError)

    def test_mixed_case_unauthorized(self):
        error = Exception("Unauthorized request to API")
        result = _classify_api_error(error)
        assert isinstance(result, AuthenticationError)
