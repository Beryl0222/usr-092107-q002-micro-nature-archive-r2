"""影像档案命令行入口。

用法：

    python3 -m src.cli demo
        构建岭南演示场景并写出 data/demo/ 全部产物。

    python3 -m src.cli label <events.jsonl> <asset_id> [--at TS] [--role ROLE]
        为展项输出来源与许可说明。

    python3 -m src.cli export <events.jsonl> <out_dir> [--role qualified_researcher] [--sensitive]
        生成脱敏、带指纹的研究导出。

    python3 -m src.cli verify <research_export.json>
        复算导出指纹，验证可复现性。

    python3 -m src.cli trace <events.jsonl> <photographer_id>
        输出摄影师作品使用去向与署名更正记录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .demo import run as run_demo
from .exporting import build_research_export, verify_export_file, write_research_export
from .policy import Caller
from .services import ArchiveService
from .store import EventStore

ROLES = ("public", "curator", "photographer", "qualified_researcher", "archivist")


def _load_service(path: str) -> ArchiveService:
    store = EventStore(path)
    return ArchiveService(store)


def _print_json(value: object) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="micro-nature-archive", description="岭南微距影像档案服务")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="构建端到端演示场景")

    p_label = sub.add_parser("label", help="生成展项来源与许可说明")
    p_label.add_argument("events")
    p_label.add_argument("asset_id")
    p_label.add_argument("--at", required=True)
    p_label.add_argument("--role", default="public", choices=ROLES)
    p_label.add_argument("--caller", default="cli")
    p_label.add_argument("--exhibit")

    p_export = sub.add_parser("export", help="生成脱敏研究导出")
    p_export.add_argument("events")
    p_export.add_argument("out_dir")
    p_export.add_argument("--role", default="qualified_researcher", choices=ROLES)
    p_export.add_argument("--caller", default="cli-researcher")
    p_export.add_argument("--sensitive", action="store_true", help="持有敏感物种精确位置专项授权")
    p_export.add_argument("--include-photographer", action="store_true")
    p_export.add_argument("--observation", action="append", help="只导出指定观察，可重复")

    p_verify = sub.add_parser("verify", help="复算导出文件指纹")
    p_verify.add_argument("export_file")

    p_trace = sub.add_parser("trace", help="摄影师作品使用去向")
    p_trace.add_argument("events")
    p_trace.add_argument("photographer_id")

    args = parser.parse_args(argv)

    if args.command == "demo":
        results = run_demo()
        _print_json(results)
        return 0

    if args.command == "label":
        svc = _load_service(args.events)
        caller = Caller(args.caller, args.role)
        label = svc.exhibit_label(asset_id=args.asset_id, caller=caller, at=args.at,
                                  exhibit_id=args.exhibit)
        _print_json(label)
        return 0 if label["compliant"] else 2

    if args.command == "export":
        store = EventStore(args.events)
        caller = Caller(args.caller, args.role,
                        frozenset({"sensitive_location"}) if args.sensitive else frozenset())
        payload = build_research_export(
            store, caller=caller,
            observation_ids=args.observation,
            include_photographer=args.include_photographer)
        info = write_research_export(payload, args.out_dir)
        _print_json(info)
        return 0

    if args.command == "verify":
        result = verify_export_file(Path(args.export_file))
        _print_json(result)
        return 0 if result["ok"] else 3

    if args.command == "trace":
        svc = _load_service(args.events)
        _print_json(svc.photographer_trace(args.photographer_id))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
