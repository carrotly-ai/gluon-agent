"""Tests for PendingQuestion methods."""


from gluon.models import PendingQuestion, QuestionStatus


def _make_question(**kwargs) -> PendingQuestion:
    """Helper to create a PendingQuestion with defaults."""
    defaults = {
        "run_id": "run-1",
        "question_text": "Which database?",
        "header": "Database",
        "options": [
            {"label": "PostgreSQL", "description": "Relational DB"},
            {"label": "MongoDB", "description": "Document DB"},
        ],
    }
    defaults.update(kwargs)
    return PendingQuestion(**defaults)


class TestIsPending:
    def test_true_for_pending(self):
        q = _make_question(status=QuestionStatus.PENDING)
        assert q.is_pending is True

    def test_false_for_answered(self):
        q = _make_question(status=QuestionStatus.ANSWERED)
        assert q.is_pending is False

    def test_false_for_auto_answered(self):
        q = _make_question(status=QuestionStatus.AUTO_ANSWERED)
        assert q.is_pending is False

    def test_false_for_expired(self):
        q = _make_question(status=QuestionStatus.EXPIRED)
        assert q.is_pending is False


class TestAnswerString:
    def test_empty_when_no_labels(self):
        q = _make_question(selected_labels=[])
        assert q.answer_string == ""

    def test_single_label(self):
        q = _make_question(selected_labels=["PostgreSQL"])
        assert q.answer_string == "PostgreSQL"

    def test_multiple_labels_comma_separated(self):
        q = _make_question(selected_labels=["PostgreSQL", "MongoDB"])
        assert q.answer_string == "PostgreSQL, MongoDB"


class TestGetRecommendedOption:
    def test_finds_recommended(self):
        q = _make_question(
            options=[
                {"label": "Option A", "description": "desc"},
                {"label": "Option B (Recommended)", "description": "desc"},
            ]
        )
        assert q.get_recommended_option() == "Option B (Recommended)"

    def test_case_insensitive_recommended(self):
        q = _make_question(
            options=[
                {"label": "Option A (recommended)", "description": "desc"},
            ]
        )
        assert q.get_recommended_option() == "Option A (recommended)"

    def test_none_when_no_recommendation(self):
        q = _make_question(
            options=[
                {"label": "Option A", "description": "desc"},
                {"label": "Option B", "description": "desc"},
            ]
        )
        assert q.get_recommended_option() is None

    def test_empty_options_returns_none(self):
        q = _make_question(options=[])
        assert q.get_recommended_option() is None


class TestAutoAnswer:
    def test_selects_recommended(self):
        q = _make_question(
            options=[
                {"label": "Option A", "description": "desc"},
                {"label": "Option B (Recommended)", "description": "desc"},
            ]
        )
        q.auto_answer()
        assert q.selected_labels == ["Option B (Recommended)"]
        assert q.status == QuestionStatus.AUTO_ANSWERED
        assert q.answered_at is not None

    def test_falls_back_to_first_when_no_recommendation(self):
        q = _make_question(
            options=[
                {"label": "Alpha", "description": "desc"},
                {"label": "Beta", "description": "desc"},
            ]
        )
        q.auto_answer()
        assert q.selected_labels == ["Alpha"]

    def test_empty_options_sets_auto_answered(self):
        q = _make_question(options=[])
        q.auto_answer()
        assert q.status == QuestionStatus.AUTO_ANSWERED
        assert q.selected_labels == []

    def test_custom_source(self):
        q = _make_question()
        q.auto_answer(source="ralph")
        assert q.answer_source == "ralph"

    def test_default_source(self):
        q = _make_question()
        q.auto_answer()
        assert q.answer_source == "auto_recommended"


class TestAnswer:
    def test_sets_selected_labels(self):
        q = _make_question()
        q.answer(["PostgreSQL"], source="user")
        assert q.selected_labels == ["PostgreSQL"]

    def test_sets_status_answered(self):
        q = _make_question()
        q.answer(["PostgreSQL"])
        assert q.status == QuestionStatus.ANSWERED

    def test_sets_answered_at(self):
        q = _make_question()
        q.answer(["PostgreSQL"])
        assert q.answered_at is not None

    def test_default_source_is_user(self):
        q = _make_question()
        q.answer(["PostgreSQL"])
        assert q.answer_source == "user"

    def test_multiple_labels(self):
        q = _make_question(multi_select=True)
        q.answer(["PostgreSQL", "MongoDB"], source="user")
        assert q.selected_labels == ["PostgreSQL", "MongoDB"]
        assert q.answer_string == "PostgreSQL, MongoDB"
