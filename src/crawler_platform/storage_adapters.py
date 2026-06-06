from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .storage import FileStore


@runtime_checkable
class StorageBackend(Protocol):
    """Storage facade used by core services.

    This protocol reflects the v1 runtime calls used across crawler services and is
    intentionally conservative to support a gradual migration to alternate storage
    backends.
    """

    def save_spider(self, spider: Any) -> Any: ...
    def load_spider(self, spider_id: str) -> Any: ...
    def list_spiders(self) -> list[dict[str, Any]]: ...

    def save_task(self, task: Any) -> Path: ...
    def load_task(self, task_id: str) -> Any: ...
    def list_tasks(self) -> list[dict[str, Any]]: ...
    def update_task_status(self, task_id: str, status: Any, **updates: Any) -> Any: ...
    def append_record(self, task_id: str, record: dict[str, Any]) -> None: ...
    def read_records(self, task_id: str, *, strict: bool = True) -> list[dict[str, Any]]: ...

    def save_schedule(self, schedule: Any) -> Path: ...
    def get_schedule(self, schedule_id: str) -> Any: ...
    def list_schedules(self, enabled: bool | None = None, spider_id: str | None = None) -> list[dict[str, Any]]: ...
    def list_jobs(self, status: str | None = None, source: str | None = None, spider_id: str | None = None) -> list[dict[str, Any]]: ...
    def enqueue_job(self, job: Any) -> Any: ...
    def claim_job(self, worker_id: str, now: str | None = None, *, lease_seconds: int = 300) -> Any | None: ...
    def heartbeat_job(self, job_id: str, worker_id: str, now: str | None = None, *, lease_seconds: int = 300) -> Any: ...
    def complete_job(self, job_id: str, worker_id: str, result: dict[str, Any] | None = None) -> Any: ...
    def fail_job(self, job_id: str, worker_id: str, error: dict[str, Any] | str, retry: bool = True) -> Any: ...
    def cancel_job(self, job_id: str) -> Any: ...
    def requeue_expired_leases(self, now: str | None = None) -> list[dict[str, Any]]: ...
    def get_queue_stats(self) -> dict[str, Any]: ...

    def append_log(self, scope: str, target_id: str, event: dict[str, Any]) -> Path: ...
    def record_metric(self, scope: str, target_id: str, metric: dict[str, Any]) -> Path: ...
    def create_run_report(self, target_type: str, target_id: str, report: dict[str, Any]) -> Path: ...

    def get_worker_state(self, worker_id: str) -> Any: ...
    def save_worker_state(self, worker_state: Any) -> Any: ...

    def check_storage(self) -> dict[str, Any]: ...


@runtime_checkable
class QueueBackend(Protocol):
    """Queue abstraction focused on worker job dispatching."""

    def enqueue(self, topic: str, item: dict[str, Any], dedupe_key: str | None = None) -> dict[str, Any]: ...

    def claim(
        self,
        worker_id: str,
        topics: list[str] | None = None,
        batch_size: int = 1,
    ) -> list[dict[str, Any]]: ...

    def nack(self, job_id: str, reason: str, retry_at: str | None = None) -> None: ...
    def ack(self, job_id: str) -> None: ...
    def heartbeat(self, job_id: str, worker_id: str) -> None: ...
    def metrics(self) -> dict[str, Any]: ...


class FileStoreBackend:
    """Compatibility wrapper that exposes v1 FileStore as the production adapter target."""

    def __init__(self, root: str | Path = "data", *, lock_timeout_seconds: float = 5.0) -> None:
        self._store = FileStore(root=root, lock_timeout_seconds=lock_timeout_seconds)

    @property
    def root(self) -> Path:
        return self._store.root

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _InProcessQueueItem:
    __slots__ = ("job_id", "topic", "item", "worker_id", "dedupe_key")

    def __init__(self, job_id: str, topic: str, item: dict[str, Any], dedupe_key: str | None = None) -> None:
        self.job_id = job_id
        self.topic = topic
        self.item = item
        self.worker_id = None
        self.dedupe_key = dedupe_key


