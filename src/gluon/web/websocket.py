"""WebSocket connection manager for real-time updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

if TYPE_CHECKING:
    from gluon.models import ExecutionRun

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self) -> None:
        """Initialize the WebSocket manager."""
        self.connections: set[WebSocket] = set()
        self.log_subscriptions: dict[str, set[WebSocket]] = {}  # run_id → clients
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.connections.add(websocket)
        logger.debug(f"WebSocket connected. Total connections: {len(self.connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            self.connections.discard(websocket)
            # Remove from all log subscriptions
            for run_id, subscribers in list(self.log_subscriptions.items()):
                subscribers.discard(websocket)
                if not subscribers:
                    del self.log_subscriptions[run_id]
        logger.debug(f"WebSocket disconnected. Total connections: {len(self.connections)}")

    async def subscribe_logs(self, websocket: WebSocket, run_id: str) -> None:
        """Subscribe a client to log updates for a specific run."""
        async with self._lock:
            if run_id not in self.log_subscriptions:
                self.log_subscriptions[run_id] = set()
            self.log_subscriptions[run_id].add(websocket)
        logger.debug(f"Client subscribed to logs for run {run_id[:8]}")

    async def unsubscribe_logs(self, websocket: WebSocket, run_id: str) -> None:
        """Unsubscribe a client from log updates."""
        async with self._lock:
            if run_id in self.log_subscriptions:
                self.log_subscriptions[run_id].discard(websocket)
                if not self.log_subscriptions[run_id]:
                    del self.log_subscriptions[run_id]
        logger.debug(f"Client unsubscribed from logs for run {run_id[:8]}")

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all connected clients."""
        if not self.connections:
            return

        disconnected: set[WebSocket] = set()
        async with self._lock:
            connections = list(self.connections)

        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)

        # Clean up disconnected clients
        if disconnected:
            async with self._lock:
                self.connections -= disconnected

    async def broadcast_run_update(self, run: ExecutionRun, project_name: str) -> None:
        """Notify all clients of a run status change."""
        message = {
            "type": "run_updated",
            "run": {
                "id": run.id,
                "project_id": run.project_id,
                "project_name": project_name,
                "status": run.status.value,
                "prompt": run.prompt,
                "initiator": run.initiator,
                "created_at": run.created_at.isoformat(),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "duration_seconds": run.duration_seconds,
                "error_message": run.error_message,
                # Cost tracking
                "cost_usd": run.cost_usd,
                # Git/PR fields for Kanban routing
                "use_worktree": run.use_worktree,
                "branch_name": run.branch_name,
                "pr_number": run.pr_number,
                "pr_url": run.pr_url,
                "pr_status": run.pr_status,
                "pr_mergeable": run.pr_mergeable,
                # Archive status
                "archived": run.archived,
                # Ralph Loop fields
                "ralph_enabled": run.ralph_enabled,
                "loop_count": run.loop_count,
                "max_loops": run.max_loops,
                "circuit_state": run.circuit_state,
                "completion_confidence": run.completion_confidence,
                "completion_reason": run.completion_reason,
                # Chain/formula fields
                "chain_id": run.chain_id,
                "chain_step_name": run.metadata.get("step_name") if run.metadata else None,
            },
        }
        await self.broadcast(message)

    async def broadcast_run_created(self, run: ExecutionRun, project_name: str) -> None:
        """Notify all clients of a new run."""
        message = {
            "type": "run_created",
            "run": {
                "id": run.id,
                "project_id": run.project_id,
                "project_name": project_name,
                "status": run.status.value,
                "prompt": run.prompt,
                "initiator": run.initiator,
                "created_at": run.created_at.isoformat(),
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "error_message": None,
                # Cost tracking
                "cost_usd": None,
                # Git/PR fields for Kanban routing
                "use_worktree": run.use_worktree,
                "branch_name": run.branch_name,
                "pr_number": None,
                "pr_url": None,
                "pr_status": None,
                "pr_mergeable": None,
                # Archive status
                "archived": False,
                # Ralph Loop fields
                "ralph_enabled": run.ralph_enabled,
                "loop_count": run.loop_count or 0,
                "max_loops": run.max_loops,
                "circuit_state": run.circuit_state or "CLOSED",
                "completion_confidence": run.completion_confidence or 0,
                "completion_reason": run.completion_reason,
                # Chain/formula fields
                "chain_id": run.chain_id,
                "chain_step_name": run.metadata.get("step_name") if run.metadata else None,
            },
        }
        await self.broadcast(message)

    async def broadcast_step_progress(
        self,
        run_id: str,
        step_name: str,
        step_index: int,
        total_steps: int,
        step_status: str,
    ) -> None:
        """Broadcast chain step progress to all clients.

        Args:
            run_id: The run ID
            step_name: Current step name (e.g., "Plan", "Implement")
            step_index: Current step index (0-based)
            total_steps: Total steps in chain
            step_status: Step status: "running", "completed", "failed"
        """
        message = {
            "type": "step_progress",
            "run_id": run_id,
            "step_name": step_name,
            "step_index": step_index,
            "total_steps": total_steps,
            "step_status": step_status,
        }
        await self.broadcast(message)

    async def broadcast_loop_progress(
        self,
        run_id: str,
        loop_count: int,
        max_loops: int,
        circuit_state: str,
        completion_confidence: float,
        cost_usd: float,
        files_changed: int = 0,
        has_errors: bool = False,
    ) -> None:
        """Broadcast ralph loop progress to all clients.

        Args:
            run_id: The run ID
            loop_count: Current iteration number
            max_loops: Maximum iterations allowed
            circuit_state: Current circuit breaker state (CLOSED/HALF_OPEN/OPEN)
            completion_confidence: Confidence that task is complete (0-100)
            cost_usd: Total cost so far
            files_changed: Number of files changed in this iteration
            has_errors: Whether this iteration had errors
        """
        message = {
            "type": "loop_progress",
            "run_id": run_id,
            "loop_count": loop_count,
            "max_loops": max_loops,
            "circuit_state": circuit_state,
            "completion_confidence": completion_confidence,
            "cost_usd": cost_usd,
            "files_changed": files_changed,
            "has_errors": has_errors,
        }
        await self.broadcast(message)

    async def _send_to_subscribers(self, run_id: str, message: dict[str, Any]) -> None:
        """Send a message to all clients subscribed to a specific run."""
        async with self._lock:
            subscribers = self.log_subscriptions.get(run_id, set()).copy()

        if not subscribers:
            return

        disconnected: set[WebSocket] = set()
        for websocket in subscribers:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)

        # Clean up disconnected clients
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    self.connections.discard(ws)
                    if run_id in self.log_subscriptions:
                        self.log_subscriptions[run_id].discard(ws)

    async def stream_log_line(self, run_id: str, stream: str, line: str) -> None:
        """Send a log line to subscribed clients."""
        message = {
            "type": "log_line",
            "run_id": run_id,
            "stream": stream,
            "line": line,
        }
        await self._send_to_subscribers(run_id, message)

    async def stream_agent_message(self, run_id: str, msg: dict[str, Any]) -> None:
        """Stream an agent message to subscribed clients.

        Args:
            run_id: The run ID to stream to
            msg: Parsed message from messages.jsonl with type, content, metadata, timestamp
        """
        message = {
            "type": "agent_message",
            "run_id": run_id,
            "message": msg,
        }
        await self._send_to_subscribers(run_id, message)

    async def stream_progress(self, run_id: str, turns: int, tool_calls: int, elapsed_seconds: float) -> None:
        """Stream progress update to subscribed clients.

        Args:
            run_id: The run ID to stream to
            turns: Number of conversation turns
            tool_calls: Number of tool calls made
            elapsed_seconds: Time elapsed since task start
        """
        message = {
            "type": "progress",
            "run_id": run_id,
            "turns": turns,
            "tool_calls": tool_calls,
            "elapsed_seconds": elapsed_seconds,
        }
        await self._send_to_subscribers(run_id, message)

    async def stream_token_update(
        self,
        run_id: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        context_used: int | None = None,
        context_window: int | None = None,
        cache_read: int = 0,
        cache_create: int = 0,
        model: str | None = None,
    ) -> None:
        """Stream token/cost update to subscribed clients.

        Args:
            run_id: The run ID to stream to
            input_tokens: Total input tokens used
            output_tokens: Total output tokens used
            estimated_cost_usd: Estimated cost in USD
            context_used: Live context occupancy (latest turn's full input)
            context_window: Max context window size (from model_usage)
            cache_read: Prompt-cache read tokens
            cache_create: Prompt-cache create tokens
            model: Model identifier string
        """
        message: dict[str, Any] = {
            "type": "token_update",
            "run_id": run_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "cache_read": cache_read,
            "cache_create": cache_create,
        }
        if context_used is not None:
            message["context_used"] = context_used
        if context_window is not None:
            message["context_window"] = context_window
        if model is not None:
            message["model"] = model
        await self._send_to_subscribers(run_id, message)

    async def broadcast_pending_questions(
        self,
        run_id: str,
        questions: list[dict[str, Any]],
        question_ids: list[str],
    ) -> None:
        """Broadcast new pending questions to all clients.

        Args:
            run_id: The run ID the questions belong to
            questions: List of question dicts from AskUserQuestion tool
            question_ids: List of PendingQuestion IDs (parallel to questions)
        """
        message = {
            "type": "pending_questions",
            "run_id": run_id,
            "questions": [
                {
                    "id": qid,
                    "question": q.get("question", ""),
                    "header": q.get("header", "Question"),
                    "options": q.get("options", []),
                    "multi_select": q.get("multiSelect", False),
                }
                for qid, q in zip(question_ids, questions)
            ],
        }
        # Broadcast to all clients (not just subscribers) so any open dashboard sees it
        await self.broadcast(message)
        # Also send to run subscribers
        await self._send_to_subscribers(run_id, message)

    async def broadcast_question_answered(self, run_id: str, question_id: str) -> None:
        """Broadcast that a question was answered.

        Args:
            run_id: The run ID the question belongs to
            question_id: The PendingQuestion ID that was answered
        """
        message = {
            "type": "question_answered",
            "run_id": run_id,
            "question_id": question_id,
        }
        await self.broadcast(message)
        await self._send_to_subscribers(run_id, message)

    async def broadcast_todos_updated(
        self,
        run_id: str,
        todos: list[dict[str, Any]],
        todo_count: int,
        completed_count: int,
        in_progress_count: int,
        pending_count: int,
    ) -> None:
        """Broadcast updated todo state to all clients and run subscribers.

        Args:
            run_id: The run ID the todos belong to
            todos: Raw todo items from TodoWrite
            todo_count: Total number of todos
            completed_count: Number of completed todos
            in_progress_count: Number of in-progress todos
            pending_count: Number of pending todos
        """
        message = {
            "type": "todos_updated",
            "run_id": run_id,
            "todos": todos,
            "todo_count": todo_count,
            "completed_count": completed_count,
            "in_progress_count": in_progress_count,
            "pending_count": pending_count,
        }
        await self.broadcast(message)
        await self._send_to_subscribers(run_id, message)

    async def broadcast_activity_event(
        self,
        event_id: str,
        actor: str,
        action: str,
        result: str | None = None,
        message: str | None = None,
    ) -> None:
        """Broadcast a new activity event to all clients."""
        msg = {
            "type": "activity_event",
            "event": {
                "id": event_id,
                "actor": actor,
                "action": action,
                "result": result,
                "message": message,
            },
        }
        await self.broadcast(msg)

    async def broadcast_queue_updated(
        self,
        item_id: str,
        status: str,
        project_id: str,
    ) -> None:
        """Broadcast a work queue item status change."""
        msg = {
            "type": "queue_updated",
            "item": {
                "id": item_id,
                "status": status,
                "project_id": project_id,
            },
        }
        await self.broadcast(msg)

    async def broadcast_merge_updated(
        self,
        entry_id: str,
        status: str,
        branch_name: str,
    ) -> None:
        """Broadcast a merge queue entry status change."""
        msg = {
            "type": "merge_updated",
            "entry": {
                "id": entry_id,
                "status": status,
                "branch_name": branch_name,
            },
        }
        await self.broadcast(msg)

    async def broadcast_witness_decision(
        self,
        run_id: str,
        classification: str,
        confidence: float,
        action: str,
    ) -> None:
        """Broadcast a new witness health classification."""
        msg = {
            "type": "witness_decision",
            "run_id": run_id,
            "classification": classification,
            "confidence": confidence,
            "action": action,
        }
        await self.broadcast(msg)

    async def handle_client_message(self, websocket: WebSocket, data: str) -> None:
        """Handle incoming WebSocket message from client."""
        try:
            message = json.loads(data)
            msg_type = message.get("type")

            if msg_type == "subscribe_logs":
                run_id = message.get("run_id")
                if run_id:
                    await self.subscribe_logs(websocket, run_id)
                    await websocket.send_json({"type": "subscribed", "run_id": run_id})

            elif msg_type == "unsubscribe_logs":
                run_id = message.get("run_id")
                if run_id:
                    await self.unsubscribe_logs(websocket, run_id)
                    await websocket.send_json({"type": "unsubscribed", "run_id": run_id})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {data[:100]}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")


# Global instance
ws_manager = WebSocketManager()
