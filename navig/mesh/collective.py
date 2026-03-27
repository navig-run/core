"""
MeshCollective — distributed task decomposition for Navig Mesh (MVP 2).

Gated by config flag: mesh.collective_enabled (default: False).

Architecture:
  - Leader receives a task and decomposes it into subtasks via TaskDecomposer.
  - Each subtask is dispatched to the best available standby via SubtaskDispatcher.
  - Standbys process their subtask locally and publish partial results via UDP.
  - LeaderAggregator collects partial results and reassembles the final response.
  - PartialResultBus is the in-process pub/sub between dispatcher and aggregator.

Graceful degradation:
  - If a standby goes offline mid-task, its subtask is re-queued to the next
    best available peer or processed locally.
  - If no peers are available, the entire task runs locally — collective mode
    is fully transparent to callers.

Use:
  ```python
  collective = MeshCollective(registry, discovery)
  await collective.start()
  result = await collective.run(task_text, context=...)
  await collective.stop()
  ```

Enable in config:
  ```yaml
  mesh:
    collective_enabled: true
  ```
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# How long to wait for a subtask response before timing out and re-queuing
SUBTASK_TIMEOUT_S = 30
# Maximum number of subtasks to dispatch in parallel
MAX_PARALLEL_SUBTASKS = 4


class PartialResultBus:
    """
    In-process pub/sub for partial results.

    Subscribers register a callback keyed by task_id.  When a partial result
    arrives (from UDP or HTTP), the callback is invoked with the result dict.
    """

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[dict], None]]] = {}

    def subscribe(self, task_id: str, cb: Callable[[dict], None]) -> None:
        self._subs.setdefault(task_id, []).append(cb)

    def unsubscribe(self, task_id: str) -> None:
        self._subs.pop(task_id, None)

    def publish(self, task_id: str, result: dict) -> None:
        for cb in self._subs.get(task_id, []):
            try:
                cb(result)
            except Exception as exc:
                logger.error("[collective] PartialResultBus callback error: %s", exc)


class TaskDecomposer:
    """
    Splits a task string into subtasks suitable for parallel execution.

    Current strategy (MVP): round-robin chunks if the task is long enough.
    Future: use the leader's LLM to produce a proper decomposition plan.
    """

    MIN_CHARS_FOR_SPLIT = 200
    MAX_CHUNKS = MAX_PARALLEL_SUBTASKS

    def decompose(self, task: str, peer_count: int) -> List[str]:
        """
        Return a list of subtask strings.

        If the task is short or only one peer is available, returns the
        original task (single-item list) — caller falls through to local.
        """
        if not task or peer_count < 2 or len(task) < self.MIN_CHARS_FOR_SPLIT:
            return [task]

        # Simple heuristic: split on sentence boundaries
        sentences = [s.strip() for s in task.replace("?", "?.").split(".") if s.strip()]
        n = min(self.MAX_CHUNKS, peer_count, len(sentences))
        if n < 2:
            return [task]

        chunk_size = max(1, len(sentences) // n)
        chunks = [
            ". ".join(sentences[i * chunk_size : (i + 1) * chunk_size])
            for i in range(n)
        ]
        # Append any remainder to the last chunk
        remainder = sentences[n * chunk_size :]
        if remainder and chunks:
            chunks[-1] += ". " + ". ".join(remainder)
        return chunks


class SubtaskDispatcher:
    """
    Dispatches a subtask to a specific peer via POST /mesh/subtask.
    Falls back to local execution on HTTP failure.
    """

    async def dispatch(
        self,
        subtask: str,
        task_id: str,
        subtask_id: str,
        target_url: str,
        context: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        POST subtask to peer gateway.  Returns the peer's response dict or
        None on failure (triggers local fallback in MeshCollective).
        """
        url = f"{target_url.rstrip('/')}/mesh/subtask"
        payload = {
            "task_id": task_id,
            "subtask_id": subtask_id,
            "text": subtask,
            "context": context or {},
        }
        try:
            import aiohttp as _aio

            async with _aio.ClientSession() as session:
                async with session.post(
                    url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=_aio.ClientTimeout(total=SUBTASK_TIMEOUT_S),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "[collective] Subtask dispatch failed (HTTP %d) to %s",
                            resp.status,
                            target_url,
                        )
                        return None
                    return await resp.json()

        except Exception as exc:
            logger.warning(
                "[collective] Subtask dispatch error to %s: %s", target_url, exc
            )
            return None


