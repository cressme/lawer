"""微信证据助手 - 安装脚本"""

from setuptools import setup, find_packages
from pathlib import Path

# 读取版本号
version = "1.0.0"

# 读取 README（如果存在）
long_description = "微信证据助手 - 为律师打造的微信电子证据智能分析工具"
readme_path = Path(__file__).parent / "README.md"
if readme_path.exists():
    long_description = readme_path.read_text(encoding="utf-8")

# 核心依赖（不含平台特定或可选依赖）
install_requires = [
    "openai>=1.0",
    "pyyaml>=6.0",
    "python-docx>=0.8.11",
    "Pillow>=9.0",
    "langgraph>=1.2",
    "langchain-core>=1.4",
    "langchain-openai>=1.2",
    "pydantic>=2.7",
]

# 可选依赖分组
extras_require = {
    # Windows 平台数据提取
    "extract": [
        "pymem>=1.13",
        "pysqlcipher3>=1.2.0",
        "pycryptodome>=3.20",
    ],
    # 语音处理
    "voice": [
        "openai-whisper>=20230918",
        "pilk>=0.2.0",
    ],
    # OCR 文字识别
    "ocr": [
        "paddleocr>=2.7.0",
    ],
    # 完整安装
    "all": [
        "pymem>=1.13",
        "pysqlcipher3>=1.2.0",
        "pycryptodome>=3.20",
        "openai-whisper>=20230918",
        "pilk>=0.2.0",
        "paddleocr>=2.7.0",
    ],
}

setup(
    name="wechat-evidence-agent",
    version=version,
    description="微信证据助手 - 为律师打造的微信电子证据智能分析工具",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="WeChat Evidence Agent Team",
    python_requires=">=3.9",
    packages=find_packages(),
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        "console_scripts": [
            "wechat-evidence=run:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Legal Industry",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Office/Business",
        "Natural Language :: Chinese (Simplified)",
    ],
)