class InProcessQueue:
    """Simple in-memory queue backend for local tests and development."""

    def __init__(self) -> None:
        self._items: deque[_InProcessQueueItem] = deque()
        self._seen: set[str] = set()
        self._acked = set[str]()

    def _next_id(self, item: dict[str, Any]) -> str:
        base = str(item.get("id") or item.get("job_id") or item.get("task_id") or "")
        return base or str(len(self._items) + len(self._acked) + 1)

    def enqueue(self, topic: str, item: dict[str, Any], dedupe_key: str | None = None) -> dict[str, Any]:
        payload = dict(item)
        if topic:
            payload.setdefault("topic", topic)
        payload.setdefault("id", self._next_id(payload))
        if dedupe_key:
            if dedupe_key in self._seen:
                return payload
            self._seen.add(dedupe_key)
        node = _InProcessQueueItem(job_id=payload["id"], topic=topic, item=payload, dedupe_key=dedupe_key)
        self._items.append(node)
        return payload

    def claim(self, worker_id: str, topics: list[str] | None = None, batch_size: int = 1) -> list[dict[str, Any]]:
        batch_size = max(1, int(batch_size))
        claimed: list[dict[str, Any]] = []
        for node in self._items:
            if node.worker_id is not None:
                continue
            if topics is not None and node.topic not in topics:
                continue
            node.worker_id = worker_id
            claimed.append({"topic": node.topic, "id": node.job_id, **node.item, "worker_id": worker_id})
            if len(claimed) >= batch_size:
                break
        return claimed

    def nack(self, job_id: str, reason: str, retry_at: str | None = None) -> None:
        item = _find_queued_item(self._items, job_id)
        if item is None:
            return
        item.worker_id = None
        item.item["nack_reason"] = reason
        item.item["retry_at"] = retry_at

    def ack(self, job_id: str) -> None:
        if (pending := _find_queued_item(self._items, job_id)) is not None:
            self._items.remove(pending)
        self._acked.add(job_id)
        # allow duplicate ack calls; no-op after ack
        return None

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        item = _find_queued_item(self._items, job_id)
        if item is None:
            return
        if item.worker_id == worker_id:
            item.item["heartbeat_by"] = worker_id

    def metrics(self) -> dict[str, Any]:
        return {"queued": len(self._items), "acked": len(self._acked), "in_flight": len([item for item in self._items if item.worker_id is not None])}


class RedisQueue(InProcessQueue):
    """Production placeholder queue implementation (wire-format compatible with InProcessQueue)."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("RedisQueue is planned for production rollout and is currently a skeleton.")


def _find_queued_item(items: Iterable[_InProcessQueueItem], job_id: str) -> _InProcessQueueItem | None:
    for item in items:
        if item.job_id == job_id:
            return item
    return None


def build_storage_backend(
    profile: str = "local",
    *,
    data_dir: str | Path = "data",
    lock_timeout_seconds: float = 5.0,
) -> StorageBackend:
    normalized = (profile or "local").lower()
    if normalized in {"local", "file", "filestore"}:
        return FileStoreBackend(root=data_dir, lock_timeout_seconds=lock_timeout_seconds)
    raise ValueError(
        "Unsupported storage profile for this v1 rollout. "
        "Use 'local' to keep existing FileStore behavior."
    )


def build_queue_backend(profile: str = "inprocess", *, storage: StorageBackend | None = None) -> QueueBackend:
    normalized = (profile or "inprocess").lower()
    if normalized in {"inprocess", "local", "file"}:
        return InProcessQueue()
    if normalized in {"redis", "redisqueue"}:
        return RedisQueue()
    raise ValueError("Unsupported queue profile. Supported: inprocess, local, redis.")
