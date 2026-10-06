"""校验领域事件信封与按事件类型定义的载荷。

与 ``contracts/domain.schema.json`` 对应，使用标准库实现，便于在没有
第三方依赖的环境中联调。校验返回错误信息列表；空列表表示通过。
"""

from __future__ import annotations

import re
from datetime import datetime

ENVELOPE_REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

EVENT_TYPES = {
    "OBSERVATION_REGISTERED",
    "IDENTIFICATION_REVIEWED",
    "ASSET_DERIVED",
    "USE_LICENSED",
    "USE_LICENSE_WITHDRAWN",
    "CREDIT_CORRECTED",
    "PUBLICATION_FROZEN",
    "USE_LOGGED",
}

AGGREGATE_TYPES = {
    "field_observation",
    "media_asset",
    "taxon_review",
    "exhibition_use",
}

SCOPES = {
    "public_exhibition",
    "catalog_print",
    "research_citation",
    "commercial",
    "educational",
    "web_promotion",
}

USE_PURPOSES = SCOPES | {"research_export"}

_ID_BASES = {"photographer", "curator", "expert"}
_CONFIDENCE = {"low", "medium", "high"}
_DERIVATIVE_KINDS = {"crop", "color_grade", "enlarge", "retouch", "other"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require(payload: dict, fields: tuple[str, ...], prefix: str) -> list[str]:
    return [f"{prefix}缺少字段：{name}" for name in fields if name not in payload]


def _check_datetime(value: object, field: str, prefix: str) -> list[str]:
    if not isinstance(value, str):
        return [f"{prefix}{field} 必须是 ISO 日期时间字符串"]
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return [f"{prefix}{field} 不是合法的日期时间：{value}"]
    return []


def _check_checksum(value: object, prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}checksum 必须是对象"]
    errors: list[str] = []
    if value.get("algorithm") != "sha256":
        errors.append(f"{prefix}checksum.algorithm 必须是 sha256")
    hex_value = value.get("value")
    if not isinstance(hex_value, str) or not _SHA256_RE.match(hex_value):
        errors.append(f"{prefix}checksum.value 必须是 64 位小写十六进制 sha256")
    return errors


def _check_location(value: object, prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}location 必须是对象"]
    errors: list[str] = []
    precision = value.get("precision")
    if precision not in {"blurred", "exact"}:
        errors.append(f"{prefix}location.precision 必须是 blurred 或 exact")
    if not isinstance(value.get("label"), str) or not value["label"]:
        errors.append(f"{prefix}location.label 不能为空")
    if precision == "exact":
        for coord in ("lat", "lon"):
            if not isinstance(value.get(coord), (int, float)):
                errors.append(f"{prefix}精确位置缺少 {coord}")
    if "lat" in value and isinstance(value.get("lat"), (int, float)) and not -90 <= value["lat"] <= 90:
        errors.append(f"{prefix}location.lat 超出 [-90, 90]")
    if "lon" in value and isinstance(value.get("lon"), (int, float)) and not -180 <= value["lon"] <= 180:
        errors.append(f"{prefix}location.lon 超出 [-180, 180]")
    return errors


def _validate_registered(payload: dict) -> list[str]:
    prefix = "payload(OBSERVATION_REGISTERED)："
    errors = _require(payload, ("observed_at", "photographer_id", "location", "conditions", "original_file"), prefix)
    if errors:
        return errors
    errors += _check_datetime(payload["observed_at"], "observed_at", prefix)
    if not isinstance(payload["photographer_id"], str) or not payload["photographer_id"]:
        errors.append(f"{prefix}photographer_id 不能为空")
    errors += _check_location(payload["location"], prefix)
    conditions = payload["conditions"]
    if not isinstance(conditions, dict) or not isinstance(conditions.get("habitat"), str) or not conditions.get("habitat"):
        errors.append(f"{prefix}conditions.habitat 不能为空")
    original = payload["original_file"]
    if not isinstance(original, dict):
        errors.append(f"{prefix}original_file 必须是对象")
    else:
        if not isinstance(original.get("filename"), str) or not original["filename"]:
            errors.append(f"{prefix}original_file.filename 不能为空")
        errors += _check_checksum(original.get("checksum"), prefix + "original_file.")
    capture = payload.get("capture")
    if capture is not None:
        if not isinstance(capture, dict):
            errors.append(f"{prefix}capture 必须是对象")
        elif "focal_length_mm" in capture and not (
            isinstance(capture["focal_length_mm"], (int, float)) and capture["focal_length_mm"] > 0
        ):
            errors.append(f"{prefix}capture.focal_length_mm 必须为正数")
        if isinstance(capture, dict) and "iso" in capture and (
            not isinstance(capture["iso"], int) or capture["iso"] < 1
        ):
            errors.append(f"{prefix}capture.iso 必须为正整数")
    ident = payload.get("initial_identification")
    if ident is not None:
        if not isinstance(ident, dict):
            errors.append(f"{prefix}initial_identification 必须是对象")
        else:
            if not ident.get("scientific_name"):
                errors.append(f"{prefix}initial_identification.scientific_name 不能为空")
            if ident.get("basis") not in _ID_BASES:
                errors.append(f"{prefix}initial_identification.basis 必须属于 {sorted(_ID_BASES)}")
            if "confidence" in ident and ident["confidence"] not in _CONFIDENCE:
                errors.append(f"{prefix}initial_identification.confidence 必须属于 {sorted(_CONFIDENCE)}")
    return errors


def _validate_derived(payload: dict) -> list[str]:
    prefix = "payload(ASSET_DERIVED)："
    errors = _require(
        payload,
        ("derivative_asset_id", "parent_asset_id", "kind", "transform", "producer", "checksum"),
        prefix,
    )
    if errors:
        return errors
    for field in ("derivative_asset_id", "parent_asset_id", "producer"):
        if not isinstance(payload[field], str) or not payload[field]:
            errors.append(f"{prefix}{field} 不能为空字符串")
    if payload.get("kind") not in _DERIVATIVE_KINDS:
        errors.append(f"{prefix}kind 必须属于 {sorted(_DERIVATIVE_KINDS)}")
    if not isinstance(payload.get("transform"), dict):
        errors.append(f"{prefix}transform 必须是可复现的参数对象")
    errors += _check_checksum(payload.get("checksum"), prefix)
    return errors


def _validate_reviewed(payload: dict) -> list[str]:
    prefix = "payload(IDENTIFICATION_REVIEWED)："
    fields = ("review_seq", "scientific_name", "common_name", "basis", "decided_by", "decided_at")
    errors = _require(payload, fields, prefix)
    if errors:
        return errors
    if not isinstance(payload["review_seq"], int) or payload["review_seq"] < 1:
        errors.append(f"{prefix}review_seq 必须是正整数")
    if not payload["scientific_name"]:
        errors.append(f"{prefix}scientific_name 不能为空")
    if payload["basis"] not in _ID_BASES:
        errors.append(f"{prefix}basis 必须属于 {sorted(_ID_BASES)}")
    if "confidence" in payload and payload["confidence"] not in _CONFIDENCE:
        errors.append(f"{prefix}confidence 必须属于 {sorted(_CONFIDENCE)}")
    errors += _check_datetime(payload["decided_at"], "decided_at", prefix)
    return errors


def _validate_licensed(payload: dict) -> list[str]:
    prefix = "payload(USE_LICENSED)："
    errors = _require(payload, ("license_id", "asset_id", "granted_by", "scopes", "granted_at"), prefix)
    if errors:
        return errors
    for field in ("license_id", "asset_id", "granted_by"):
        if not isinstance(payload[field], str) or not payload[field]:
            errors.append(f"{prefix}{field} 不能为空字符串")
    scopes = payload["scopes"]
    if not isinstance(scopes, list) or not scopes:
        errors.append(f"{prefix}scopes 必须是非空数组")
    else:
        bad = [s for s in scopes if s not in SCOPES]
        if bad:
            errors.append(f"{prefix}存在非法 scope：{bad}")
        if len(set(scopes)) != len(scopes):
            errors.append(f"{prefix}scopes 不能重复")
    errors += _check_datetime(payload["granted_at"], "granted_at", prefix)
    if "expires_at" in payload:
        errors += _check_datetime(payload["expires_at"], "expires_at", prefix)
    return errors


def _validate_withdrawn(payload: dict) -> list[str]:
    prefix = "payload(USE_LICENSE_WITHDRAWN)："
    errors = _require(payload, ("license_id", "withdrawn_at", "reason"), prefix)
    if errors:
        return errors
    errors += _check_datetime(payload["withdrawn_at"], "withdrawn_at", prefix)
    retained = payload.get("retained_scopes", ["research_citation"])
    if retained and any(s != "research_citation" for s in retained):
        errors.append(f"{prefix}依法保留的 scope 仅允许 research_citation")
    return errors


def _validate_credit(payload: dict) -> list[str]:
    prefix = "payload(CREDIT_CORRECTED)："
    errors = _require(payload, ("asset_id", "correct_photographer_id", "corrected_at", "reason"), prefix)
    if errors:
        return errors
    errors += _check_datetime(payload["corrected_at"], "corrected_at", prefix)
    if not payload["reason"]:
        errors.append(f"{prefix}reason 不能为空（更正须留痕）")
    return errors


def _validate_frozen(payload: dict) -> list[str]:
    prefix = "payload(PUBLICATION_FROZEN)："
    errors = _require(payload, ("publication_id", "asset_id", "frozen_at", "snapshot"), prefix)
    if errors:
        return errors
    errors += _check_datetime(payload["frozen_at"], "frozen_at", prefix)
    snapshot = payload["snapshot"]
    required_snapshot = ("scientific_name", "common_name", "credit", "asset_checksum")
    if not isinstance(snapshot, dict):
        errors.append(f"{prefix}snapshot 必须是对象")
    elif any(not snapshot.get(k) for k in required_snapshot):
        errors.append(f"{prefix}snapshot 必须包含 {required_snapshot}")
    return errors


def _validate_use_logged(payload: dict) -> list[str]:
    prefix = "payload(USE_LOGGED)："
    errors = _require(payload, ("use_id", "asset_id", "purpose", "used_at", "used_by"), prefix)
    if errors:
        return errors
    if payload["purpose"] not in USE_PURPOSES:
        errors.append(f"{prefix}purpose 必须属于 {sorted(USE_PURPOSES)}")
    errors += _check_datetime(payload["used_at"], "used_at", prefix)
    return errors


_PAYLOAD_VALIDATORS = {
    "OBSERVATION_REGISTERED": _validate_registered,
    "ASSET_DERIVED": _validate_derived,
    "IDENTIFICATION_REVIEWED": _validate_reviewed,
    "USE_LICENSED": _validate_licensed,
    "USE_LICENSE_WITHDRAWN": _validate_withdrawn,
    "CREDIT_CORRECTED": _validate_credit,
    "PUBLICATION_FROZEN": _validate_frozen,
    "USE_LOGGED": _validate_use_logged,
}


def validate_event(record: dict) -> list[str]:
    """校验完整领域事件；返回中文错误信息列表，空列表表示通过。"""
    errors = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if errors:
        return errors

    if record["event_type"] not in EVENT_TYPES:
        errors.append(f"event_type 非法：{record['event_type']}")
    if record["aggregate_type"] not in AGGREGATE_TYPES:
        errors.append(f"aggregate_type 非法：{record['aggregate_type']}")
    if not isinstance(record["aggregate_id"], str) or not record["aggregate_id"]:
        errors.append("aggregate_id 不能为空")
    if not isinstance(record["event_id"], str) or not record["event_id"]:
        errors.append("event_id 不能为空")
    if not isinstance(record["summary"], str) or not record["summary"]:
        errors.append("summary 不能为空")
    if not isinstance(record["version"], int) or record["version"] < 1:
        errors.append("version 必须是正整数")
    errors += _check_datetime(record["occurred_at"], "occurred_at", "")

    payload = record.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload 必须是对象")
        return errors
    validator = _PAYLOAD_VALIDATORS.get(record["event_type"])
    if validator is not None:
        errors += validator(payload)
    return errors


def validate_envelope(record: dict) -> list[str]:
    """仅校验信封字段，兼容历史轻量记录。"""
    errors = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    return errors
