"""脱敏且可复现的研究导出。

同一份事件流、同一调用方资质、同一导出参数必然产出字节一致的文件：

* 序列化使用固定键序与分隔符；
* 清单记录事件流指纹（事件 JSONL 的 sha256）、事件条数与契约版本；
* 精确位置经 :mod:`src.policy` 按资质脱敏；摄影师身份默认去除。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .policy import Caller, mask_for_export
from .projections import ArchiveProjection
from .store import EventStore

EXPORT_SCHEMA_VERSION = "research-export/1.0"


def canonical_dumps(value: object) -> str:
    """确定性 JSON 文本：键排序、无多余空白、保留非 ASCII 字符。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_stream_fingerprint(events: list[dict]) -> dict:
    """事件流指纹：逐事件规范序列化后流式哈希。"""
    digest = hashlib.sha256()
    for event in events:
        digest.update(canonical_dumps(event).encode("utf-8"))
        digest.update(b"\n")
    return {"algorithm": "sha256", "value": digest.hexdigest(), "event_count": len(events)}


def build_research_export(store: EventStore, *, caller: Caller,
                          observation_ids: list[str] | None = None,
                          include_photographer: bool = False,
                          include_lineage: bool = True) -> dict:
    """构建研究导出包（内存对象）。

    导出内容是投影视图，不直接暴露原始事件；位置与身份字段在此时脱敏。
    """
    projection = ArchiveProjection()
    projection.rebuild(store.all_events)

    selected = observation_ids or list(projection.observation_order)
    unknown = [oid for oid in selected if oid not in projection.observations]
    if unknown:
        raise ValueError(f"导出包含不存在的观察：{unknown}")

    records = []
    for oid in selected:
        obs = projection.observations[oid]
        view = mask_for_export(caller, obs, include_photographer=include_photographer)
        if include_lineage:
            view["derivatives"] = [
                {
                    "asset_id": aid,
                    "kind": projection.assets[aid].kind,
                    "parent_asset_id": projection.assets[aid].parent_asset_id,
                    "transform": projection.assets[aid].transform,
                    "producer": projection.assets[aid].producer,
                    "checksum": (
                        projection.assets[aid].checksum["value"]
                        if projection.assets[aid].checksum else None
                    ),
                }
                # 第一个是母版，已经在 original_file 中给出
                for aid in projection.assets_by_observation[oid][1:]
            ]
        records.append(view)

    payload = {
        "schema": EXPORT_SCHEMA_VERSION,
        "caller": {"caller_id": caller.caller_id, "role": caller.role},
        "sensitive_location_access": "sensitive_location" in caller.entitlements,
        "include_photographer": include_photographer,
        "records": records,
    }
    stream_fp = event_stream_fingerprint(store.all_events)
    manifest = {
        "schema": EXPORT_SCHEMA_VERSION,
        "event_stream": stream_fp,
        "record_count": len(records),
        "observation_ids": selected,
    }
    # 导出包指纹：对不含指纹字段的规范内容哈希，保证可复现。
    export_fp = hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()
    manifest["export_fingerprint"] = {"algorithm": "sha256", "value": export_fp}
    payload["manifest"] = manifest
    return payload


def write_research_export(payload: dict, out_dir: str | Path) -> dict:
    """将导出包写入目录，返回文件路径与指纹。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    export_path = out / "research_export.json"
    export_path.write_text(canonical_dumps(payload) + "\n", encoding="utf-8")
    manifest_path = out / "manifest.json"
    manifest_path.write_text(canonical_dumps(payload["manifest"]) + "\n", encoding="utf-8")
    return {
        "export_file": str(export_path),
        "manifest_file": str(manifest_path),
        "export_fingerprint": payload["manifest"]["export_fingerprint"]["value"],
        "stream_fingerprint": payload["manifest"]["event_stream"]["value"],
    }


def verify_export_file(path: str | Path) -> dict:
    """复算导出文件指纹，用于复核"同一份导出可被原样重建"。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = payload.pop("manifest")
    actual = hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()
    return {
        "ok": actual == expected["export_fingerprint"]["value"],
        "recomputed": actual,
        "recorded": expected["export_fingerprint"]["value"],
        "event_stream": expected["event_stream"],
    }