class LeaderAggregator:
    """
    Collects partial results for a task_id and assembles them on completion.

    Waits for ``expected_count`` partial results within the timeout window.
    On timeout, assembles whatever arrived.
    """

    def __init__(
        self,
        task_id: str,
        expected_count: int,
        timeout_s: float = SUBTASK_TIMEOUT_S,
    ) -> None:
        self._task_id = task_id
        self._expected = expected_count
        self._timeout_s = timeout_s
        self._results: List[dict] = []
        self._done = asyncio.Event()

    def on_partial(self, result: dict) -> None:
        self._results.append(result)
        if len(self._results) >= self._expected:
            self._done.set()

    async def wait(self) -> List[dict]:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "[collective] Aggregator timeout: got %d/%d for task %s",
                len(self._results),
                self._expected,
                self._task_id,
            )
        return list(self._results)

    @staticmethod
    def assemble(results: List[dict], original_task: str) -> str:
        """Concatenate partial results in arrival order."""
        if not results:
            return ""
        parts = [r.get("text") or r.get("output") or "" for r in results]
        return "\n\n".join(p for p in parts if p)


class MeshCollective:
    """
    Orchestrates distributed task execution across mesh peers.

    This class is the main entry point — callers use `run()`.

    Args:
        registry:  NodeRegistry providing peer list + leader status.
        discovery: MeshDiscovery for UDP broadcasts.
    """

    def __init__(self, registry: Any, discovery: Any) -> None:
        self._registry = registry
        self._discovery = discovery
        self._enabled = False  # set by start() from config
        self._bus = PartialResultBus()
        self._decomposer = TaskDecomposer()
        self._dispatcher = SubtaskDispatcher()

    async def start(self) -> None:
        """Enable collective mode if config flag is set."""
        try:
            from navig.config import get_config_manager

            cfg = get_config_manager()
            mesh_cfg = cfg.global_config.get("mesh", {})
            self._enabled = bool(mesh_cfg.get("collective_enabled", False))
        except Exception:
            self._enabled = False

        if self._enabled:
            logger.info("[collective] MeshCollective enabled")
        else:
            logger.debug(
                "[collective] MeshCollective disabled (mesh.collective_enabled=false)"
            )

    async def stop(self) -> None:
        self._enabled = False
        logger.debug("[collective] MeshCollective stopped")

    async def run(
        self,
        task: str,
        context: Optional[dict] = None,
        local_fn: Optional[Callable[[str], Any]] = None,
    ) -> str:
        """
        Run a task, distributing subtasks to peers if collective is enabled
        and peers are available.  Falls through to ``local_fn`` otherwise.

        Args:
            task:     The full task text (natural language or structured JSON).
            context:  Optional context dict passed to each subtask.
            local_fn: Coroutine factory for local execution fallback.
                      Signature: async def local_fn(text: str) -> str

        Returns:
            Assembled result string.
        """
        if not self._enabled:
            return await self._run_local(task, local_fn)

        if not self._registry.am_i_leader():
            # Only the leader distributes — standbys process directly
            return await self._run_local(task, local_fn)

        peers = [
            p
            for p in self._registry.list_peers()
            if not p.is_self and p.role != "yielding"
        ]
        if not peers:
            return await self._run_local(task, local_fn)

        subtasks = self._decomposer.decompose(task, len(peers))
        if len(subtasks) < 2:
            return await self._run_local(task, local_fn)

        task_id = str(uuid.uuid4())[:12]
        aggregator = LeaderAggregator(task_id, expected_count=len(subtasks))
        self._bus.subscribe(task_id, aggregator.on_partial)

        try:
            # Dispatch subtasks in parallel
            dispatch_coros = []
            for i, (subtask, peer) in enumerate(zip(subtasks, peers)):
                sid = f"{task_id}:{i}"
                dispatch_coros.append(
                    self._dispatch_one(subtask, task_id, sid, peer, context, local_fn)
                )

            partial_results = await asyncio.gather(
                *dispatch_coros, return_exceptions=True
            )

            # Feed into aggregator
            for res in partial_results:
                if isinstance(res, dict):
                    aggregator.on_partial(res)

            results = await aggregator.wait()
            return LeaderAggregator.assemble(results, task) or await self._run_local(
                task, local_fn
            )

        finally:
            self._bus.unsubscribe(task_id)

    async def _dispatch_one(
        self,
        subtask: str,
        task_id: str,
        subtask_id: str,
        peer: Any,
        context: Optional[dict],
        local_fn: Optional[Callable],
    ) -> dict:
        """Dispatch a single subtask; fall back to local on failure."""
        result = await self._dispatcher.dispatch(
            subtask, task_id, subtask_id, peer.gateway_url, context
        )
        if result:
            return result
        # Peer failed — run locally
        text = await self._run_local(subtask, local_fn)
        return {"text": text, "subtask_id": subtask_id, "source": "local_fallback"}

    @staticmethod
    async def _run_local(task: str, local_fn: Optional[Callable]) -> str:
        if local_fn is None:
            return ""
        try:
            result = local_fn(task)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result) if result else ""
        except Exception as exc:
            logger.error("[collective] Local fallback error: %s", exc)
            return ""

    def notify_partial(self, task_id: str, result: dict) -> None:
        """Called by the subtask HTTP endpoint when a partial result arrives."""
        self._bus.publish(task_id, result)
