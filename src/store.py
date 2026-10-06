"""只追加（append-only）事件存储。

内存索引加 JSONL 持久化：

* ``event_id`` 全局唯一，重复提交返回既有事件，不产生第二条记录；
* 同一 ``aggregate_id`` 的 ``version`` 从 1 起严格递增；
* 投稿去重键 ``submission_id`` 唯一，重复投稿指向同一次观察；
* 事件一经写入即不可变，任何更正都必须是新事件。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .errors import (
    DuplicateEventError,
    ValidationError,
    VersionConflictError,
)
from .validator import validate_event


class EventStore:
    def __init__(self, path: str | Path | None = None):
        self._events: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._by_aggregate: dict[str, list[dict]] = defaultdict(list)
        self._versions: dict[str, int] = {}
        self._submissions: dict[str, str] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self._load()

    # ---- 读侧 ----------------------------------------------------------

    @property
    def all_events(self) -> list[dict]:
        """按接收顺序的全部事件（顺序即事实发生的登记顺序）。"""
        return list(self._events)

    def events_for(self, aggregate_id: str) -> list[dict]:
        return list(self._by_aggregate.get(aggregate_id, ()))

    def get(self, event_id_value: str) -> dict | None:
        return self._by_id.get(event_id_value)

    def observation_for_submission(self, submission_id: str) -> str | None:
        return self._submissions.get(submission_id)

    def next_event_seq(self) -> int:
        return len(self._events) + 1

    # ---- 写侧 ----------------------------------------------------------

    def append(self, event: dict, *, expect_new: bool = True) -> dict:
        """校验并追加事件。

        若 ``event_id`` 已存在：载荷完全一致时返回既有事件（幂等重放）；
        否则抛出 :class:`DuplicateEventError`。
        """
        existing = self._by_id.get(event.get("event_id"))
        if existing is not None:
            if existing == event:
                return existing
            raise DuplicateEventError(f"event_id 已存在且载荷不一致：{event['event_id']}")

        errors = validate_event(event)
        if errors:
            raise ValidationError(errors)

        if event["event_type"] == "OBSERVATION_REGISTERED":
            submission_id = event["payload"].get("submission_id")
            if submission_id and submission_id in self._submissions:
                # 重复投稿不形成第二次观察：直接返回既有观察的登记事件。
                return self.events_for(self._submissions[submission_id])[0]

        aggregate_id = event["aggregate_id"]
        expected_version = self._versions.get(aggregate_id, 0) + 1
        if event["version"] != expected_version:
            raise VersionConflictError(
                f"聚合 {aggregate_id} 下一版本应为 {expected_version}，收到 {event['version']}"
            )

        self._commit(event)
        if event["event_type"] == "OBSERVATION_REGISTERED":
            submission_id = event["payload"].get("submission_id")
            if submission_id:
                self._submissions[submission_id] = aggregate_id
        return event

    def _commit(self, event: dict) -> None:
        self._events.append(event)
        self._by_id[event["event_id"]] = event
        self._by_aggregate[event["aggregate_id"]].append(event)
        self._versions[event["aggregate_id"]] = event["version"]
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            # 重建索引；加载时信任此前已通过校验并落盘的事件。
            self._events.append(event)
            self._by_id[event["event_id"]] = event
            self._by_aggregate[event["aggregate_id"]].append(event)
            self._versions[event["aggregate_id"]] = event["version"]
            if event["event_type"] == "OBSERVATION_REGISTERED":
                submission_id = event["payload"].get("submission_id")
                if submission_id:
                    self._submissions.setdefault(submission_id, event["aggregate_id"])
