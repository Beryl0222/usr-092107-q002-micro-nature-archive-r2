"""访问策略：位置保护、资质校验与脱敏。

规则：

* 精确栖息地位置只向具备资质的研究者（``qualified_researcher``）开放；
  其他调用方（公众展项、图录、网页）只能看到模糊位置；
* 濒危/易受干扰物种（``sensitive_taxon``）即使对象本身有精确坐标，
  未获专项授权时也只返回模糊位置；
* 研究导出统一经 :func:`mask_for_export` 脱敏，保证同一事件流 +
  同一导出参数的输出可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 调用方角色
ROLE_PUBLIC = "public"
ROLE_CURATOR = "curator"
ROLE_PHOTOGRAPHER = "photographer"
ROLE_RESEARCHER = "qualified_researcher"
ROLE_ARCHIVIST = "archivist"

# 敏感物种精确位置的专项授权
ENTITLEMENT_SENSITIVE_LOCATION = "sensitive_location"


@dataclass(frozen=True)
class Caller:
    caller_id: str
    role: str = ROLE_PUBLIC
    entitlements: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def qualified_researcher(cls, caller_id: str, *, sensitive: bool = False) -> "Caller":
        entitlements = frozenset({ENTITLEMENT_SENSITIVE_LOCATION}) if sensitive else frozenset()
        return cls(caller_id, ROLE_RESEARCHER, entitlements)


def can_view_exact_location(caller: Caller, *, sensitive_taxon: bool) -> bool:
    """精确位置开放判定。"""
    if caller.role != ROLE_RESEARCHER:
        return False
    if sensitive_taxon and ENTITLEMENT_SENSITIVE_LOCATION not in caller.entitlements:
        return False
    return True


def mask_location(location: dict) -> dict:
    """抹掉精确坐标，保留模糊区域标签与半径。"""
    masked = {"precision": "blurred", "label": location.get("label", "")}
    if location.get("radius_m") is not None:
        masked["radius_m"] = location["radius_m"]
    return masked


def location_for(caller: Caller, location: dict, *, sensitive_taxon: bool) -> dict:
    """按调用方资质返回位置视图。"""
    if can_view_exact_location(caller, sensitive_taxon=sensitive_taxon):
        return dict(location)
    # 非研究者或无专项授权：即使存储的是精确点，也降级为模糊位置。
    if location.get("precision") == "blurred":
        return dict(location)
    label = location.get("label") or _blur_coordinates(location)
    return {"precision": "blurred", "label": label}


def _blur_coordinates(location: dict) -> str:
    lat, lon = location.get("lat"), location.get("lon")
    if lat is None or lon is None:
        return "位置已脱敏"
    return f"约 {round(lat, 1)}°N, {round(lon, 1)}°E 区域（坐标已模糊）"


def mask_for_export(caller: Caller, observation, *, include_photographer: bool = False) -> dict:
    """研究导出脱敏视图。

    默认去除摄影师身份信息；位置按资质降级。母版校验值保留，
    以便复核文件真实性。
    """
    view = {
        "observation_id": observation.observation_id,
        "observed_at": observation.observed_at,
        "location": location_for(caller, observation.location,
                                 sensitive_taxon=observation.sensitive_taxon),
        "sensitive_taxon": observation.sensitive_taxon,
        "conditions": observation.conditions,
        "identification": observation.current_identification,
        "original_file": {
            "filename": observation.original_file["filename"],
            "checksum": observation.original_file["checksum"],
        },
    }
    if include_photographer and caller.role in {ROLE_RESEARCHER, ROLE_ARCHIVIST, ROLE_CURATOR}:
        view["photographer_id"] = observation.photographer_id
    return view
