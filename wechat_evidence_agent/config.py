"""
WeChat Evidence Agent - 中央配置模块

支持从 YAML 文件或环境变量加载配置，提供统一的配置管理接口。
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 证据模板设置
# ---------------------------------------------------------------------------

@dataclass
class EvidenceTemplateSettings:
    """证据文书模板配置"""

    court_name: str = ""
    """受理法院名称，例如 '北京市朝阳区人民法院'"""

    case_number: str = ""
    """案号，例如 '(2026)京0105民初1234号'"""

    lawyer_name: str = ""
    """代理律师姓名"""

    law_firm: str = ""
    """律师事务所名称"""

    plaintiff: str = ""
    """原告姓名"""

    defendant: str = ""
    """被告姓名"""

    notary_office: str = ""
    """公证处名称（如适用）"""


# ---------------------------------------------------------------------------
# 主配置
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """
    WeChat Evidence Agent 中央配置。

    优先级：代码直接赋值 > 环境变量 > 配置文件 > 默认值。
    """

    # --- 平台 ---
    detected_platform: str = field(default_factory=lambda: platform.system())
    """自动检测的操作系统平台 (Windows / Darwin / Linux)"""

    # --- OpenAI / LLM ---
    openai_api_key: str = ""
    """OpenAI API 密钥，也可通过 OPENAI_API_KEY 环境变量设置"""

    openai_base_url: Optional[str] = None
    """自定义 API 端点（兼容 OpenAI 接口的第三方服务）"""

    openai_model: str = "gpt-4o"
    """使用的模型名称"""

    # --- 路径 ---
    wechat_dir: Optional[str] = None
    """微信数据目录，覆盖自动检测路径"""

    output_dir: str = "./output"
    """输出目录"""

    # --- 媒体处理 ---
    whisper_model: str = "base"
    """Whisper 语音识别模型（tiny/base/small/medium/large）"""

    wechat_image_aes_key: str = ""
    """微信新版图片 AES Key。通常自动提取，必要时可手动配置。"""

    # --- 语言 ---
    language: str = "zh"
    """主要语言（zh/en）"""

    # --- 证据模板 ---
    evidence_template: EvidenceTemplateSettings = field(
        default_factory=EvidenceTemplateSettings
    )

    # --- 高级 ---
    max_concurrent_requests: int = 5
    """LLM 最大并发请求数"""

    request_timeout: int = 120
    """单次 LLM 请求超时（秒）"""

    log_level: str = "INFO"
    """日志级别"""

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Config:
        """
        获取默认配置，自动从环境变量填充敏感字段。

        Returns:
            预填充环境变量的 Config 实例
        """
        cfg = cls()
        cfg._apply_env_vars()
        return cfg

    @classmethod
    def load_from_file(cls, path: str | Path) -> Config:
        """
        从 YAML 配置文件加载。

        Args:
            path: YAML 文件路径

        Returns:
            Config 实例

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式错误
        """
        import yaml  # 延迟导入，非核心依赖

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式错误，期望字典，得到 {type(raw).__name__}")

        cfg = cls._from_dict(raw)
        # 环境变量覆盖文件配置
        cfg._apply_env_vars()
        return cfg

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def save_to_file(self, path: str | Path, redact_secrets: bool = True) -> None:
        """
        将当前配置保存到 YAML 文件。

        Args:
            path: 目标文件路径
        """
        import yaml

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = asdict(self)
        # 不保存 API 密钥到文件
        if redact_secrets and data.get("openai_api_key"):
            data["openai_api_key"] = "***REDACTED***"

        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(
                data,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        logger.info("配置已保存到 %s", path)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _apply_env_vars(self) -> None:
        """从环境变量覆盖配置字段。"""
        env_mapping = {
            "OPENAI_API_KEY": "openai_api_key",
            "OPENAI_BASE_URL": "openai_base_url",
            "OPENAI_MODEL": "openai_model",
            "WECHAT_DIR": "wechat_dir",
            "WECHAT_EVIDENCE_OUTPUT_DIR": "output_dir",
            "WHISPER_MODEL": "whisper_model",
            "WECHAT_IMAGE_AES_KEY": "wechat_image_aes_key",
            "WECHAT_EVIDENCE_LANGUAGE": "language",
            "WECHAT_EVIDENCE_LOG_LEVEL": "log_level",
        }
        for env_key, attr in env_mapping.items():
            value = os.environ.get(env_key)
            if value is not None:
                setattr(self, attr, value)

    @classmethod
    def _from_dict(cls, data: dict) -> Config:
        """从字典构建 Config，支持嵌套的 evidence_template。"""
        template_data = data.pop("evidence_template", None)
        template = (
            EvidenceTemplateSettings(**template_data)
            if isinstance(template_data, dict)
            else EvidenceTemplateSettings()
        )

        # 只取 Config 已知的字段，忽略多余键
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        filtered["evidence_template"] = template

        return cls(**filtered)

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------

    @property
    def output_path(self) -> Path:
        """返回 output_dir 的 Path 对象，确保目录存在。"""
        p = Path(self.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def __repr__(self) -> str:
        key_display = "***" if self.openai_api_key else "(未设置)"
        return (
            f"Config(model={self.openai_model!r}, "
            f"api_key={key_display}, "
            f"output_dir={self.output_dir!r}, "
            f"language={self.language!r})"
        )
