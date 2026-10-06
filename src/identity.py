"""领域身份约定。

仓库已有约定：每条记录携带全局唯一 ``event_id``、所属 ``aggregate_id``
与聚合内严格递增的 ``version``。本模块固定各类聚合与派生资产的 ID 形态，
让观察、原始文件、派生版本、鉴定评审、许可与出版记录之间的关系可机读。
"""

from __future__ import annotations

import re

_OBS_RE = re.compile(r"^obs-lingnan-\d{4}-\d{4}$")
_SEQ_RE = re.compile(r"^[0-9a-zA-Z][0-9a-zA-Z._-]{2,}$")


def observation_id(year: int, seq: int) -> str:
    """野外观察编号，例如 obs-lingnan-2026-0007。"""
    return f"obs-lingnan-{year:04d}-{seq:04d}"


def is_observation_id(value: str) -> bool:
    return bool(_OBS_RE.match(value))


def master_asset_id(observation_id_value: str) -> str:
    """原始文件（母版）虚拟资产编号，不对应派生事件，而是派生链的根。"""
    return f"master:{observation_id_value}"


def derivative_asset_id(observation_id_value: str, kind: str, seq: int) -> str:
    """派生版本编号，例如 deriv-obs-lingnan-2026-0007-crop-0001。"""
    return f"deriv-{observation_id_value}-{kind}-{seq:04d}"


def taxon_review_id(observation_id_value: str) -> str:
    """一次观察的鉴定演进流（多次评审共享同一聚合）。"""
    return f"taxon:{observation_id_value}"


def license_id(observation_id_value: str, seq: int) -> str:
    return f"lic-{observation_id_value}-{seq:03d}"


def publication_id(catalog_code: str, seq: int) -> str:
    return f"pub-{catalog_code}-{seq:03d}"


def use_record_id(seq: int) -> str:
    return f"use-{seq:06d}"


def event_id(seq: int) -> str:
    """仓库内事件编号；event_id 也可由外部投稿系统提供，但必须全局唯一。"""
    return f"evt-{seq:08d}"


def is_slug(value: str) -> bool:
    return bool(_SEQ_RE.match(value))
