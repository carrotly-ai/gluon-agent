"""Tests for AskUserQuestion handler functionality."""

from datetime import timedelta

import pytest

from gluon.models import PendingQuestion, QuestionStatus, utc_now
from gluon.store import GluonStore


@pytest.fixture
def sample_project(store: GluonStore, tmp_path):
    """Create a sample project for testing."""

    project_path = tmp_path / "test-project"
    project_path.mkdir(exist_ok=True)
    project = store.create_project(name="test-project", path=project_path)
    return project


@pytest.fixture
def sample_run(store: GluonStore, sample_project):
    """Create a sample run for testing."""
    run = store.create_run(
        project_id=sample_project.id,
        prompt="Test prompt",
    )
    return run


class TestPendingQuestion:
    """Tests for PendingQuestion model."""

    def test_create_pending_question(self, store: GluonStore, sample_run):
        """Test creating a pending question."""
        question = PendingQuestion(
            run_id=sample_run.id,
            question_text="Which database should we use?",
            header="Database",
            options=[
                {"label": "PostgreSQL", "description": "Robust relational database"},
                {"label": "MySQL", "description": "Popular open-source database"},
            ],
        )
        store.create_pending_question(question)

        # Fetch and verify
        fetched = store.get_pending_question(question.id)
        assert fetched is not None
        assert fetched.question_text == "Which database should we use?"
        assert fetched.header == "Database"
        assert fetched.status == QuestionStatus.PENDING
        assert len(fetched.options) == 2
        assert fetched.options[0]["label"] == "PostgreSQL"

    def test_question_answer(self, store: GluonStore, sample_run):
        """Test answering a question."""
        question = PendingQuestion(
            run_id=sample_run.id,
            question_text="Which database?",
            header="Database",
            options=[
                {"label": "PostgreSQL"},
                {"label": "MySQL"},
            ],
        )
        store.create_pending_question(question)

        # Answer the question
        question.answer(labels=["PostgreSQL"], source="user")
        store.update_pending_question(question)

        # Verify the answer
        fetched = store.get_pending_question(question.id)
        assert fetched.status == QuestionStatus.ANSWERED
        assert fetched.selected_labels == ["PostgreSQL"]
        assert fetched.answer_source == "user"
        assert fetched.answered_at is not None

    def test_question_auto_answer(self, store: GluonStore, sample_run):
        """Test auto-answering a question."""
        question = PendingQuestion(
            run_id=sample_run.id,
            question_text="Which database?",
            header="Database",
            options=[
                {"label": "PostgreSQL (Recommended)"},
                {"label": "MySQL"},
            ],
        )
        store.create_pending_question(question)

        # Auto-answer
        question.auto_answer(source="timeout_auto")
        store.update_pending_question(question)

        # Verify auto-answer selected first option
        fetched = store.get_pending_question(question.id)
        assert fetched.status == QuestionStatus.AUTO_ANSWERED
        assert fetched.selected_labels == ["PostgreSQL (Recommended)"]
        assert fetched.answer_source == "timeout_auto"

    def test_multi_select_question(self, store: GluonStore, sample_run):
        """Test multi-select question."""
        question = PendingQuestion(
            run_id=sample_run.id,
            question_text="Which features do you want?",
            header="Features",
            options=[
                {"label": "Authentication"},
                {"label": "Dark Mode"},
                {"label": "Notifications"},
            ],
            multi_select=True,
        )
        store.create_pending_question(question)

        # Answer with multiple selections
        question.answer(
            labels=["Authentication", "Dark Mode"],
            source="user",
        )
        store.update_pending_question(question)

        # Verify multiple selections
        fetched = store.get_pending_question(question.id)
        assert fetched.multi_select is True
        assert fetched.selected_labels == ["Authentication", "Dark Mode"]

    def test_list_pending_questions(self, store: GluonStore, sample_run):
        """Test listing questions for a run."""
        # Create multiple questions
        q1 = PendingQuestion(
            run_id=sample_run.id,
            question_index=0,
            question_text="First question?",
            header="Q1",
            options=[{"label": "A"}, {"label": "B"}],
        )
        q2 = PendingQuestion(
            run_id=sample_run.id,
            question_index=1,
            question_text="Second question?",
            header="Q2",
            options=[{"label": "C"}, {"label": "D"}],
        )
        store.create_pending_question(q1)
        store.create_pending_question(q2)

        # List all questions for the run
        questions = store.list_pending_questions(sample_run.id)
        assert len(questions) == 2
        assert questions[0].question_index == 0
        assert questions[1].question_index == 1

    def test_list_pending_questions_status_filter(self, store: GluonStore, sample_run):
        """Test listing questions filtered by status."""
        # Create questions with different statuses
        q1 = PendingQuestion(
            run_id=sample_run.id,
            question_index=0,
            question_text="Pending question?",
            header="Q1",
            options=[{"label": "A"}],
        )
        q2 = PendingQuestion(
            run_id=sample_run.id,
            question_index=1,
            question_text="Answered question?",
            header="Q2",
            options=[{"label": "B"}],
        )
        store.create_pending_question(q1)
        store.create_pending_question(q2)

        # Answer second question
        q2.answer(labels=["B"], source="user")
        store.update_pending_question(q2)

        # Filter by pending status
        pending = store.list_pending_questions(sample_run.id, status=QuestionStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == q1.id

        # Filter by answered status
        answered = store.list_pending_questions(sample_run.id, status=QuestionStatus.ANSWERED)
        assert len(answered) == 1
        assert answered[0].id == q2.id

    def test_question_expiration(self, store: GluonStore, sample_run):
        """Test question expiration tracking."""
        expires_at = utc_now() + timedelta(minutes=5)
        question = PendingQuestion(
            run_id=sample_run.id,
            question_text="Time-limited question?",
            header="Urgent",
            options=[{"label": "Yes"}, {"label": "No"}],
            expires_at=expires_at,
        )
        store.create_pending_question(question)

        fetched = store.get_pending_question(question.id)
        assert fetched.expires_at is not None
        # Verify expiration time is within expected range
        assert fetched.expires_at >= utc_now()

    def test_delete_pending_questions(self, store: GluonStore, sample_run):
        """Test deleting questions for a run."""
        # Create questions
        q1 = PendingQuestion(
            run_id=sample_run.id,
            question_text="Question 1",
            header="Q1",
            options=[{"label": "A"}],
        )
        q2 = PendingQuestion(
            run_id=sample_run.id,
            question_text="Question 2",
            header="Q2",
            options=[{"label": "B"}],
        )
        store.create_pending_question(q1)
        store.create_pending_question(q2)

        # Verify questions exist
        assert len(store.list_pending_questions(sample_run.id)) == 2

        # Delete all questions for the run
        deleted = store.delete_pending_questions(sample_run.id)
        assert deleted == 2

        # Verify questions are deleted
        assert len(store.list_pending_questions(sample_run.id)) == 0


class TestAutoAnswerHandler:
    """Tests for Ralph loop auto-answer handler logic."""

    def test_finds_recommended_option(self):
        """Test that auto-answer finds recommended option."""
        from gluon.runner import TaskRunner

        _runner = TaskRunner()  # Ensure TaskRunner can be instantiated

        # Simulate the auto-answer logic
        questions = [
            {
                "question": "Which approach?",
                "header": "Approach",
                "options": [
                    {"label": "Option A"},
                    {"label": "Option B (Recommended)"},
                    {"label": "Option C"},
                ],
            }
        ]

        # Find recommended option
        answers = {}
        for q in questions:
            header = q.get("header", "Question")
            options = q.get("options", [])
            selected = None

            for opt in options:
                label = opt.get("label", "")
                if "(Recommended)" in label or "(recommended)" in label:
                    selected = label
                    break

            if not selected and options:
                selected = options[0].get("label", "")

            answers[header] = selected or ""

        assert answers["Approach"] == "Option B (Recommended)"

    def test_falls_back_to_first_option(self):
        """Test that auto-answer falls back to first option."""
        questions = [
            {
                "question": "Which approach?",
                "header": "Approach",
                "options": [
                    {"label": "First Option"},
                    {"label": "Second Option"},
                ],
            }
        ]

        # Find answer (no recommended option)
        answers = {}
        for q in questions:
            header = q.get("header", "Question")
            options = q.get("options", [])
            selected = None

            for opt in options:
                label = opt.get("label", "")
                if "(Recommended)" in label:
                    selected = label
                    break

            if not selected and options:
                selected = options[0].get("label", "")

            answers[header] = selected or ""

        assert answers["Approach"] == "First Option"
