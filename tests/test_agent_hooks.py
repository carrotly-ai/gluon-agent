"""Tests for SubagentTracker lifecycle from agent_hooks.py."""

import pytest

from gluon.agent_hooks import SubagentTracker


@pytest.fixture
def tracker() -> SubagentTracker:
    return SubagentTracker()


class TestSubagentTrackerInit:
    def test_initial_count_zero(self, tracker: SubagentTracker):
        assert tracker.active_count == 0

    def test_initial_all_done_is_set(self, tracker: SubagentTracker):
        assert tracker.all_done.is_set()


class TestSubagentTrackerIncrement:
    @pytest.mark.asyncio
    async def test_increments_count(self, tracker: SubagentTracker):
        await tracker.increment()
        assert tracker.active_count == 1

    @pytest.mark.asyncio
    async def test_clears_all_done(self, tracker: SubagentTracker):
        await tracker.increment()
        assert not tracker.all_done.is_set()

    @pytest.mark.asyncio
    async def test_multiple_increments(self, tracker: SubagentTracker):
        await tracker.increment()
        await tracker.increment()
        await tracker.increment()
        assert tracker.active_count == 3


class TestSubagentTrackerDecrement:
    @pytest.mark.asyncio
    async def test_decrements_count(self, tracker: SubagentTracker):
        await tracker.increment()
        await tracker.increment()
        await tracker.decrement()
        assert tracker.active_count == 1

    @pytest.mark.asyncio
    async def test_sets_all_done_at_zero(self, tracker: SubagentTracker):
        await tracker.increment()
        await tracker.decrement()
        assert tracker.active_count == 0
        assert tracker.all_done.is_set()

    @pytest.mark.asyncio
    async def test_clamps_at_zero(self, tracker: SubagentTracker):
        await tracker.decrement()
        assert tracker.active_count == 0
        assert tracker.all_done.is_set()

    @pytest.mark.asyncio
    async def test_double_decrement_from_zero(self, tracker: SubagentTracker):
        await tracker.decrement()
        await tracker.decrement()
        assert tracker.active_count == 0


class TestSubagentTrackerReset:
    @pytest.mark.asyncio
    async def test_resets_to_zero(self, tracker: SubagentTracker):
        await tracker.increment()
        await tracker.increment()
        await tracker.reset()
        assert tracker.active_count == 0

    @pytest.mark.asyncio
    async def test_sets_all_done(self, tracker: SubagentTracker):
        await tracker.increment()
        await tracker.reset()
        assert tracker.all_done.is_set()

    @pytest.mark.asyncio
    async def test_idempotent(self, tracker: SubagentTracker):
        await tracker.reset()
        await tracker.reset()
        assert tracker.active_count == 0
        assert tracker.all_done.is_set()


class TestSubagentTrackerRoundTrip:
    @pytest.mark.asyncio
    async def test_increment_then_decrement(self, tracker: SubagentTracker):
        await tracker.increment()
        assert not tracker.all_done.is_set()
        await tracker.decrement()
        assert tracker.all_done.is_set()
        assert tracker.active_count == 0

    @pytest.mark.asyncio
    async def test_multiple_increments_then_decrements(self, tracker: SubagentTracker):
        for _ in range(5):
            await tracker.increment()
        assert tracker.active_count == 5
        assert not tracker.all_done.is_set()

        for _ in range(5):
            await tracker.decrement()
        assert tracker.active_count == 0
        assert tracker.all_done.is_set()
