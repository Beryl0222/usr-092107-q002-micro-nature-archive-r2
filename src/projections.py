"""从只追加事件流构建的读模型（投影）。

投影随时可以从事件流完整重建，因此不含独立事实；所有"当前状态"都是
对事件序列的折叠结果，历史版本（评审、署名、许可）全部保留。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .identity import master_asset_id


@dataclass
class Observation:
    observation_id: str
    registered_event_id: str
    observed_at: str
    photographer_id: str
    photographer_display_name: str
    location: dict
    sensitive_taxon: bool
    conditions: dict
    original_file: dict
    submission_id: str | None
    capture: dict | None = None
    # 随事件演进的部分：
    current_identification: dict | None = None
    review_history: list[dict] = field(default_factory=list)
    credit_photographer_id: str | None = None
    credit_display_name: str | None = None

    @property
    def credit(self) -> str:
        return self.credit_display_name or self.photographer_display_name or self.credit_photographer_id


@dataclass
class Asset:
    asset_id: str
    observation_id: str
    kind: str  # master | crop | color_grade | enlarge | retouch | other
    checksum: dict | None
    parent_asset_id: str | None
    transform: dict
    producer: str
    filename: str | None
    derived_event_id: str | None


@dataclass
class License:
    license_id: str
    asset_id: str
    observation_id: str
    granted_by: str
    scopes: set[str]
    granted_at: str
    expires_at: str | None
    terms: str
    withdrawn: bool = False
    withdrawn_at: str | None = None
    withdraw_reason: str | None = None
    retained_scopes: set[str] = field(default_factory=set)

    def allows(self, scope: str, *, at: str | None = None) -> bool:
        """许可在某用途、某时点是否有效。"""
        if self.withdrawn:
            return scope in self.retained_scopes
        if self.expires_at and at and at > self.expires_at:
            return False
        return scope in self.scopes


@dataclass
class Publication:
    publication_id: str
    asset_id: str
    observation_id: str
    title: str
    frozen_at: str
    snapshot: dict


@dataclass
class UseRecord:
    use_id: str
    asset_id: str
    observation_id: str
    purpose: str
    used_at: str
    used_by: str
    publication_id: str | None
    exhibit_id: str | None
    detail: str | None


class ArchiveProjection:
    """折叠整个事件流得到的档案当前状态与历史索引。"""

    def __init__(self) -> None:
        self.observations: dict[str, Observation] = {}
        self.observation_order: list[str] = []
        self.assets: dict[str, Asset] = {}
        # asset_id -> 所属观察
        self.asset_observation: dict[str, str] = {}
        # 观察 -> 其全部资产（含母版）
        self.assets_by_observation: dict[str, list[str]] = defaultdict(list)
        self.licenses: dict[str, License] = {}
        self.licenses_by_asset: dict[str, list[str]] = defaultdict(list)
        self.publications: dict[str, Publication] = {}
        self.publications_by_asset: dict[str, list[str]] = defaultdict(list)
        self.use_log: list[UseRecord] = []
        self.uses: dict[str, UseRecord] = {}
        self.use_log_by_asset: dict[str, list[str]] = defaultdict(list)

    # ---- 折叠 ----------------------------------------------------------

    def apply(self, event: dict) -> None:
        etype = event["event_type"]
        handler = getattr(self, f"_on_{etype.lower()}", None)
        if handler:
            handler(event)

    def rebuild(self, events: list[dict]) -> None:
        self.__init__()
        for event in events:
            self.apply(event)

    def _on_observation_registered(self, event: dict) -> None:
        p = event["payload"]
        obs_id = event["aggregate_id"]
        obs = Observation(
            observation_id=obs_id,
            registered_event_id=event["event_id"],
            observed_at=p["observed_at"],
            photographer_id=p["photographer_id"],
            photographer_display_name=p.get("photographer_display_name", p["photographer_id"]),
            location=p["location"],
            sensitive_taxon=p.get("sensitive_taxon", False),
            conditions=p["conditions"],
            original_file=p["original_file"],
            submission_id=p.get("submission_id"),
            capture=p.get("capture"),
            credit_photographer_id=p["photographer_id"],
            credit_display_name=p.get("photographer_display_name"),
        )
        ident = p.get("initial_identification")
        if ident:
            obs.current_identification = dict(ident, review_seq=0, decided_at=p["observed_at"],
                                              decided_by=p["photographer_id"])
        self.observations[obs_id] = obs
        self.observation_order.append(obs_id)
        # 母版资产作为派生链根节点
        master_id = master_asset_id(obs_id)
        self.assets[master_id] = Asset(
            asset_id=master_id,
            observation_id=obs_id,
            kind="master",
            checksum=p["original_file"]["checksum"],
            parent_asset_id=None,
            transform={},
            producer=p["photographer_id"],
            filename=p["original_file"]["filename"],
            derived_event_id=None,
        )
        self.asset_observation[master_id] = obs_id
        self.assets_by_observation[obs_id].append(master_id)

    def _on_identification_reviewed(self, event: dict) -> None:
        aggregate = event["aggregate_id"]
        obs_id = aggregate.split("taxon:", 1)[1] if aggregate.startswith("taxon:") else aggregate
        obs = self.observations[obs_id]
        p = event["payload"]
        decision = {k: p[k] for k in (
            "review_seq", "scientific_name", "common_name", "basis",
            "confidence", "decided_by", "decided_at", "note") if k in p}
        decision["event_id"] = event["event_id"]
        obs.review_history.append(decision)
        obs.current_identification = decision

    def _on_asset_derived(self, event: dict) -> None:
        p = event["payload"]
        obs_id = self.asset_observation[p["parent_asset_id"]]
        asset = Asset(
            asset_id=p["derivative_asset_id"],
            observation_id=obs_id,
            kind=p["kind"],
            checksum=p["checksum"],
            parent_asset_id=p["parent_asset_id"],
            transform=p["transform"],
            producer=p["producer"],
            filename=p.get("filename"),
            derived_event_id=event["event_id"],
        )
        self.assets[p["derivative_asset_id"]] = asset
        self.asset_observation[p["derivative_asset_id"]] = obs_id
        self.assets_by_observation[obs_id].append(p["derivative_asset_id"])

    def _on_use_licensed(self, event: dict) -> None:
        p = event["payload"]
        obs_id = self.asset_observation.get(p["asset_id"], "")
        lic = License(
            license_id=p["license_id"],
            asset_id=p["asset_id"],
            observation_id=obs_id,
            granted_by=p["granted_by"],
            scopes=set(p["scopes"]),
            granted_at=p["granted_at"],
            expires_at=p.get("expires_at"),
            terms=p.get("terms", ""),
        )
        self.licenses[p["license_id"]] = lic
        self.licenses_by_asset[p["asset_id"]].append(p["license_id"])

    def _on_use_license_withdrawn(self, event: dict) -> None:
        p = event["payload"]
        lic = self.licenses[p["license_id"]]
        lic.withdrawn = True
        lic.withdrawn_at = p["withdrawn_at"]
        lic.withdraw_reason = p["reason"]
        # 撤权不影响依法保存的科研引用
        lic.retained_scopes = set(p.get("retained_scopes", ["research_citation"]))

    def _on_credit_corrected(self, event: dict) -> None:
        p = event["payload"]
        obs_id = self.asset_observation[p["asset_id"]]
        obs = self.observations[obs_id]
        obs.credit_photographer_id = p["correct_photographer_id"]
        obs.credit_display_name = p.get("correct_display_name", p["correct_photographer_id"])

    def _on_publication_frozen(self, event: dict) -> None:
        p = event["payload"]
        obs_id = self.asset_observation.get(p["asset_id"], "")
        pub = Publication(
            publication_id=p["publication_id"],
            asset_id=p["asset_id"],
            observation_id=obs_id,
            title=p.get("title", ""),
            frozen_at=p["frozen_at"],
            snapshot=p["snapshot"],
        )
        self.publications[p["publication_id"]] = pub
        self.publications_by_asset[p["asset_id"]].append(p["publication_id"])

    def _on_use_logged(self, event: dict) -> None:
        p = event["payload"]
        rec = UseRecord(
            use_id=p["use_id"],
            asset_id=p["asset_id"],
            observation_id=self.asset_observation.get(p["asset_id"], ""),
            purpose=p["purpose"],
            used_at=p["used_at"],
            used_by=p["used_by"],
            publication_id=p.get("publication_id"),
            exhibit_id=p.get("exhibit_id"),
            detail=p.get("detail"),
        )
        self.use_log.append(rec)
        self.uses[rec.use_id] = rec
        self.use_log_by_asset[p["asset_id"]].append(p["use_id"])

    # ---- 查询 ----------------------------------------------------------

    def lineage(self, asset_id: str) -> list[Asset]:
        """从母版到指定资产的完整派生链（摄影参数、调色、放大均可追溯）。"""
        chain: list[Asset] = []
        current = self.assets.get(asset_id)
        seen: set[str] = set()
        while current is not None and current.asset_id not in seen:
            chain.append(current)
            seen.add(current.asset_id)
            if current.parent_asset_id is None:
                break
            current = self.assets.get(current.parent_asset_id)
        return list(reversed(chain))

    def descendants(self, asset_id: str) -> list[str]:
        """母版/资产的全部下游派生版本。"""
        result: list[str] = []
        stack = [asset_id]
        while stack:
            current = stack.pop()
            children = [a.asset_id for a in self.assets.values() if a.parent_asset_id == current]
            result.extend(children)
            stack.extend(children)
        return result

    def effective_licenses(self, asset_id: str, *, at: str | None = None) -> list[License]:
        result = []
        for lic_id in self.licenses_by_asset.get(asset_id, ()):
            lic = self.licenses[lic_id]
            if at is None or lic.granted_at <= at:
                result.append(lic)
        return result

    def has_scope(self, asset_id: str, scope: str, *, at: str | None = None) -> bool:
        return any(lic.allows(scope, at=at) for lic in self.effective_licenses(asset_id, at=at))

    def uses_for_observation(self, observation_id: str) -> list[UseRecord]:
        """摄影师追查作品使用去向：一次观察下母版与所有派生版本的使用记录。"""
        records: list[UseRecord] = []
        for asset_id in self.assets_by_observation.get(observation_id, ()):
            for use_id in self.use_log_by_asset.get(asset_id, ()):
                records.append(self.uses[use_id])
        return sorted(records, key=lambda u: u.used_at)
