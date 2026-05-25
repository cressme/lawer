"""
高级媒体处理模块

在 L1 提取层的基础上，对媒体文件进行高级处理：
1. 语音转文字 (Whisper)
2. 图片 OCR 文字识别 (PaddleOCR)
3. 视频关键帧提取
4. 批量媒体处理与结果汇总
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .message_parser import MessageType, ParsedMessage

logger = logging.getLogger(__name__)


class MediaProcessor:
    """
    高级媒体处理器

    在数据提取层 (L1) 已完成文件解密/格式转换的基础上，
    对媒体内容进行语义级处理：语音转文字、图片 OCR、视频关键帧提取等。

    用法示例::

        processor = MediaProcessor(output_dir=Path("./processed"))
        text = processor.voice_to_text(Path("voice.wav"))
        ocr_text = processor.image_ocr(Path("screenshot.jpg"))
        frames = processor.extract_video_keyframes(Path("video.mp4"))
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        """
        初始化媒体处理器。

        Args:
            output_dir: 处理结果的输出目录。不指定则使用临时目录。
        """
        if output_dir:
            self._output_dir = Path(output_dir)
        else:
            self._output_dir = Path(tempfile.mkdtemp(prefix="wechat_media_"))
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 语音转文字
    # ------------------------------------------------------------------

    def voice_to_text(
        self,
        voice_path: Path | str,
        *,
        engine: str = "whisper",
        language: str = "zh",
        model_size: str = "base",
    ) -> str:
        """
        将语音文件转换为文字。

        支持两种引擎：
        - "whisper": 使用 OpenAI Whisper 本地模型 (默认)
        - "whisper_api": 使用 OpenAI Whisper API (需要 API Key)

        Args:
            voice_path: 语音文件路径 (支持 wav/mp3/m4a 等格式)。
            engine: 转写引擎，可选 "whisper" 或 "whisper_api"。
            language: 语言代码，默认 "zh" (中文)。
            model_size: Whisper 本地模型大小，默认 "base"。
                        可选: tiny, base, small, medium, large。

        Returns:
            转写后的文字内容。

        Raises:
            FileNotFoundError: 语音文件不存在。
            RuntimeError: 转写引擎不可用或转写失败。
        """
        voice_path = Path(voice_path)
        if not voice_path.exists():
            raise FileNotFoundError(f"语音文件不存在: {voice_path}")

        if engine == "whisper":
            return self._voice_to_text_whisper_local(
                voice_path, language=language, model_size=model_size
            )
        elif engine == "whisper_api":
            return self._voice_to_text_whisper_api(
                voice_path, language=language
            )
        else:
            raise ValueError(f"不支持的语音转写引擎: {engine}")

    def _voice_to_text_whisper_local(
        self,
        voice_path: Path,
        *,
        language: str = "zh",
        model_size: str = "base",
    ) -> str:
        """使用本地 Whisper 模型进行语音转文字。"""
        try:
            import whisper
        except ImportError:
            raise RuntimeError(
                "需要安装 openai-whisper 库: pip install openai-whisper"
            )

        try:
            model = whisper.load_model(model_size)
            result = model.transcribe(
                str(voice_path),
                language=language,
                fp16=False,
            )
            text = result.get("text", "").strip()
            logger.info(
                "语音转文字完成 (whisper local): %s -> %d 字符",
                voice_path.name, len(text),
            )
            return text
        except Exception as e:
            raise RuntimeError(f"Whisper 本地转写失败: {e}") from e

    def _voice_to_text_whisper_api(
        self,
        voice_path: Path,
        *,
        language: str = "zh",
    ) -> str:
        """使用 OpenAI Whisper API 进行语音转文字。"""
        try:
            import openai
        except ImportError:
            raise RuntimeError(
                "需要安装 openai 库: pip install openai"
            )

        try:
            client = openai.OpenAI()
            with open(voice_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language,
                )
            text = transcript.text.strip()
            logger.info(
                "语音转文字完成 (whisper API): %s -> %d 字符",
                voice_path.name, len(text),
            )
            return text
        except Exception as e:
            raise RuntimeError(f"Whisper API 转写失败: {e}") from e

    # ------------------------------------------------------------------
    # 图片 OCR
    # ------------------------------------------------------------------

    def image_ocr(
        self,
        image_path: Path | str,
        *,
        lang: str = "ch",
    ) -> str:
        """
        对图片进行 OCR 文字识别。

        优先使用 RapidOCR（轻量、适合本地打包），回退到 PaddleOCR / pytesseract。

        Args:
            image_path: 图片文件路径。
            lang: 识别语言，默认 "ch" (中文)。
                  PaddleOCR: "ch", "en" 等。
                  Tesseract: "chi_sim", "eng" 等。

        Returns:
            识别出的文字内容，多行以换行符分隔。

        Raises:
            FileNotFoundError: 图片文件不存在。
            RuntimeError: OCR 引擎不可用。
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        # 优先使用 RapidOCR。PaddleOCR 3.x 在部分 Windows/Paddle 组合上
        # 会触发运行时错误，RapidOCR 更适合作为客户端默认 OCR。
        try:
            return self._ocr_with_rapidocr(image_path)
        except (ImportError, RuntimeError) as e:
            logger.debug("RapidOCR 不可用: %s，尝试 PaddleOCR", e)

        try:
            return self._ocr_with_paddle(image_path, lang=lang)
        except (ImportError, RuntimeError) as e:
            logger.debug("PaddleOCR 不可用: %s，尝试 pytesseract", e)

        # 回退到 pytesseract
        try:
            return self._ocr_with_tesseract(image_path, lang=lang)
        except (ImportError, RuntimeError) as e:
            logger.debug("pytesseract 不可用: %s", e)

        raise RuntimeError(
            "OCR 引擎不可用。请安装 paddleocr (pip install paddleocr paddlepaddle) "
            "或 rapidocr-onnxruntime / pytesseract。"
        )

    def _ocr_with_rapidocr(self, image_path: Path) -> str:
        """使用 RapidOCR/ONNXRuntime 进行文字识别。"""
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        result, _ = engine(str(image_path))
        lines: List[str] = []
        for item in result or []:
            if not item or len(item) < 2:
                continue
            text = item[1]
            if text:
                lines.append(str(text))

        text = "\n".join(lines)
        logger.info(
            "OCR 完成 (RapidOCR): %s -> %d 行",
            image_path.name, len(lines),
        )
        return text

    def _ocr_with_paddle(
        self, image_path: Path, *, lang: str = "ch"
    ) -> str:
        """使用 PaddleOCR 进行文字识别。"""
        from paddleocr import PaddleOCR

        try:
            ocr = PaddleOCR(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
            )
        except ValueError:
            ocr = PaddleOCR(use_angle_cls=True, lang=lang)

        try:
            result = ocr.predict(str(image_path)) if hasattr(ocr, "predict") else ocr.ocr(str(image_path), cls=True)
        except TypeError:
            result = ocr.ocr(str(image_path))

        lines: List[str] = []
        self._collect_paddle_text(result, lines)

        text = "\n".join(lines)
        logger.info(
            "OCR 完成 (PaddleOCR): %s -> %d 行",
            image_path.name, len(lines),
        )
        return text

    def _collect_paddle_text(self, value: object, lines: List[str]) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key in ("rec_texts", "texts"):
                texts = value.get(key)
                if isinstance(texts, list):
                    lines.extend(str(text) for text in texts if text)
                    return
            for item in value.values():
                self._collect_paddle_text(item, lines)
            return
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[1], (list, tuple)) and value[1]:
                lines.append(str(value[1][0]))
                return
            for item in value:
                self._collect_paddle_text(item, lines)

    def _ocr_with_tesseract(
        self, image_path: Path, *, lang: str = "ch"
    ) -> str:
        """使用 pytesseract 进行文字识别。"""
        import pytesseract
        from PIL import Image

        # 语言代码映射：PaddleOCR -> Tesseract
        lang_map = {"ch": "chi_sim", "en": "eng"}
        tess_lang = lang_map.get(lang, lang)

        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=tess_lang)
        text = text.strip()

        logger.info(
            "OCR 完成 (tesseract): %s -> %d 字符",
            image_path.name, len(text),
        )
        return text

    # ------------------------------------------------------------------
    # 视频关键帧提取
    # ------------------------------------------------------------------

    def extract_video_keyframes(
        self,
        video_path: Path | str,
        *,
        interval: int = 5,
        max_frames: int = 50,
    ) -> List[Path]:
        """
        从视频中按固定间隔提取关键帧图片。

        使用 ffmpeg 命令行工具提取指定间隔的帧画面。

        Args:
            video_path: 视频文件路径。
            interval: 提取间隔（秒），默认每 5 秒一帧。
            max_frames: 最大提取帧数，默认 50 帧，防止过大视频产生过多文件。

        Returns:
            提取的帧图片文件路径列表。

        Raises:
            FileNotFoundError: 视频文件不存在。
            RuntimeError: ffmpeg 不可用或提取失败。
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 创建帧输出目录
        frames_dir = self._output_dir / f"frames_{video_path.stem}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        output_pattern = str(frames_dir / "frame_%04d.jpg")

        try:
            # 使用 ffmpeg 按间隔提取帧
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(max_frames),
                "-q:v", "2",  # JPEG 质量
                output_pattern,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg 执行失败 (code={result.returncode}): "
                    f"{result.stderr[:500]}"
                )

        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg 未安装或不在 PATH 中。"
                "请安装 ffmpeg: https://ffmpeg.org/download.html"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg 执行超时（超过 120 秒）")

        # 收集输出的帧文件
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        logger.info(
            "视频关键帧提取完成: %s -> %d 帧 (间隔 %ds)",
            video_path.name, len(frames), interval,
        )
        return frames

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------

    def process_all_media(
        self,
        messages: List[ParsedMessage],
        output_dir: Optional[Path | str] = None,
    ) -> Dict[int, Dict[str, Any]]:
        """
        批量处理消息列表中的所有媒体内容。

        遍历消息列表，对包含媒体文件的消息执行对应的处理：
        - 语音消息 -> 语音转文字
        - 图片消息 -> OCR 文字识别
        - 视频消息 -> 关键帧提取

        Args:
            messages: 解析后的消息列表。
            output_dir: 处理结果输出目录，不指定则使用默认目录。

        Returns:
            处理结果映射: {msg_id: {"type": ..., "result": ..., "error": ...}}
        """
        if output_dir:
            original_dir = self._output_dir
            self._output_dir = Path(output_dir)
            self._output_dir.mkdir(parents=True, exist_ok=True)

        results: Dict[int, Dict[str, Any]] = {}
        processed = 0
        errors = 0

        for msg in messages:
            if not msg.media_path:
                continue

            media_path = Path(msg.media_path)
            if not media_path.exists():
                logger.debug(
                    "媒体文件不存在，跳过 (msg_id=%s): %s",
                    msg.msg_id, media_path,
                )
                continue

            result: Dict[str, Any] = {"type": msg.msg_type.name}

            try:
                if msg.msg_type == MessageType.VOICE:
                    text = self.voice_to_text(media_path)
                    result["result"] = text
                    result["transcript"] = text

                elif msg.msg_type == MessageType.IMAGE:
                    ocr_text = self.image_ocr(media_path)
                    result["result"] = ocr_text
                    result["ocr_text"] = ocr_text

                elif msg.msg_type == MessageType.VIDEO:
                    frames = self.extract_video_keyframes(media_path)
                    result["result"] = [str(f) for f in frames]
                    result["keyframes"] = [str(f) for f in frames]
                    result["frame_count"] = len(frames)

                else:
                    continue

                processed += 1

            except Exception as e:
                result["error"] = str(e)
                errors += 1
                logger.warning(
                    "媒体处理失败 (msg_id=%s, type=%s): %s",
                    msg.msg_id, msg.msg_type.name, e,
                )

            results[msg.msg_id] = result

        if output_dir:
            self._output_dir = original_dir

        logger.info(
            "批量媒体处理完成: 处理 %d, 失败 %d, 总计 %d 条媒体消息",
            processed, errors, processed + errors,
        )
        return results
