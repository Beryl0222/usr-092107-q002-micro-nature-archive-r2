"""影像档案应用服务。

所有写操作都构造并追加不可变事件；时间戳由调用方显式给出，
保证同一场景可重复构建、测试与演示。读操作返回投影数据或
按调用方脱敏后的视图。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import identity
from .errors import (
    ArchiveError,
    LicenseStateError,
    PublicationStateError,
    UnknownAggregateError,
)
from .policy import (
    ROLE_RESEARCHER,
    Caller,
    location_for,
    mask_for_export,
)
from .projections import ArchiveProjection
from .store import EventStore

# 用途 → 许可 scope 的映射
PURPOSE_SCOPE = {
    "public_exhibition": "public_exhibition",
    "catalog_print": "catalog_print",
    "commercial": "commercial",
    "educational": "educational",
    "web_promotion": "web_promotion",
    "research_citation": "research_citation",
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArchiveService:
    def __init__(self, store: EventStore):
        self.store = store
        self.projection = ArchiveProjection()
        self.projection.rebuild(store.all_events)

    # ---- 内部 ----------------------------------------------------------

    def _append(self, *, event_type: str, aggregate_type: str, aggregate_id: str,
                occurred_at: str, summary: str, payload: dict) -> dict:
        event = {
            "event_id": identity.event_id(self.store.next_event_seq()),
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": occurred_at,
            "version": len(self.store.events_for(aggregate_id)) + 1,
            "summary": summary,
            "payload": payload,
        }
        committed = self.store.append(event)
        self.projection.apply(committed)
        return committed

    # ---- 写作命令 ------------------------------------------------------

    def register_observation(self, *, obs_id: str, at: str, observed_at: str,
                             photographer_id: str, location: dict, conditions: dict,
                             original_file: dict, display_name: str | None = None,
                             initial_identification: dict | None = None,
                             sensitive_taxon: bool = False,
                             submission_id: str | None = None,
                             capture: dict | None = None) -> dict:
        """登记一次野外观察。

        同一 ``submission_id`` 重复提交时返回既有登记事件，
        不产生第二次观察（幂等由存储层保证）。
        """
        if submission_id:
            existing = self.store.observation_for_submission(submission_id)
            if existing:
                return self.store.events_for(existing)[0]
        payload = {
            "observed_at": observed_at,
            "photographer_id": photographer_id,
            "location": location,
            "conditions": conditions,
            "original_file": original_file,
            "sensitive_taxon": sensitive_taxon,
        }
        if display_name:
            payload["photographer_display_name"] = display_name
        if initial_identification:
            payload["initial_identification"] = initial_identification
        if submission_id:
            payload["submission_id"] = submission_id
        if capture:
            payload["capture"] = capture
        return self._append(
            event_type="OBSERVATION_REGISTERED",
            aggregate_type="field_observation",
            aggregate_id=obs_id,
            occurred_at=at,
            summary=f"登记野外观察 {obs_id}",
            payload=payload,
        )

    def derive_asset(self, *, obs_id: str, kind: str, parent_asset_id: str,
                     transform: dict, checksum: dict, producer: str, at: str,
                     filename: str | None = None, seq: int | None = None) -> dict:
        """记录裁切、调色、放大等派生版本；父资产必须已存在。"""
        if obs_id not in self.projection.observations:
            raise UnknownAggregateError(f"观察不存在：{obs_id}")
        if parent_asset_id not in self.projection.assets:
            raise UnknownAggregateError(f"父资产不存在：{parent_asset_id}")
        if seq is None:
            existing = [a for a in self.projection.assets_by_observation[obs_id]
                        if self.projection.assets[a].kind == kind]
            seq = len(existing) + 1
        asset_id = identity.derivative_asset_id(obs_id, kind, seq)
        payload = {
            "derivative_asset_id": asset_id,
            "parent_asset_id": parent_asset_id,
            "kind": kind,
            "transform": transform,
            "producer": producer,
            "checksum": checksum,
        }
        if filename:
            payload["filename"] = filename
        return self._append(
            event_type="ASSET_DERIVED",
            aggregate_type="media_asset",
            aggregate_id=f"media:{obs_id}",
            occurred_at=at,
            summary=f"记录 {obs_id} 的{kind}派生版本 {asset_id}",
            payload=payload,
        )

    def review_identification(self, *, obs_id: str, scientific_name: str,
                              common_name: str, basis: str, decided_by: str,
                              decided_at: str, confidence: str = "medium",
                              note: str | None = None,
                              taxonomy_source: str | None = None) -> dict:
        """追加一次专家/策展鉴定；名称历史全部保留。"""
        if obs_id not in self.projection.observations:
            raise UnknownAggregateError(f"观察不存在：{obs_id}")
        history = self.projection.observations[obs_id].review_history
        seq = len(history) + 1
        payload = {
            "review_seq": seq,
            "scientific_name": scientific_name,
            "common_name": common_name,
            "basis": basis,
            "confidence": confidence,
            "decided_by": decided_by,
            "decided_at": decided_at,
        }
        if history:
            payload["supersedes_review_seq"] = history[-1]["review_seq"]
        if note:
            payload["note"] = note
        if taxonomy_source:
            payload["taxonomy_source"] = taxonomy_source
        return self._append(
            event_type="IDENTIFICATION_REVIEWED",
            aggregate_type="taxon_review",
            aggregate_id=identity.taxon_review_id(obs_id),
            occurred_at=decided_at,
            summary=f"{obs_id} 鉴定更新为 {scientific_name}（{common_name}）",
            payload=payload,
        )

    def grant_license(self, *, obs_id: str, asset_id: str, granted_by: str,
                      scopes: list[str], granted_at: str,
                      expires_at: str | None = None, terms: str = "",
                      seq: int | None = None) -> dict:
        if asset_id not in self.projection.assets:
            raise UnknownAggregateError(f"资产不存在：{asset_id}")
        if seq is None:
            seq = len(self.projection.licenses_by_asset.get(asset_id, ())) + 1
        lic_id = identity.license_id(obs_id, seq)
        payload = {
            "license_id": lic_id,
            "asset_id": asset_id,
            "granted_by": granted_by,
            "scopes": scopes,
            "granted_at": granted_at,
            "terms": terms,
        }
        if expires_at:
            payload["expires_at"] = expires_at
        return self._append(
            event_type="USE_LICENSED",
            aggregate_type="exhibition_use",
            aggregate_id=f"usage:{asset_id}",
            occurred_at=granted_at,
            summary=f"{asset_id} 授予用途 {scopes}",
            payload=payload,
        )

    def withdraw_license(self, *, license_id: str, withdrawn_at: str, reason: str,
                         retained_scopes: list[str] | None = None) -> dict:
        """撤回展示/商业许可；科研引用依法保留，不因撤权而删除。"""
        lic = self.projection.licenses.get(license_id)
        if lic is None:
            raise LicenseStateError(f"许可不存在：{license_id}")
        if lic.withdrawn:
            raise LicenseStateError(f"许可已撤回：{license_id}")
        payload = {
            "license_id": license_id,
            "withdrawn_at": withdrawn_at,
            "reason": reason,
            "retained_scopes": retained_scopes or ["research_citation"],
        }
        return self._append(
            event_type="USE_LICENSE_WITHDRAWN",
            aggregate_type="exhibition_use",
            aggregate_id=f"usage:{lic.asset_id}",
            occurred_at=withdrawn_at,
            summary=f"{license_id} 撤回商业/展示许可，保留科研引用",
            payload=payload,
        )

    def correct_credit(self, *, obs_id: str, asset_id: str,
                       correct_photographer_id: str, corrected_at: str, reason: str,
                       correct_display_name: str | None = None) -> dict:
        """更正错误署名；更正记录留痕，使用去向仍可追查。"""
        if asset_id not in self.projection.assets:
            raise UnknownAggregateError(f"资产不存在：{asset_id}")
        payload = {
            "asset_id": asset_id,
            "correct_photographer_id": correct_photographer_id,
            "corrected_at": corrected_at,
            "reason": reason,
        }
        if correct_display_name:
            payload["correct_display_name"] = correct_display_name
        return self._append(
            event_type="CREDIT_CORRECTED",
            aggregate_type="media_asset",
            aggregate_id=f"media:{obs_id}",
            occurred_at=corrected_at,
            summary=f"{asset_id} 署名更正为 {correct_display_name or correct_photographer_id}",
            payload=payload,
        )

    def freeze_publication(self, *, publication_id: str, asset_id: str,
                           frozen_at: str, title: str = "") -> dict:
        """出版时冻结当时依据（名称、署名、校验值、许可）。

        日后公众名称更新，已出版图录仍显示该快照。
        """
        if asset_id not in self.projection.assets:
            raise UnknownAggregateError(f"资产不存在：{asset_id}")
        if publication_id in self.projection.publications:
            raise PublicationStateError(f"出版冻结已存在，不可改写：{publication_id}")
        obs_id = self.projection.asset_observation[asset_id]
        obs = self.projection.observations[obs_id]
        ident = obs.current_identification or {}
        asset = self.projection.assets[asset_id]
        scopes = sorted(
            scope for lic in self.projection.effective_licenses(asset_id, at=frozen_at)
            if not lic.withdrawn
            for scope in lic.scopes
        )
        payload = {
            "publication_id": publication_id,
            "asset_id": asset_id,
            "title": title,
            "frozen_at": frozen_at,
            "snapshot": {
                "scientific_name": ident.get("scientific_name", ""),
                "common_name": ident.get("common_name", ""),
                "credit": obs.credit,
                "asset_checksum": asset.checksum["value"] if asset.checksum else "",
                "license_scopes": scopes,
            },
        }
        return self._append(
            event_type="PUBLICATION_FROZEN",
            aggregate_type="exhibition_use",
            aggregate_id=f"pub:{publication_id}",
            occurred_at=frozen_at,
            summary=f"图录 {publication_id} 冻结 {asset_id} 的出版依据",
            payload=payload,
        )

    # ---- 使用授权与留痕 ------------------------------------------------

    def authorize_use(self, *, asset_id: str, purpose: str, at: str) -> None:
        """使用前的许可闸门；不满足则抛错，不写使用记录。"""
        if asset_id not in self.projection.assets:
            raise UnknownAggregateError(f"资产不存在：{asset_id}")
        scope = PURPOSE_SCOPE[purpose]
        licenses = self.projection.effective_licenses(asset_id, at=at)
        if not licenses:
            raise LicenseStateError(f"{asset_id} 没有任何许可记录，禁止{purpose}")
        if not any(lic.allows(scope, at=at) for lic in licenses):
            raise LicenseStateError(f"{asset_id} 在 {at} 无有效 {scope} 许可（可能已撤回或过期）")

    def log_use(self, *, asset_id: str, purpose: str, used_at: str, used_by: str,
                detail: str | None = None, publication_id: str | None = None,
                exhibit_id: str | None = None) -> dict:
        """登记一次实际使用，供摄影师追查去向。展示/商业用途须先过许可闸门。"""
        if purpose != "research_export":
            self.authorize_use(asset_id=asset_id, purpose=purpose, at=used_at)
        seq = len(self.projection.use_log) + 1
        payload = {
            "use_id": identity.use_record_id(seq),
            "asset_id": asset_id,
            "purpose": purpose,
            "used_at": used_at,
            "used_by": used_by,
        }
        if detail:
            payload["detail"] = detail
        if publication_id:
            payload["publication_id"] = publication_id
        if exhibit_id:
            payload["exhibit_id"] = exhibit_id
        event = self._append(
            event_type="USE_LOGGED",
            aggregate_type="exhibition_use",
            aggregate_id=f"usage:{asset_id}",
            occurred_at=used_at,
            summary=f"{asset_id} 用于 {purpose}（{used_by}）",
            payload=payload,
        )
        return event

    # ---- 读模型：策展说明 ----------------------------------------------

    def exhibit_label(self, *, asset_id: str, caller: Caller, at: str,
                      exhibit_id: str | None = None) -> dict:
        """为任一展项生成"来源与许可相符"的说明；不合规时显式标注。"""
        asset = self.projection.assets.get(asset_id)
        if asset is None:
            raise UnknownAggregateError(f"资产不存在：{asset_id}")
        obs = self.projection.observations[asset.observation_id]
        ident = obs.current_identification or {}
        lineage = []
        for step in self.projection.lineage(asset_id):
            lineage.append({
                "asset_id": step.asset_id,
                "kind": step.kind,
                "filename": step.filename,
                "transform": step.transform,
                "producer": step.producer,
                "checksum": step.checksum["value"] if step.checksum else None,
            })
        license_rows = []
        compliant = True
        blockers: list[str] = []
        for lic in self.projection.effective_licenses(asset_id, at=at):
            row = {
                "license_id": lic.license_id,
                "granted_by": lic.granted_by,
                "scopes": sorted(lic.scopes),
                "status": "withdrawn" if lic.withdrawn else "active",
                "retained_scopes": sorted(lic.retained_scopes),
            }
            license_rows.append(row)
        if not self.projection.has_scope(asset_id, "public_exhibition", at=at):
            compliant = False
            blockers.append("缺少有效的 public_exhibition 许可（或许可已撤回/过期）")
        return {
            "exhibit_id": exhibit_id,
            "generated_for": at,
            "compliant": compliant,
            "blockers": blockers,
            "title_subject": {
                "scientific_name": ident.get("scientific_name"),
                "common_name": ident.get("common_name"),
                "identification_basis": ident.get("basis"),
                "review_seq": ident.get("review_seq"),
            },
            "credit": obs.credit,
            "provenance": {
                "observation_id": obs.observation_id,
                "observed_at": obs.observed_at,
                "location": location_for(caller, obs.location, sensitive_taxon=obs.sensitive_taxon),
                "conditions": obs.conditions,
                "capture": obs.capture,
                "master_file": {
                    "filename": obs.original_file["filename"],
                    "checksum": obs.original_file["checksum"]["value"],
                },
                "lineage": lineage,
            },
            "licenses": license_rows,
        }

    def catalog_page(self, *, publication_id: str) -> dict:
        """已出版图录页：永远显示冻结时的名称与署名，不随后续改名变化。"""
        pub = self.projection.publications.get(publication_id)
        if pub is None:
            raise PublicationStateError(f"图录不存在：{publication_id}")
        snap = pub.snapshot
        return {
            "publication_id": publication_id,
            "title": pub.title,
            "frozen_at": pub.frozen_at,
            "as_published": {
                "scientific_name": snap["scientific_name"],
                "common_name": snap["common_name"],
                "credit": snap["credit"],
                "license_scopes": snap.get("license_scopes", []),
            },
            "asset_id": pub.asset_id,
            "asset_checksum": snap["asset_checksum"],
            "note": "名称与署名以出版冻结时为准；后续鉴定变更见档案当前记录。",
        }

    # ---- 读模型：摄影师追查 --------------------------------------------

    def photographer_trace(self, photographer_id: str) -> dict:
        """作品使用去向 + 署名更正历史。

        当前作品以更正后的署名为准；曾错误署名为该摄影师、后被更正移出的
        作品单列在 ``works_corrected_away``，使更正对双方都留痕可查。
        """
        current_ids = [
            oid for oid in self.projection.observation_order
            if self.projection.observations[oid].credit_photographer_id == photographer_id
        ]
        corrected_away_ids = [
            oid for oid in self.projection.observation_order
            if (self.projection.observations[oid].photographer_id == photographer_id
                and self.projection.observations[oid].credit_photographer_id != photographer_id)
        ]

        def works_for(obs_ids: list[str]) -> list[dict]:
            works = []
            for oid in obs_ids:
                obs = self.projection.observations[oid]
                uses = [
                    {
                        "use_id": u.use_id,
                        "asset_id": u.asset_id,
                        "purpose": u.purpose,
                        "used_at": u.used_at,
                        "used_by": u.used_by,
                        "publication_id": u.publication_id,
                        "exhibit_id": u.exhibit_id,
                        "detail": u.detail,
                    }
                    for u in self.projection.uses_for_observation(oid)
                ]
                works.append({
                    "observation_id": oid,
                    "current_credit": obs.credit,
                    "uses": uses,
                })
            return works

        corrections = []
        for event in self.store.all_events:
            if event["event_type"] != "CREDIT_CORRECTED":
                continue
            asset_id = event["payload"]["asset_id"]
            obs_id = self.projection.asset_observation.get(asset_id)
            if obs_id in current_ids or obs_id in corrected_away_ids:
                p = event["payload"]
                corrections.append({
                    "event_id": event["event_id"],
                    "asset_id": asset_id,
                    "observation_id": obs_id,
                    "corrected_at": p["corrected_at"],
                    "correct_photographer_id": p["correct_photographer_id"],
                    "correct_display_name": p.get("correct_display_name"),
                    "reason": p["reason"],
                })
        corrected_away = []
        for oid in corrected_away_ids:
            obs = self.projection.observations[oid]
            corrected_away.append({
                "observation_id": oid,
                "current_credit": obs.credit,
                "current_photographer_id": obs.credit_photographer_id,
            })
        return {
            "photographer_id": photographer_id,
            "works": works_for(current_ids),
            "works_corrected_away": corrected_away,
            "credit_corrections": corrections,
        }
