"""Repository-local CLI: python -m ddl_text --demo multiple."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .adapters import ReplayAdapter, UnavailableAdapter
from .config import Settings
from .codex_client import ROOT, live_options, preflight, validate_endpoint, validate_provider_endpoint
from .extraction import ExtractionAdapter, OfflineDraftClient
from .pipeline import process_request
from .render import render_text
from .provider import make_client


EXAMPLES = Path(__file__).resolve().parents[1] / "ddl_text/tests/fixtures/text-contract-v0.1.json"


def main(argv=None):
    # Configure both streams before argparse can emit localized errors on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="文本识别：离线回放、七牛 API 或 DeepSeek 经 Codex")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", help="合成样例 case_id，例如 absolute、multiple、partial-batch")
    source.add_argument("--input", help="UTF-8 JSON 请求路径，- 表示标准输入")
    parser.add_argument("--replay", help="人工合成回放文件，格式与契约样例相同")
    parser.add_argument("--drafts", help="离线合成 Agent 草稿文件；运行真实日期和证据处理，不调用模型")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline")
    parser.add_argument("--format", choices=["text", "json"], default="json")
    parser.add_argument("--config", help="仅含非敏感参数的 JSON 配置路径")
    parser.add_argument("--audit", action="store_true", help="向标准错误输出不含正文的状态与耗时")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="真实模式的本地密钥配置文件")
    parser.add_argument("--check-connection", action="store_true", help="只检查本地配置和 CLI，不访问模型")
    args = parser.parse_args(argv)
    if not (args.demo or args.input or args.check_connection):
        parser.error("请选择 --demo、--input 或 --check-connection")
    if args.check_connection and (args.demo or args.input or args.replay or args.drafts):
        parser.error("本地配置检查不能与输入或回放同时使用")
    if args.demo and (args.mode != "offline" or args.replay or args.drafts):
        parser.error("--demo 只允许离线内置回放")
    if args.mode == "real" and (args.replay or args.drafts):
        parser.error("真实模式不能使用样例回放")
    if args.replay and args.drafts:
        parser.error("完整结果回放与草稿处理不能同时选择")
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig")) if args.config else {}
        if not isinstance(config, dict) or "mode" in config:
            raise ValueError("mode must be selected explicitly by CLI")
        options = None
        if args.mode == "real" or args.check_connection:
            options = live_options(Path(args.env_file))
            config.setdefault("model", options["model"])
            config.setdefault("endpoint", options["endpoint"])
            config.setdefault("timeout_seconds", 120)
            if config.get("credential_env") not in (None, options["credential_env"]):
                raise ValueError("credential does not match provider")
        settings = Settings(mode=args.mode, **config)
        adapter = UnavailableAdapter()
        if options is not None:
            if not settings.model or not settings.endpoint:
                raise ValueError("real mode requires model and endpoint")
            validate_endpoint(settings.endpoint)
            validate_provider_endpoint(options["provider"], settings.endpoint)
            if args.check_connection:
                if options["provider"] == "deepseek":
                    preflight(options)
                print("provider=" + options["provider"] + "; model=" + settings.model + "; endpoint=" + settings.endpoint)
                print(("七牛 API 直连；" if options["provider"] == "qiniu" else "Codex CLI：可用；") + "配置格式：通过；密钥：" +
                      ("已配置" if options["key"].strip() else "未填写") + "。本检查未调用模型。")
                return 0 if options["key"].strip() else 1
            if options["key"].strip():
                adapter = ExtractionAdapter(make_client(options))
        if args.drafts:
            entries = json.loads(Path(args.drafts).read_text(encoding="utf-8-sig"))
            if not isinstance(entries, list):
                raise ValueError("draft entries must be a list")
            adapter = ExtractionAdapter(OfflineDraftClient(entries))
        if args.demo or args.replay:
            fixture = json.loads((EXAMPLES if args.demo else Path(args.replay)).read_text(encoding="utf-8-sig"))
            cases = fixture["cases"]
            if not isinstance(cases, list):
                raise ValueError("cases must be a list")
            adapter = ReplayAdapter(cases)
        if args.demo:
            matching = [case for case in cases if case["case_id"] == args.demo]
            if len(matching) != 1:
                raise ValueError("unknown demo")
            request = matching[0]["input"]
        else:
            raw = sys.stdin.buffer.read().decode("utf-8-sig") if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig")
            request = json.loads(raw)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        parser.error("无法读取或解析输入、回放或配置；请检查格式与参数。")
    if args.audit:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Diagnostics describe mode; JSON stdout stays a pure versioned result object.
    print("mode=%s; adapter=%s" % (settings.mode,
          ("qiniu_api" if options["provider"] == "qiniu" else "codex") if settings.mode == "real"
          else "synthetic_drafts" if args.drafts else "replay_only"), file=sys.stderr)
    result = process_request(request, adapter, settings)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json"
          else render_text(result, "drafts" if args.drafts else settings.mode))
    return 0 if result["status"] in {"success", "no_tasks"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
