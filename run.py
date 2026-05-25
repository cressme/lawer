#!/usr/bin/env python3
"""
微信证据助手 - 启动脚本

提供命令行入口，支持交互式对话、快速提取、快速导出等模式。

用法示例:
    python run.py                          # 交互式对话（默认）
    python run.py chat                     # 同上
    python run.py --config config.yaml     # 使用自定义配置
    python run.py extract --contact 张三    # 快速提取聊天记录
    python run.py export --contact 张三 --format docx --output ./out
    python run.py contacts                 # 列出联系人
    python run.py init                     # 生成默认配置文件
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 启动横幅（精简版，用于非交互模式）
# ---------------------------------------------------------------------------

_BANNER_MINI = """\
\033[36m\033[1m微信证据助手 v1.0\033[0m  |  WeChat Evidence Agent
"""


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_chat(args: argparse.Namespace) -> None:
    """启动交互式对话。"""
    config = _load_config(args)
    from wechat_evidence_agent.main import WeChatEvidenceApp

    app = WeChatEvidenceApp(config=config)
    app.start_interactive()


def cmd_gui(args: argparse.Namespace) -> None:
    """Start the local browser client."""
    config = _load_config(args)
    from wechat_evidence_agent.web_client import run_web_client

    config_path = Path(getattr(args, "config", None) or "config.yaml")
    run_web_client(
        config=config,
        config_path=config_path,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


def cmd_extract(args: argparse.Namespace) -> None:
    """快速提取聊天记录。"""
    config = _load_config(args)
    print(_BANNER_MINI)

    from wechat_evidence_agent.main import WeChatEvidenceApp

    app = WeChatEvidenceApp(config=config)

    contact = args.contact
    start = args.start
    end = args.end

    print(f"正在提取与「{contact}」的聊天记录...")
    if start or end:
        print(f"  时间范围：{start or '不限'} 至 {end or '不限'}")

    try:
        result = app.quick_extract(contact, start_date=start, end_date=end)
    except Exception as e:
        print(f"\033[31m错误：{e}\033[0m")
        sys.exit(1)

    if result.get("error"):
        print(f"\033[31m错误：{result['error']}\033[0m")
        sys.exit(1)

    count = result["count"]
    print(f"\033[32m提取完成：共 {count} 条消息\033[0m")

    # 打印时间线摘要
    stats = result.get("timeline", {})
    if stats.get("date_range"):
        dr = stats["date_range"]
        print(f"  时间跨度：{dr.get('start', '?')} ~ {dr.get('end', '?')}")
    if stats.get("days_count"):
        print(f"  活跃天数：{stats['days_count']} 天")
    if stats.get("type_counts"):
        print(f"  消息类型：", end="")
        parts = [f"{k} {v}条" for k, v in stats["type_counts"].items()]
        print("、".join(parts))


def cmd_export(args: argparse.Namespace) -> None:
    """快速导出证据文件。"""
    config = _load_config(args)
    print(_BANNER_MINI)

    from wechat_evidence_agent.main import WeChatEvidenceApp

    app = WeChatEvidenceApp(config=config)

    contact = args.contact
    fmt = args.format
    output = args.output or config.output_dir

    print(f"正在导出与「{contact}」的聊天证据 ({fmt} 格式)...")

    try:
        result_path = app.quick_export(contact, output_path=output, format=fmt)
        print(f"\033[32m导出完成：{result_path}\033[0m")
    except Exception as e:
        print(f"\033[31m导出失败：{e}\033[0m")
        sys.exit(1)


def cmd_contacts(args: argparse.Namespace) -> None:
    """列出联系人。"""
    config = _load_config(args)
    print(_BANNER_MINI)

    from wechat_evidence_agent.main import WeChatEvidenceApp

    app = WeChatEvidenceApp(config=config)

    print("正在读取联系人列表...")
    try:
        result = app.agent.tool_executor.execute(
            "list_contacts",
            {"keyword": args.keyword} if args.keyword else {},
        )
        print(result)
    except Exception as e:
        print(f"\033[31m读取联系人失败：{e}\033[0m")
        sys.exit(1)


def cmd_init(args: argparse.Namespace) -> None:
    """生成默认配置文件。"""
    output_path = Path(args.output or "config.yaml")

    if output_path.exists():
        confirm = input(f"文件 {output_path} 已存在，是否覆盖？(y/N) ")
        if confirm.lower() != "y":
            print("已取消。")
            return

    from wechat_evidence_agent.config import Config as _Config

    config = _Config.get_default_config()
    config.save_to_file(output_path)
    print(f"\033[32m默认配置文件已生成：{output_path}\033[0m")
    print(f"\033[33m请编辑该文件填写 OpenAI API 密钥等必要配置。\033[0m")


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def _load_config(args: argparse.Namespace) -> "Config":
    """从命令行参数加载配置。"""
    from wechat_evidence_agent.config import Config

    config_path = getattr(args, "config", None)
    if config_path:
        path = Path(config_path)
        if not path.exists():
            print(f"\033[31m错误：配置文件不存在：{config_path}\033[0m")
            sys.exit(1)
        return Config.load_from_file(path)

    bundled_config = Path(sys.argv[0]).resolve().parent / "config.yaml"
    if bundled_config.exists():
        return Config.load_from_file(bundled_config)

    local_config = Path("config.yaml")
    if local_config.exists():
        return Config.load_from_file(local_config)

    return Config.get_default_config()


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="wechat-evidence",
        description="微信证据助手 - 为律师打造的微信电子证据智能分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python run.py                                # 启动交互式对话\n"
            "  python run.py --config my.yaml chat          # 使用自定义配置\n"
            '  python run.py extract --contact "张三"        # 快速提取聊天记录\n'
            '  python run.py export --contact "张三" -f docx # 快速导出证据\n'
            "  python run.py contacts                       # 列出所有联系人\n"
            "  python run.py init                           # 生成默认配置文件\n"
        ),
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="配置文件路径 (YAML 格式)",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # --- chat ---
    sp_chat = subparsers.add_parser(
        "chat",
        help="启动交互式对话（默认模式）",
    )
    sp_chat.set_defaults(func=cmd_chat)

    # --- gui ---
    sp_gui = subparsers.add_parser(
        "gui",
        help="启动本地客户端界面",
    )
    sp_gui.add_argument(
        "--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1",
    )
    sp_gui.add_argument(
        "--port", type=int, default=8765, help="监听端口，默认 8765",
    )
    sp_gui.add_argument(
        "--no-browser", action="store_true", help="只启动服务，不自动打开浏览器",
    )
    sp_gui.set_defaults(func=cmd_gui)

    # --- extract ---
    sp_extract = subparsers.add_parser(
        "extract",
        help="快速提取指定联系人的聊天记录",
    )
    sp_extract.add_argument(
        "--contact", required=True, help="联系人名称、备注或微信ID",
    )
    sp_extract.add_argument(
        "--start", default=None, help="起始日期 (YYYY-MM-DD)",
    )
    sp_extract.add_argument(
        "--end", default=None, help="结束日期 (YYYY-MM-DD)",
    )
    sp_extract.set_defaults(func=cmd_extract)

    # --- export ---
    sp_export = subparsers.add_parser(
        "export",
        help="快速导出证据文件",
    )
    sp_export.add_argument(
        "--contact", required=True, help="联系人名称、备注或微信ID",
    )
    sp_export.add_argument(
        "--format", "-f", default="docx",
        choices=["docx", "html", "pdf"],
        help="导出格式 (默认 docx)",
    )
    sp_export.add_argument(
        "--output", "-o", default=None, help="输出路径（文件或目录）",
    )
    sp_export.set_defaults(func=cmd_export)

    # --- contacts ---
    sp_contacts = subparsers.add_parser(
        "contacts",
        help="列出或搜索联系人",
    )
    sp_contacts.add_argument(
        "--keyword", "-k", default=None, help="搜索关键词（可选）",
    )
    sp_contacts.set_defaults(func=cmd_contacts)

    # --- init ---
    sp_init = subparsers.add_parser(
        "init",
        help="生成默认配置文件",
    )
    sp_init.add_argument(
        "--output", "-o", default="config.yaml", help="配置文件输出路径",
    )
    sp_init.set_defaults(func=cmd_init)

    return parser


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    """命令行主入口。"""
    parser = build_parser()
    args = parser.parse_args()

    # 如果没有指定子命令，默认进入 chat 模式
    if args.command is None:
        args.host = "127.0.0.1"
        args.port = 8765
        args.no_browser = False
        args.func = cmd_gui

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n\033[33m已中断。\033[0m")
        sys.exit(0)
    except Exception as e:
        print(f"\n\033[31m程序出错：{e}\033[0m")
        sys.exit(1)


if __name__ == "__main__":
    main()
