"""岭南微距影像档案端到端演示场景。

场景覆盖全部验收点：

1. 野外观察登记保留时间、模糊/精确位置、环境条件、母版校验值与拍摄参数；
2. 裁切、调色、放大作为派生事件演进，专家鉴定、作者授权各自独立演进；
3. 敏感物种（金斑喙凤蝶，国家一级保护）精确位置仅对有专项授权的研究者开放；
4. 公众名称更新后，已出版图录仍显示冻结时依据；
5. 撤回商业/展示许可不影响依法保存的科研引用；
6. 重复投稿不形成第二次观察；错误署名可更正且使用去向可追查；
7. 研究导出脱敏、跨重跑/跨重建字节一致（可复现）。

运行：``python3 -m src.demo``，产物写入 ``data/demo/``。
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import LicenseStateError
from .exporting import (
    build_research_export,
    canonical_dumps,
    verify_export_file,
    write_research_export,
)
from .identity import (
    derivative_asset_id,
    master_asset_id,
    observation_id,
    publication_id,
)
from .policy import Caller
from .services import ArchiveService, sha256_hex
from .store import EventStore

DEMO_DIR = Path(__file__).parents[1] / "data" / "demo"
EVENTS_PATH = DEMO_DIR / "events.jsonl"


def fake_checksum(label: str) -> dict:
    return {"algorithm": "sha256", "value": sha256_hex(label.encode("utf-8"))}


def build_world(store: EventStore | None = None) -> tuple[ArchiveService, dict]:
    """按固定时间线构建演示档案；给定空 store 时幂等可重建。

    返回 (服务, 重复投稿返回的既有登记事件)。
    """
    svc = ArchiveService(store or EventStore())

    o1 = observation_id(2026, 1)
    o2 = observation_id(2026, 2)
    o3 = observation_id(2026, 3)
    o4 = observation_id(2026, 4)
    o5 = observation_id(2026, 5)

    # ---- 1. 陈静：中华齿螳（常规观察，模糊位置） ------------------------
    svc.register_observation(
        obs_id=o1,
        at="2026-09-30T10:00:00+08:00",
        observed_at="2026-09-20T09:12:00+08:00",
        photographer_id="p-chenjing",
        display_name="陈静",
        location={
            "precision": "blurred",
            "label": "广东·鼎湖山国家级自然保护区（周边山地）",
            "radius_m": 2000,
        },
        conditions={
            "habitat": "亚热带常绿阔叶林林下落叶层",
            "weather": "多云转晴",
            "temperature_c": 26.4,
            "notes": "晨露未散，逆光微距",
        },
        original_file={
            "filename": "DJI_20260920_091200_RAW.DNG",
            "checksum": fake_checksum("master:obs1:DJI_20260920_091200_RAW.DNG"),
            "byte_size": 48231200,
        },
        initial_identification={
            "scientific_name": "Odontomantis sinensis",
            "common_name": "中华齿螳",
            "basis": "photographer",
            "confidence": "medium",
            "taxonomy_source": "WF11 2026",
        },
        submission_id="sub-2026-09-30-chenjing-01",
        capture={
            "camera": "Canon EOS R5",
            "lens": "RF 100mm F2.8L Macro",
            "focal_length_mm": 100.0,
            "aperture": "f/8",
            "shutter_speed": "1/250",
            "iso": 800,
            "magnification": "1.4x",
        },
    )
    m1 = master_asset_id(o1)
    svc.derive_asset(
        obs_id=o1, kind="crop", parent_asset_id=m1, at="2026-09-21T14:00:00+08:00",
        producer="p-chenjing",
        transform={"source_crop": [1200, 800, 3600, 3000], "software": "Darktable 4.8"},
        checksum=fake_checksum("deriv:obs1:crop:1"),
        filename="odontomantis_crop_01.tiff",
    )
    crop1 = derivative_asset_id(o1, "crop", 1)
    svc.derive_asset(
        obs_id=o1, kind="color_grade", parent_asset_id=crop1, at="2026-09-21T14:20:00+08:00",
        producer="editor-li",
        transform={"preset": "LN_exhibition_warm_v3", "white_balance_k": 5300,
                   "saturation": "+6", "software": "Darktable 4.8"},
        checksum=fake_checksum("deriv:obs1:color_grade:1"),
        filename="odontomantis_exhibition.tiff",
    )
    grade1 = derivative_asset_id(o1, "color_grade", 1)
    svc.grant_license(
        obs_id=o1, asset_id=grade1, granted_by="p-chenjing",
        scopes=["public_exhibition", "catalog_print", "web_promotion"],
        granted_at="2026-09-28T09:00:00+08:00",
        terms="署名-非商业性使用 4.0；展项与图录须标注作者",
    )
    svc.freeze_publication(
        publication_id=publication_id("LNWG2026", 12),
        asset_id=grade1, frozen_at="2026-10-01T09:00:00+08:00",
        title="《岭南微观·2026》图录 第12页",
    )
    pub1 = publication_id("LNWG2026", 12)
    svc.log_use(asset_id=grade1, purpose="catalog_print",
                used_at="2026-10-02T10:00:00+08:00", used_by="美术馆出版部",
                publication_id=pub1, detail="图录首版印刷 1200 册")
    svc.log_use(asset_id=grade1, purpose="public_exhibition",
                used_at="2026-10-05T09:30:00+08:00", used_by="美术馆展览部",
                exhibit_id="EX-2026-018", detail="「岭南微观」常设展展项 EX-2026-018")

    # ---- 2. 陈静：金斑喙凤蝶（敏感物种，精确位置入库） ------------------
    svc.register_observation(
        obs_id=o2,
        at="2026-09-30T10:05:00+08:00",
        observed_at="2026-08-17T07:40:00+08:00",
        photographer_id="p-chenjing",
        display_name="陈静",
        location={
            "precision": "exact",
            "label": "广东·南岭（精确坐标限资质访问）",
            "lat": 24.9381,
            "lon": 112.9912,
            "radius_m": 0,
        },
        sensitive_taxon=True,
        conditions={
            "habitat": "中山常绿阔叶林林缘蜜源植物",
            "weather": "晴",
            "temperature_c": 22.1,
            "notes": "国家一级保护野生动物，坐标按档案规则封存",
        },
        original_file={
            "filename": "DJI_20260817_074000_RAW.DNG",
            "checksum": fake_checksum("master:obs2:teinopetalus_raw"),
            "byte_size": 52100440,
        },
        initial_identification={
            "scientific_name": "Teinopalpus aureus",
            "common_name": "金斑喙凤蝶",
            "basis": "expert",
            "confidence": "high",
            "taxonomy_source": "中国生物物种名录 2024",
        },
        submission_id="sub-2026-09-30-chenjing-02",
        capture={"camera": "Canon EOS R5", "lens": "RF 100mm F2.8L Macro",
                 "aperture": "f/5.6", "shutter_speed": "1/500", "iso": 400},
    )
    m2 = master_asset_id(o2)
    svc.derive_asset(
        obs_id=o2, kind="enlarge", parent_asset_id=m2, at="2026-09-22T10:00:00+08:00",
        producer="p-chenjing",
        transform={"scale": 2.0, "interpolation": "lanczos3", "target_dpi": 600},
        checksum=fake_checksum("deriv:obs2:enlarge:1"),
        filename="teinopalpus_plate_2x.tiff",
    )
    enlarge2 = derivative_asset_id(o2, "enlarge", 1)
    svc.grant_license(
        obs_id=o2, asset_id=enlarge2, granted_by="p-chenjing",
        scopes=["research_citation", "educational"],
        granted_at="2026-09-29T09:00:00+08:00",
        terms="科研与教育用途；禁止商业展示，禁止公开精确栖息地",
    )
    svc.log_use(asset_id=enlarge2, purpose="research_citation",
                used_at="2026-10-03T15:00:00+08:00",
                used_by="华南昆虫研究所·赵岷",
                detail="保护遗传学论文图版，引用观察编号")

    # ---- 3. 王秀：巨腿螳，鉴定随时间演进，图录冻结旧名 ------------------
    svc.register_observation(
        obs_id=o3,
        at="2026-09-30T10:10:00+08:00",
        observed_at="2026-07-09T11:05:00+08:00",
        photographer_id="p-wangxiu",
        display_name="王秀",
        location={
            "precision": "blurred",
            "label": "广西·大瑶山（沟谷雨林带）",
            "radius_m": 5000,
        },
        conditions={"habitat": "沟谷雨林灌木层", "weather": "阴", "temperature_c": 28.0},
        original_file={
            "filename": "WX_20260709_110500.NEF",
            "checksum": fake_checksum("master:obs3:hestiasula_raw"),
            "byte_size": 39881220,
        },
        initial_identification={
            "scientific_name": "Hestiasula sp.",
            "common_name": "巨腿螳（未定种）",
            "basis": "photographer",
            "confidence": "low",
        },
        submission_id="sub-2026-09-30-wangxiu-01",
    )
    m3 = master_asset_id(o3)
    svc.derive_asset(
        obs_id=o3, kind="crop", parent_asset_id=m3, at="2026-09-18T09:00:00+08:00",
        producer="p-wangxiu",
        transform={"source_crop": [400, 300, 5200, 4200]},
        checksum=fake_checksum("deriv:obs3:crop:1"),
        filename="hestiasula_crop.tiff",
    )
    crop3 = derivative_asset_id(o3, "crop", 1)
    svc.grant_license(
        obs_id=o3, asset_id=crop3, granted_by="p-wangxiu",
        scopes=["public_exhibition", "catalog_print"],
        granted_at="2026-09-27T09:00:00+08:00", terms="署名使用",
    )
    # 策展初审：仍只到属
    svc.review_identification(
        obs_id=o3, scientific_name="Hestiasula sp.", common_name="巨腿螳（未定种）",
        basis="curator", confidence="low", decided_by="curator-zhou",
        decided_at="2026-09-26T14:00:00+08:00", note="图录送印前未能定种，按属级付印",
    )
    # 10-01 图录按当时名称冻结
    svc.freeze_publication(
        publication_id=publication_id("LNWG2026", 27),
        asset_id=crop3, frozen_at="2026-10-01T09:00:00+08:00",
        title="《岭南微观·2026》图录 第27页",
    )
    pub3 = publication_id("LNWG2026", 27)
    svc.log_use(asset_id=crop3, purpose="catalog_print",
                used_at="2026-10-02T10:00:00+08:00", used_by="美术馆出版部",
                publication_id=pub3, detail="图录首版印刷")
    # 10-04 专家定种：公众名称更新
    svc.review_identification(
        obs_id=o3, scientific_name="Hestiasula major", common_name="大巨腿螳",
        basis="expert", confidence="high", decided_by="e-zhaomin",
        decided_at="2026-10-04T16:30:00+08:00",
        taxonomy_source="Zootaxa 2026 修订名录",
        note="比对外生殖器与前足斑纹，由属级修订为有效种",
    )

    # ---- 4. 何琦：阳彩臂金龟，原误署林伟，后更正署名 --------------------
    svc.register_observation(
        obs_id=o4,
        at="2026-09-30T10:15:00+08:00",
        observed_at="2026-06-11T20:18:00+08:00",
        photographer_id="p-linwei",
        display_name="林伟",
        location={
            "precision": "blurred",
            "label": "广东·象头山省级自然保护区（周边）",
            "radius_m": 3000,
        },
        conditions={"habitat": "常绿阔叶林夜间灯诱点", "weather": "闷热有阵雨",
                    "temperature_c": 27.6},
        original_file={
            "filename": "LW_20260611_201800_RAW.CR3",
            "checksum": fake_checksum("master:obs4:cheirotonus_raw"),
            "byte_size": 44102870,
        },
        initial_identification={
            "scientific_name": "Cheirotonus jansoni",
            "common_name": "阳彩臂金龟",
            "basis": "curator",
            "confidence": "high",
        },
        submission_id="sub-2026-09-30-linwei-01",
    )
    m4 = master_asset_id(o4)
    svc.derive_asset(
        obs_id=o4, kind="crop", parent_asset_id=m4, at="2026-09-19T11:00:00+08:00",
        producer="p-linwei",
        transform={"source_crop": [800, 600, 4600, 3800]},
        checksum=fake_checksum("deriv:obs4:crop:1"),
        filename="cheirotonus_crop.tiff",
    )
    crop4 = derivative_asset_id(o4, "crop", 1)
    svc.grant_license(
        obs_id=o4, asset_id=crop4, granted_by="p-linwei",
        scopes=["public_exhibition", "commercial"],
        granted_at="2026-09-27T09:00:00+08:00", terms="署名使用",
    )
    # 更正前已按旧署名投入展示（去向仍可追查，记录不删除）
    svc.log_use(asset_id=crop4, purpose="public_exhibition",
                used_at="2026-10-05T09:30:00+08:00", used_by="美术馆展览部",
                exhibit_id="EX-2026-031", detail="展期早期标签署名：林伟")
    # 10-05 摄影师申诉：借用设备导致 EXIF 与投稿署名错误
    svc.correct_credit(
        obs_id=o4, asset_id=crop4,
        correct_photographer_id="p-heqi", correct_display_name="何琦",
        corrected_at="2026-10-05T13:00:00+08:00",
        reason="相机为何琦所有、当晚由何琦拍摄；林伟系借设备导出，EXIF 与投稿署名均误",
    )

    # ---- 5. 陈静：龙眼鸡，撤回商业/展示许可，科研引用保留 ---------------
    svc.register_observation(
        obs_id=o5,
        at="2026-09-30T10:20:00+08:00",
        observed_at="2026-05-30T16:42:00+08:00",
        photographer_id="p-chenjing",
        display_name="陈静",
        location={
            "precision": "blurred",
            "label": "广东·广州·龙眼树林（城市边缘）",
            "radius_m": 1500,
        },
        conditions={"habitat": "龙眼树枝干", "weather": "雷阵雨后",
                    "temperature_c": 29.3},
        original_file={
            "filename": "DJI_20260530_164200_RAW.DNG",
            "checksum": fake_checksum("master:obs5:pyrops_raw"),
            "byte_size": 41220330,
        },
        initial_identification={
            "scientific_name": "Pyrops candelarius",
            "common_name": "龙眼鸡",
            "basis": "photographer",
            "confidence": "high",
        },
        submission_id="sub-2026-09-30-chenjing-03",
    )
    m5 = master_asset_id(o5)
    svc.derive_asset(
        obs_id=o5, kind="color_grade", parent_asset_id=m5, at="2026-09-15T10:00:00+08:00",
        producer="editor-li",
        transform={"preset": "LN_pop_v2", "saturation": "+10"},
        checksum=fake_checksum("deriv:obs5:color_grade:1"),
        filename="pyrops_pop.tiff",
    )
    grade5 = derivative_asset_id(o5, "color_grade", 1)
    svc.grant_license(
        obs_id=o5, asset_id=grade5, granted_by="p-chenjing",
        scopes=["public_exhibition", "catalog_print", "commercial"],
        granted_at="2026-09-24T09:00:00+08:00",
        terms="一年期商业与展示授权",
    )
    lic5 = f"lic-{o5}-001"
    svc.log_use(asset_id=grade5, purpose="commercial",
                used_at="2026-10-01T12:00:00+08:00",
                used_by="岭南文创公司", detail="文创明信片打样（撤权前）")
    # 摄影师终止商业合作
    svc.withdraw_license(
        license_id=lic5, withdrawn_at="2026-10-05T18:00:00+08:00",
        reason="摄影师通知终止商业合作并停止公开展示",
    )
    # 撤权后：科研引用依法保留，可以继续
    svc.log_use(asset_id=grade5, purpose="research_citation",
                used_at="2026-10-06T09:00:00+08:00",
                used_by="华南农业大学·昆虫标本馆",
                detail="撤权后依法保存的科研引用")

    # ---- 6. 重复投稿：同一 submission_id 再次提交，必须指向同一观察 ------
    duplicate = svc.register_observation(
        obs_id=observation_id(2026, 6),  # 即使换了编号
        at="2026-10-06T08:00:00+08:00",
        observed_at="2026-09-20T09:12:00+08:00",
        photographer_id="p-chenjing",
        location={"precision": "blurred", "label": "重复投稿测试", "radius_m": 2000},
        conditions={"habitat": "x"},
        original_file={
            "filename": "DJI_20260920_091200_RAW.DNG",
            "checksum": fake_checksum("master:obs1:DJI_20260920_091200_RAW.DNG"),
        },
        submission_id="sub-2026-09-30-chenjing-01",  # 同一投稿
    )
    assert duplicate["aggregate_id"] == o1

    return svc, duplicate


def run() -> dict:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    if EVENTS_PATH.exists():
        EVENTS_PATH.unlink()

    store = EventStore(EVENTS_PATH)
    svc, duplicate = build_world(store)

    o1, o2, o3, o5 = (observation_id(2026, n) for n in (1, 2, 3, 5))
    grade1 = derivative_asset_id(o1, "color_grade", 1)
    enlarge2 = derivative_asset_id(o2, "enlarge", 1)
    grade5 = derivative_asset_id(o5, "color_grade", 1)
    crop3 = derivative_asset_id(o3, "crop", 1)
    pub3 = publication_id("LNWG2026", 27)

    results: dict[str, object] = {}

    # --- 策展说明：合规展项 ---
    label_ok = svc.exhibit_label(
        asset_id=grade1, caller=Caller("gallery-kiosk-01", "public"),
        at="2026-10-06T10:00:00+08:00", exhibit_id="EX-2026-018",
    )
    (DEMO_DIR / "label_compliant.json").write_text(
        canonical_dumps(label_ok) + "\n", encoding="utf-8")

    # --- 策展说明：撤权后不合规展项（尝试布展被许可闸门拦截） ---
    blocked: dict
    try:
        svc.log_use(asset_id=grade5, purpose="public_exhibition",
                    used_at="2026-10-06T10:00:00+08:00",
                    used_by="美术馆展览部", exhibit_id="EX-2026-099")
        blocked = {"blocked": False}
    except LicenseStateError as exc:
        blocked = {"blocked": True, "reason": str(exc)}
    label_bad = svc.exhibit_label(
        asset_id=grade5, caller=Caller("gallery-kiosk-02", "public"),
        at="2026-10-06T10:00:00+08:00", exhibit_id="EX-2026-099",
    )
    (DEMO_DIR / "label_blocked.json").write_text(
        canonical_dumps({"attempt": blocked, "label": label_bad}) + "\n",
        encoding="utf-8")

    # --- 已出版图录：旧名保留；展项标签显示新名 ---
    page = svc.catalog_page(publication_id=pub3)
    (DEMO_DIR / "catalog_page_frozen.json").write_text(
        canonical_dumps(page) + "\n", encoding="utf-8")
    results["catalog_as_published"] = page["as_published"]
    results["catalog_current_name"] = (
        svc.projection.observations[o3].current_identification["scientific_name"],
        svc.projection.observations[o3].current_identification["common_name"],
    )

    # --- 研究导出：三种调用方 ---
    callers = {
        "curator": Caller("curator-zhou", "curator"),
        "researcher": Caller.qualified_researcher("r-luoming"),
        "researcher_sensitive": Caller.qualified_researcher("r-zhaomin", sensitive=True),
    }
    fingerprints: dict[str, str] = {}
    for name, caller in callers.items():
        payload = build_research_export(
            store, caller=caller, include_photographer=False, include_lineage=True)
        info = write_research_export(payload, DEMO_DIR / f"export_{name}")
        fingerprints[name] = info["export_fingerprint"]
        results[f"sensitive_location_{name}"] = [
            r["location"] for r in payload["records"]
            if r["observation_id"] == o2
        ][0]

    # --- 可复现：从 JSONL 重建存储后重导，指纹必须一致 ---
    rebuilt = EventStore(EVENTS_PATH)
    payload_rebuilt = build_research_export(
        rebuilt, caller=callers["researcher"], include_photographer=False)
    rebuilt_ok = (
        payload_rebuilt["manifest"]["export_fingerprint"]["value"]
        == fingerprints["researcher"]
        and payload_rebuilt["manifest"]["event_stream"]["value"]
        == build_research_export(store, caller=callers["researcher"])["manifest"]["event_stream"]["value"]
    )
    verify = verify_export_file(DEMO_DIR / "export_researcher" / "research_export.json")

    # --- 摄影师追查：何琦（更正后的真实作者） ---
    trace = svc.photographer_trace("p-heqi")
    (DEMO_DIR / "photographer_trace_heqi.json").write_text(
        canonical_dumps(trace) + "\n", encoding="utf-8")

    # --- 敏感资产的位置视图：公众/普通研究者/授权研究者 ---
    results["observation_count"] = len(svc.projection.observations)
    results["event_count"] = len(store.all_events)
    results["rebuild_reproducible"] = rebuilt_ok
    results["export_verify"] = verify
    results["trace_heqi_work_count"] = len(trace["works"])
    results["trace_heqi_uses"] = [
        (w["observation_id"], u["purpose"], u["exhibit_id"])
        for w in trace["works"] for u in w["uses"]
    ]
    results["withdrawal"] = {
        "commercial_blocked": blocked["blocked"],
        "research_citation_kept": svc.projection.has_scope(
            grade5, "research_citation", at="2026-10-06T10:00:00+08:00"),
        "commercial_removed": not svc.projection.has_scope(
            grade5, "commercial", at="2026-10-06T10:00:00+08:00"),
    }
    results["duplicate_submission_observation"] = duplicate["aggregate_id"]
    results["label_compliant"] = label_ok["compliant"]
    results["label_blocked_compliant_flag"] = label_bad["compliant"]
    results["lineage_grade1"] = [
        (a["kind"], a["checksum"] is not None) for a in label_ok["provenance"]["lineage"]
    ]
    results["research_plate_license_only"] = sorted(
        s for lic in svc.projection.effective_licenses(enlarge2) for s in lic.scopes
    )

    (DEMO_DIR / "acceptance_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return results


def main() -> None:
    results = run()
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\n产物目录：{DEMO_DIR}")


if __name__ == "__main__":
    main()
