"""档案服务验收测试。

每条测试对应需求中的一个可验收结论：去重、派生谱系、鉴定时点、
撤权保留科研引用、位置资质、脱敏导出可复现、署名更正与使用追查。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.demo import build_world, run as run_demo
from src.errors import (
    DuplicateEventError,
    LicenseStateError,
    UnknownAggregateError,
    ValidationError,
    VersionConflictError,
)
from src.exporting import (
    build_research_export,
    canonical_dumps,
    verify_export_file,
    write_research_export,
)
from src.identity import derivative_asset_id, master_asset_id, observation_id, publication_id
from src.policy import Caller
from src.services import ArchiveService, sha256_hex
from src.store import EventStore
from src.validator import validate_envelope, validate_event


def checksum(label: str) -> dict:
    return {"algorithm": "sha256", "value": sha256_hex(label.encode())}


def make_service() -> tuple[ArchiveService, str]:
    svc = ArchiveService(EventStore())
    oid = observation_id(2026, 77)
    svc.register_observation(
        obs_id=oid, at="2026-09-30T10:00:00+08:00",
        observed_at="2026-09-20T09:12:00+08:00",
        photographer_id="p-a", display_name="甲",
        location={"precision": "blurred", "label": "测试地", "radius_m": 2000},
        conditions={"habitat": "林下", "weather": "多云"},
        original_file={"filename": "A.DNG", "checksum": checksum("A.DNG"), "byte_size": 100},
        initial_identification={"scientific_name": "Sp. alpha", "common_name": "甲种",
                                "basis": "photographer", "confidence": "low"},
        submission_id="sub-test-01",
        capture={"camera": "R5", "iso": 800, "aperture": "f/8"},
    )
    return svc, oid


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads(
            (Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_legacy_envelope_helper(self) -> None:
        # 没有 payload 的历史轻量信封仍可做信封级兼容校验
        legacy = {
            "event_id": "old-1", "event_type": "OBSERVATION_REGISTERED",
            "aggregate_type": "field_observation", "aggregate_id": "x",
            "occurred_at": "2026-01-01T00:00:00+08:00", "version": 1, "summary": "旧记录",
        }
        self.assertEqual(validate_envelope(legacy), [])
        self.assertTrue(validate_event(legacy))  # 完整校验要求 payload

    def test_rejects_bad_checksum_and_scope(self) -> None:
        svc, oid = make_service()
        bad_deriv = {
            "event_id": "x1", "event_type": "ASSET_DERIVED",
            "aggregate_type": "media_asset", "aggregate_id": f"media:{oid}",
            "occurred_at": "2026-09-21T00:00:00+08:00", "version": 1,
            "summary": "坏校验值",
            "payload": {
                "derivative_asset_id": "d", "parent_asset_id": master_asset_id(oid),
                "kind": "crop", "transform": {}, "producer": "p-a",
                "checksum": {"algorithm": "sha256", "value": "not-hex"},
            },
        }
        errors = validate_event(bad_deriv)
        self.assertTrue(any("checksum" in e for e in errors))


class ObservationRegistrationTest(unittest.TestCase):
    def test_registration_preserves_core_fields(self) -> None:
        svc, oid = make_service()
        obs = svc.projection.observations[oid]
        self.assertEqual(obs.observed_at, "2026-09-20T09:12:00+08:00")
        self.assertEqual(obs.location["precision"], "blurred")
        self.assertEqual(obs.conditions["habitat"], "林下")
        self.assertEqual(obs.original_file["checksum"]["value"], checksum("A.DNG")["value"])
        self.assertEqual(obs.capture["camera"], "R5")

    def test_duplicate_submission_is_one_observation(self) -> None:
        svc, oid = make_service()
        before = len(svc.projection.observations)
        event = svc.register_observation(
            obs_id=observation_id(2026, 78),
            at="2026-10-06T08:00:00+08:00",
            observed_at="2026-09-20T09:12:00+08:00", photographer_id="p-a",
            location={"precision": "blurred", "label": "重复"},
            conditions={"habitat": "林下"},
            original_file={"filename": "A.DNG", "checksum": checksum("A.DNG")},
            submission_id="sub-test-01",
        )
        self.assertEqual(event["aggregate_id"], oid)
        self.assertEqual(len(svc.projection.observations), before)

    def test_duplicate_event_id_conflicts(self) -> None:
        store = EventStore()
        base = {
            "event_id": "evt-00000001", "event_type": "USE_LOGGED",
            "aggregate_type": "exhibition_use", "aggregate_id": "usage:q",
            "occurred_at": "2026-10-01T00:00:00+08:00", "version": 1,
            "summary": "s1",
            "payload": {"use_id": "u1", "asset_id": "a", "purpose": "educational",
                        "used_at": "2026-10-01T00:00:00+08:00", "used_by": "x"},
        }
        store.append(dict(base))
        with self.assertRaises(DuplicateEventError):
            store.append(dict(base, summary="different"))

    def test_version_must_be_sequential(self) -> None:
        store = EventStore()
        good = {
            "event_id": "e1", "event_type": "USE_LOGGED",
            "aggregate_type": "exhibition_use", "aggregate_id": "usage:q",
            "occurred_at": "2026-10-01T00:00:00+08:00", "version": 1, "summary": "s",
            "payload": {"use_id": "u1", "asset_id": "a", "purpose": "educational",
                        "used_at": "2026-10-01T00:00:00+08:00", "used_by": "x"},
        }
        store.append(good)
        bad = dict(good, event_id="e2", version=3)
        with self.assertRaises(VersionConflictError):
            store.append(bad)

    def test_invalid_event_raises(self) -> None:
        with self.assertRaises(ValidationError):
            EventStore().append({"event_id": "bad"})


class LineageTest(unittest.TestCase):
    def test_derivation_chain_master_to_grade(self) -> None:
        svc, oid = make_service()
        master = master_asset_id(oid)
        svc.derive_asset(obs_id=oid, kind="crop", parent_asset_id=master,
                         at="2026-09-21T00:00:00+08:00", producer="p-a",
                         transform={"source_crop": [1, 2, 3, 4]}, checksum=checksum("crop"))
        crop = derivative_asset_id(oid, "crop", 1)
        svc.derive_asset(obs_id=oid, kind="enlarge", parent_asset_id=crop,
                         at="2026-09-22T00:00:00+08:00", producer="p-a",
                         transform={"scale": 2.0}, checksum=checksum("big"))
        big = derivative_asset_id(oid, "enlarge", 1)
        chain = [a.kind for a in svc.projection.lineage(big)]
        self.assertEqual(chain, ["master", "crop", "enlarge"])
        self.assertIn(crop, svc.projection.descendants(master))
        self.assertIn(big, svc.projection.descendants(master))

    def test_derive_requires_existing_parent(self) -> None:
        svc, oid = make_service()
        with self.assertRaises(UnknownAggregateError):
            svc.derive_asset(obs_id=oid, kind="crop", parent_asset_id="nope",
                             at="2026-09-21T00:00:00+08:00", producer="p-a",
                             transform={}, checksum=checksum("x"))


class IdentificationAndPublicationTest(unittest.TestCase):
    def test_published_catalog_keeps_name_at_freezing(self) -> None:
        svc, oid = make_service()
        master = master_asset_id(oid)
        svc.derive_asset(obs_id=oid, kind="crop", parent_asset_id=master,
                         at="2026-09-21T00:00:00+08:00", producer="p-a",
                         transform={}, checksum=checksum("crop"))
        crop = derivative_asset_id(oid, "crop", 1)
        svc.grant_license(obs_id=oid, asset_id=crop, granted_by="p-a",
                          scopes=["catalog_print"], granted_at="2026-09-25T00:00:00+08:00")
        pub = publication_id("CAT", 1)
        svc.freeze_publication(publication_id=pub, asset_id=crop,
                               frozen_at="2026-10-01T00:00:00+08:00", title="图录")
        page = svc.catalog_page(publication_id=pub)
        self.assertEqual(page["as_published"]["scientific_name"], "Sp. alpha")
        # 之后改名
        svc.review_identification(obs_id=oid, scientific_name="Sp. beta", common_name="乙种",
                                  basis="expert", confidence="high", decided_by="e1",
                                  decided_at="2026-10-04T00:00:00+08:00")
        page_after = svc.catalog_page(publication_id=pub)
        self.assertEqual(page_after["as_published"]["scientific_name"], "Sp. alpha")
        current = svc.projection.observations[oid].current_identification
        self.assertEqual(current["scientific_name"], "Sp. beta")
        self.assertEqual(len(svc.projection.observations[oid].review_history), 1)

    def test_freeze_is_immutable(self) -> None:
        svc, oid = make_service()
        crop = derivative_asset_id(oid, "crop", 1)
        master = master_asset_id(oid)
        svc.derive_asset(obs_id=oid, kind="crop", parent_asset_id=master,
                         at="2026-09-21T00:00:00+08:00", producer="p-a",
                         transform={}, checksum=checksum("crop"))
        svc.grant_license(obs_id=oid, asset_id=crop, granted_by="p-a",
                          scopes=["catalog_print"], granted_at="2026-09-25T00:00:00+08:00")
        svc.freeze_publication(publication_id="p1", asset_id=crop,
                               frozen_at="2026-10-01T00:00:00+08:00")
        with self.assertRaises(Exception):
            svc.freeze_publication(publication_id="p1", asset_id=crop,
                                   frozen_at="2026-10-02T00:00:00+08:00")


class LicenseTest(unittest.TestCase):
    def _asset_with_license(self, scopes):
        svc, oid = make_service()
        master = master_asset_id(oid)
        svc.derive_asset(obs_id=oid, kind="crop", parent_asset_id=master,
                         at="2026-09-21T00:00:00+08:00", producer="p-a",
                         transform={}, checksum=checksum("crop"))
        crop = derivative_asset_id(oid, "crop", 1)
        svc.grant_license(obs_id=oid, asset_id=crop, granted_by="p-a",
                          scopes=scopes, granted_at="2026-09-25T00:00:00+08:00")
        return svc, oid, crop

    def test_withdraw_keeps_research_citation_only(self) -> None:
        svc, oid, crop = self._asset_with_license(
            ["public_exhibition", "commercial", "research_citation"])
        at = "2026-10-06T00:00:00+08:00"
        svc.withdraw_license(license_id=f"lic-{oid}-001", withdrawn_at=at, reason="作者撤权")
        self.assertTrue(svc.projection.has_scope(crop, "research_citation", at=at))
        self.assertFalse(svc.projection.has_scope(crop, "commercial", at=at))
        self.assertFalse(svc.projection.has_scope(crop, "public_exhibition", at=at))
        # 撤权后科研使用可留痕，商业使用被闸门拦截
        svc.log_use(asset_id=crop, purpose="research_citation", used_at=at,
                    used_by="某大学标本馆")
        with self.assertRaises(LicenseStateError):
            svc.log_use(asset_id=crop, purpose="commercial", used_at=at, used_by="某公司")

    def test_double_withdraw_rejected(self) -> None:
        svc, oid, crop = self._asset_with_license(["commercial"])
        svc.withdraw_license(license_id=f"lic-{oid}-001",
                             withdrawn_at="2026-10-06T00:00:00+08:00", reason="r")
        with self.assertRaises(LicenseStateError):
            svc.withdraw_license(license_id=f"lic-{oid}-001",
                                 withdrawn_at="2026-10-07T00:00:00+08:00", reason="r2")

    def test_expired_license_blocks_use(self) -> None:
        svc, oid, crop = self._asset_with_license(["commercial"])
        lic = svc.projection.licenses[f"lic-{oid}-001"]
        lic.expires_at = "2026-10-01T00:00:00+08:00"
        self.assertFalse(svc.projection.has_scope(
            crop, "commercial", at="2026-10-06T00:00:00+08:00"))


class LocationPolicyTest(unittest.TestCase):
    def _sensitive_service(self):
        svc = ArchiveService(EventStore())
        oid = observation_id(2026, 88)
        svc.register_observation(
            obs_id=oid, at="2026-09-30T00:00:00+08:00",
            observed_at="2026-08-17T07:40:00+08:00",
            photographer_id="p-a",
            location={"precision": "exact", "label": "南岭", "lat": 24.9381, "lon": 112.9912},
            sensitive_taxon=True,
            conditions={"habitat": "林缘"},
            original_file={"filename": "S.DNG", "checksum": checksum("S.DNG")},
            initial_identification={"scientific_name": "Teinopalpus aureus",
                                    "common_name": "金斑喙凤蝶", "basis": "expert"},
        )
        return svc, oid

    def test_exact_location_only_for_qualified_researcher(self) -> None:
        svc, oid = self._sensitive_service()
        obs = svc.projection.observations[oid]
        public = Caller("kiosk", "public")
        researcher = Caller.qualified_researcher("r1")
        authorized = Caller.qualified_researcher("r2", sensitive=True)
        curator = Caller("c1", "curator")
        label = svc.exhibit_label(asset_id=master_asset_id(oid), caller=public,
                                  at="2026-10-06T00:00:00+08:00")
        self.assertEqual(label["provenance"]["location"]["precision"], "blurred")
        self.assertNotIn("lat", label["provenance"]["location"])
        # 普通研究者也看不到敏感物种精确点
        self.assertEqual(
            svc.exhibit_label(asset_id=master_asset_id(oid), caller=researcher,
                              at="2026-10-06T00:00:00+08:00")["provenance"]["location"]["precision"],
            "blurred")
        exact = svc.exhibit_label(asset_id=master_asset_id(oid), caller=authorized,
                                  at="2026-10-06T00:00:00+08:00")["provenance"]["location"]
        self.assertEqual(exact["precision"], "exact")
        self.assertEqual(exact["lat"], 24.9381)
        self.assertEqual(
            svc.exhibit_label(asset_id=master_asset_id(oid), caller=curator,
                              at="2026-10-06T00:00:00+08:00")["provenance"]["location"]["precision"],
            "blurred")


class ExportTest(unittest.TestCase):
    def test_export_is_masked_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            store = EventStore(path)
            svc, _ = build_world(store)
            o2 = observation_id(2026, 2)

            # 普通研究者导出：敏感物种坐标被模糊，且无摄影师身份字段
            caller = Caller.qualified_researcher("r-luoming")
            payload = build_research_export(store, caller=caller)
            row = next(r for r in payload["records"] if r["observation_id"] == o2)
            self.assertEqual(row["location"]["precision"], "blurred")
            self.assertNotIn("lat", row["location"])
            self.assertNotIn("photographer_id", row)

            # 有专项授权的研究者：坐标保留
            auth = Caller.qualified_researcher("r-zhaomin", sensitive=True)
            payload_auth = build_research_export(store, caller=auth)
            row_auth = next(r for r in payload_auth["records"]
                            if r["observation_id"] == o2)
            self.assertEqual(row_auth["location"]["precision"], "exact")

            # 落盘后指纹可复算
            info = write_research_export(payload, Path(tmp) / "out")
            verify = verify_export_file(info["export_file"])
            self.assertTrue(verify["ok"])

            # 从 JSONL 重建后重导，字节级一致
            rebuilt = EventStore(path)
            payload2 = build_research_export(rebuilt, caller=caller)
            self.assertEqual(canonical_dumps(payload2), canonical_dumps(payload))

    def test_unknown_observation_rejected(self) -> None:
        store = EventStore()
        with self.assertRaises(ValueError):
            build_research_export(store, caller=Caller.qualified_researcher("r"),
                                  observation_ids=["obs-lingnan-2026-9999"])


class CreditAndTraceTest(unittest.TestCase):
    def test_demo_credit_correction_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.jsonl")
            svc, _ = build_world(store)
            o4 = observation_id(2026, 4)
            obs = svc.projection.observations[o4]
            self.assertEqual(obs.credit, "何琦")
            self.assertEqual(obs.credit_photographer_id, "p-heqi")

            trace = svc.photographer_trace("p-heqi")
            self.assertEqual([w["observation_id"] for w in trace["works"]], [o4])
            # 更正前的展示使用仍在去向中，不删除历史
            uses = trace["works"][0]["uses"]
            self.assertTrue(any(u["exhibit_id"] == "EX-2026-031" for u in uses))
            self.assertEqual(len(trace["credit_corrections"]), 1)
            self.assertIn("借设备", trace["credit_corrections"][0]["reason"])

            # 原误署作者名下不再保留该作品的当前归属
            trace_linwei = svc.photographer_trace("p-linwei")
            self.assertEqual(trace_linwei["works"], [])


class DemoAcceptanceTest(unittest.TestCase):
    def test_full_demo_acceptance_summary(self) -> None:
        results = run_demo()
        self.assertTrue(results["label_compliant"])
        self.assertFalse(results["label_blocked_compliant_flag"])
        self.assertEqual(results["observation_count"], 5)
        self.assertEqual(results["duplicate_submission_observation"],
                         observation_id(2026, 1))
        self.assertTrue(results["rebuild_reproducible"])
        self.assertTrue(results["export_verify"]["ok"])
        self.assertTrue(results["withdrawal"]["commercial_blocked"])
        self.assertTrue(results["withdrawal"]["research_citation_kept"])
        self.assertTrue(results["withdrawal"]["commercial_removed"])
        # 图录旧名 vs 当前新名
        self.assertEqual(results["catalog_as_published"]["scientific_name"], "Hestiasula sp.")
        self.assertEqual(results["catalog_current_name"][0], "Hestiasula major")
        # 敏感位置仅授权研究者可见
        self.assertEqual(results["sensitive_location_curator"]["precision"], "blurred")
        self.assertEqual(results["sensitive_location_researcher"]["precision"], "blurred")
        self.assertEqual(results["sensitive_location_researcher_sensitive"]["precision"], "exact")


if __name__ == "__main__":
    unittest.main()
