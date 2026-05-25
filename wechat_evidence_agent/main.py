"""
微信证据助手 - 主应用入口

将所有模块（提取、处理、分析、证据生成、智能代理）
组装为统一的应用程序，提供交互式命令行和快捷操作接口。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from getpass import getpass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI 终端颜色
# ---------------------------------------------------------------------------

class _C:
    """ANSI 转义码常量，用于终端彩色输出。"""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    # 前景色
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    # 高亮前景
    BRIGHT_GREEN  = "\033[92m"
    BRIGHT_CYAN   = "\033[96m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_WHITE  = "\033[97m"


# ---------------------------------------------------------------------------
# 启动横幅
# ---------------------------------------------------------------------------

BANNER = f"""\
{_C.CYAN}{_C.BOLD}
  ╔══════════════════════════════════════════════════════╗
  ║                                                      ║
  ║        微 信 证 据 助 手  v1.0                       ║
  ║        WeChat Evidence Agent                         ║
  ║                                                      ║
  ║   为律师打造的微信电子证据智能分析工具               ║
  ║   支持 Windows / macOS                               ║
  ║                                                      ║
  ╚══════════════════════════════════════════════════════╝
{_C.RESET}"""

HELP_TEXT = f"""\
{_C.BRIGHT_YELLOW}{_C.BOLD}可用命令：{_C.RESET}
  {_C.GREEN}/help{_C.RESET}     显示此帮助信息
  {_C.GREEN}/quit{_C.RESET}     退出程序
  {_C.GREEN}/reset{_C.RESET}    重置对话（清除历史，保留案件记忆）
  {_C.GREEN}/export{_C.RESET}   导出当前证据清单 (docx)
  {_C.GREEN}/save{_C.RESET}     保存当前案件记忆到文件
  {_C.GREEN}/status{_C.RESET}   查看当前案件状态和证据标记

{_C.BRIGHT_YELLOW}{_C.BOLD}使用示例：{_C.RESET}
  {_C.DIM}> 帮我提取与张三从2024年1月到3月的聊天记录
  > 搜索包含"借款"的消息
  > 将转账记录标记为借款凭证
  > 分析证据链完整性
  > 生成证据清单{_C.RESET}
"""


# ---------------------------------------------------------------------------
# 主应用类
# ---------------------------------------------------------------------------

class WeChatEvidenceApp:
    """
    微信证据助手主应用

    将所有子模块组装为统一的应用程序实例，提供：
    - 交互式命令行对话界面
    - 快捷提取 / 导出接口
    - 模块间依赖注入与生命周期管理
    """

    def __init__(self, config: Config = None) -> None:
        """
        初始化应用，创建并连接所有模块。

        Args:
            config: 配置对象。不传则使用默认配置（自动读取环境变量）。
        """
        self.config = config or Config.get_default_config()
        self._setup_logging()

        # --- L1: 数据提取层 ---
        from .extractor import WeChatDBExtractor, MediaExtractor, ContactManager

        wechat_dir = Path(self.config.wechat_dir) if self.config.wechat_dir else None
        self.db_extractor = WeChatDBExtractor(wechat_dir=wechat_dir)
        self.media_extractor = MediaExtractor(
            output_dir=self.config.output_path / "media",
            image_aes_key=self.config.wechat_image_aes_key,
        )
        self.contact_manager = ContactManager()

        # --- L2: 数据处理层 ---
        from .processor import MessageParser, TimelineBuilder, MediaProcessor

        self.message_parser = MessageParser()
        self.timeline_builder = TimelineBuilder()
        self.media_processor = MediaProcessor(
            output_dir=self.config.output_path / "processed"
        )

        # --- L4: 证据生成层 ---
        from .evidence import EvidenceListGenerator, ChatRenderer, EvidenceExporter

        self.evidence_generator = EvidenceListGenerator()
        self.chat_renderer = ChatRenderer()
        self.evidence_exporter = EvidenceExporter()

        # --- L5: AI 分析层 ---
        from .analyzer import FactExtractor, LegalAnalyzer

        llm_api_key = self.config.openai_api_key or "__missing_api_key__"

        self.fact_extractor = FactExtractor(
            api_key=llm_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
        )
        self.legal_analyzer = LegalAnalyzer(
            api_key=llm_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
        )

        # --- L3: 智能代理层 ---
        from .agent import EvidenceAgent

        agent_config: Dict[str, Any] = {
            "api_key": self.config.openai_api_key,
            "model": self.config.openai_model,
            "base_url": self.config.openai_base_url,
        }
        self.agent = EvidenceAgent(config=agent_config)

        # 注入底层模块到 Agent
        self.agent.set_modules(
            extractor=self.db_extractor,
            processor=self.message_parser,
            evidence_generator=self.evidence_generator,
            analyzer=self.legal_analyzer,
        )

        from .agent_graph import LangGraphEvidenceAgent

        self.graph_agent = LangGraphEvidenceAgent(
            config=self.config,
            extractor=self.db_extractor,
            media_extractor=self.media_extractor,
            media_processor=self.media_processor,
        )

        logger.info("WeChatEvidenceApp 初始化完成")

    # ------------------------------------------------------------------
    # 日志配置
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        """根据配置设置日志级别和格式。"""
        level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    # ------------------------------------------------------------------
    # 交互式命令行
    # ------------------------------------------------------------------

    def start_interactive(self) -> None:
        """
        启动交互式对话循环。

        律师通过终端与 Agent 对话，支持自然语言指令和斜杠命令。
        输入 /quit 退出。
        """
        print(BANNER)
        print(f"{_C.DIM}输入 /help 查看可用命令，输入 /quit 退出。{_C.RESET}")
        print()

        while True:
            try:
                user_input = input(f"{_C.BRIGHT_GREEN}{_C.BOLD}律师 > {_C.RESET}")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{_C.DIM}再见！{_C.RESET}")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # 处理斜杠命令
            if user_input.startswith("/"):
                should_continue = self._handle_command(user_input)
                if not should_continue:
                    break
                continue

            # 正常对话
            print(f"{_C.DIM}正在思考...{_C.RESET}", end="\r")
            try:
                response = self.agent.chat(user_input)
            except Exception as e:
                response = f"发生错误：{e}"
                logger.error("Agent 对话出错", exc_info=True)

            # 清除 "正在思考..." 行
            print(" " * 40, end="\r")
            print(f"{_C.BRIGHT_CYAN}{_C.BOLD}助手 > {_C.RESET}{response}")
            print()

    def _handle_command(self, command: str) -> bool:
        """
        处理斜杠命令。

        Args:
            command: 以 "/" 开头的命令字符串。

        Returns:
            True 表示继续循环，False 表示退出。
        """
        cmd = command.lower().split()[0]

        if cmd in ("/quit", "/exit", "/q"):
            print(f"\n{_C.YELLOW}感谢使用微信证据助手，再见！{_C.RESET}")
            return False

        elif cmd == "/help":
            print(HELP_TEXT)

        elif cmd == "/reset":
            self.agent.reset()
            print(f"{_C.YELLOW}对话历史已重置。案件记忆保留。{_C.RESET}")
            print()

        elif cmd == "/export":
            self._cmd_export()

        elif cmd == "/save":
            self._cmd_save()

        elif cmd == "/status":
            self._cmd_status()

        elif cmd == "/config":
            self._cmd_config()

        elif cmd in ("/wechat-dir", "/wechatdir"):
            parts = command.split(maxsplit=1)
            self._cmd_wechat_dir(parts[1] if len(parts) > 1 else "")

        else:
            print(f"{_C.RED}未知命令: {cmd}。输入 /help 查看可用命令。{_C.RESET}")
            print()

        return True

    # ------------------------------------------------------------------
    # 斜杠命令实现
    # ------------------------------------------------------------------

    def _config_file_path(self) -> Path:
        """Return the user-editable config path next to the executable/script."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "config.yaml"
        return Path.cwd() / "config.yaml"

    def _cmd_config(self) -> None:
        """Configure and persist the OpenAI-compatible LLM settings."""
        print(f"{_C.BRIGHT_YELLOW}{_C.BOLD}=== 大模型配置 ==={_C.RESET}")
        print("直接回车会保留当前值。默认推荐 DeepSeek。")

        current_provider = (
            "deepseek"
            if self.config.openai_base_url == "https://api.deepseek.com"
            else "custom"
        )
        provider = input(
            f"服务商 [deepseek/openai/custom] ({current_provider}): "
        ).strip().lower() or current_provider

        if provider == "deepseek":
            default_base_url = "https://api.deepseek.com"
            default_model = "deepseek-chat"
        elif provider == "openai":
            default_base_url = ""
            default_model = "gpt-4o"
        else:
            default_base_url = self.config.openai_base_url or ""
            default_model = self.config.openai_model or "deepseek-chat"

        base_url = input(
            f"Base URL ({default_base_url or 'OpenAI 默认'}): "
        ).strip()
        model = input(f"模型名称 ({default_model}): ").strip()

        masked = "***已设置***" if self.config.openai_api_key else "未设置"
        api_key = getpass(f"API Key ({masked}，留空保持不变): ").strip()

        self.config.openai_base_url = base_url or default_base_url or None
        self.config.openai_model = model or default_model
        if api_key:
            api_key = "".join(api_key.split())
            if api_key.count("sk-") > 1:
                print(f"{_C.RED}API Key looks duplicated. Please run /config again and paste it only once.{_C.RESET}")
                print()
                return
            self.config.openai_api_key = api_key

        self._reload_llm_clients()

        config_path = self._config_file_path()
        self.config.save_to_file(config_path, redact_secrets=False)

        print(f"{_C.GREEN}配置已保存：{config_path}{_C.RESET}")
        print(f"{_C.GREEN}当前模型：{self.config.openai_model}{_C.RESET}")
        print()

    def _cmd_wechat_dir(self, path: str = "") -> None:
        """Configure and persist the local WeChat data directory."""
        if not path:
            path = input("微信数据目录路径: ").strip()
        path = path.strip().strip('"')
        if not path:
            print(f"{_C.YELLOW}已取消。{_C.RESET}")
            print()
            return

        try:
            resolved = self.db_extractor._resolve_wechat_dir(Path(path))
        except Exception as e:
            print(f"{_C.RED}设置失败：{e}{_C.RESET}")
            print()
            return

        self.db_extractor._wechat_dir = resolved
        self.config.wechat_dir = str(resolved)
        self.config.save_to_file(self._config_file_path(), redact_secrets=False)
        print(f"{_C.GREEN}微信数据目录已设置：{resolved}{_C.RESET}")
        print()

    def _reload_llm_clients(self) -> None:
        """Refresh LLM-backed modules after config changes."""
        from .analyzer import FactExtractor, LegalAnalyzer

        llm_api_key = self.config.openai_api_key or "__missing_api_key__"
        self.fact_extractor = FactExtractor(
            api_key=llm_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
        )
        self.legal_analyzer = LegalAnalyzer(
            api_key=llm_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
        )
        self.agent.configure_llm(
            api_key=self.config.openai_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
        )
        self.agent.set_modules(analyzer=self.legal_analyzer)
        if hasattr(self, "graph_agent"):
            self.graph_agent.configure(self.config)

    def _cmd_export(self) -> None:
        """执行 /export 命令：导出证据清单。"""
        output_dir = self.config.output_path / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"evidence_{timestamp}"
        output_file = output_dir / f"{filename}.docx"

        try:
            marks = self.agent.memory.get_evidence_marks()
            if not marks:
                print(f"{_C.YELLOW}当前没有已标记的证据，无法导出。请先标记关键消息。{_C.RESET}")
                print()
                return

            # 通过 Agent 的工具执行器调用导出
            result = self.agent.tool_executor.execute(
                "export_document",
                {"format": "docx", "filename": filename},
            )
            print(f"{_C.GREEN}导出完成：{result}{_C.RESET}")
        except Exception as e:
            print(f"{_C.RED}导出失败：{e}{_C.RESET}")
        print()

    def _cmd_save(self) -> None:
        """执行 /save 命令：保存案件记忆。"""
        output_dir = self.config.output_path / "sessions"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = output_dir / f"case_memory_{timestamp}.json"

        try:
            self.agent.memory.save_to_file(save_path)
            print(f"{_C.GREEN}案件记忆已保存至：{save_path}{_C.RESET}")
        except Exception as e:
            print(f"{_C.RED}保存失败：{e}{_C.RESET}")
        print()

    def _cmd_status(self) -> None:
        """执行 /status 命令：显示当前状态。"""
        summary = self.agent.memory.get_case_summary()
        history_count = len(self.agent.get_history()) - 1  # 排除系统提示词

        print(f"{_C.BRIGHT_YELLOW}{_C.BOLD}=== 当前状态 ==={_C.RESET}")
        print(f"  模型：{self.config.openai_model}")
        print(f"  对话轮数：{history_count}")
        print()
        print(summary)
        print()

    # ------------------------------------------------------------------
    # 快捷接口
    # ------------------------------------------------------------------

    def quick_extract(
        self,
        contact: str,
        start_date: str = None,
        end_date: str = None,
    ) -> dict:
        """
        快速提取指定联系人的聊天记录（不经过 Agent）。

        Args:
            contact: 联系人名称、备注或微信ID。
            start_date: 起始日期，格式 YYYY-MM-DD，可选。
            end_date: 结束日期，格式 YYYY-MM-DD，可选。

        Returns:
            包含以下字段的字典：
            - contact: 联系人标识
            - messages: 原始消息列表
            - parsed: 解析后的消息列表
            - timeline: 时间线统计摘要
            - count: 消息总数
        """
        # 解析联系人 ID
        contact_id = self.agent.tool_executor._resolve_contact_id(contact)
        if contact_id is None:
            return {
                "contact": contact,
                "error": f"未找到联系人：{contact}",
                "messages": [],
                "parsed": [],
                "count": 0,
            }

        # 提取消息
        messages = self.db_extractor.get_messages(
            contact_id=contact_id,
            start_date=start_date,
            end_date=end_date,
        )

        # 解析消息
        parsed = self.message_parser.parse_messages(messages)

        # 构建时间线统计
        timeline = self.timeline_builder.build_timeline(parsed)
        stats = self.timeline_builder.get_summary_stats(timeline)

        return {
            "contact": contact,
            "contact_id": contact_id,
            "messages": messages,
            "parsed": parsed,
            "timeline": stats,
            "count": len(messages),
        }

    def quick_export(
        self,
        contact: str,
        output_path: str,
        format: str = "docx",
    ) -> str:
        """
        快速导出指定联系人的聊天证据。

        提取聊天记录后直接导出为指定格式的文件。

        Args:
            contact: 联系人名称、备注或微信ID。
            output_path: 输出文件路径或目录。
            format: 导出格式，支持 docx / html / pdf。

        Returns:
            导出文件的完整路径。

        Raises:
            ValueError: 联系人不存在或无聊天记录。
        """
        # 先提取数据
        data = self.quick_extract(contact)
        if data.get("error"):
            raise ValueError(data["error"])
        if not data["messages"]:
            raise ValueError(f"与「{contact}」没有聊天记录。")

        # 确定输出路径
        output = Path(output_path)
        if output.is_dir():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"evidence_{contact}_{timestamp}.{format}"
            output = output / filename
        output.parent.mkdir(parents=True, exist_ok=True)

        # 使用 Agent 工具执行器导出
        result = self.agent.tool_executor.execute(
            "export_document",
            {"format": format, "filename": str(output.with_suffix("").name)},
        )

        return str(output)
