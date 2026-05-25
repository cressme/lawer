"""Image evidence decoding and OCR helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List

from PIL import Image


def inspect_image_evidence(
    image_attachments: Iterable[Dict[str, Any]],
    *,
    media_extractor: Any = None,
    media_processor: Any = None,
    output_dir: Path | str | None = None,
    max_images: int = 64,
) -> List[Dict[str, Any]]:
    """Decode WeChat image attachments and optionally OCR them.

    Every image attachment returns a result object, even if decoding or OCR
    fails. The UI and analysis node can then explain what was found and what
    still needs the WeChat image key.
    """
    output_root = Path(output_dir or Path.cwd() / "output" / "image_evidence")
    output_root.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    processed_msg_ids: set[str] = set()

    items = list(image_attachments)
    thumbnails_by_msg = {
        str(item.get("msg_id") or ""): item
        for item in items
        if _attachment_type(item) == "thumbnail" and item.get("msg_id")
    }

    for item in items:
        if len(results) >= max_images:
            break
        if _attachment_type(item) != "image":
            continue
        source = str(item.get("path") or "")
        if not source or source in seen_paths:
            continue
        seen_paths.add(source)
        msg_id = str(item.get("msg_id") or "")

        result = inspect_single_image(
            item,
            media_extractor=media_extractor,
            media_processor=media_processor,
            output_dir=output_root,
        )
        if result.get("status") == "decode_failed":
            thumb = thumbnails_by_msg.get(str(item.get("msg_id") or ""))
            if thumb:
                thumb_result = inspect_single_image(
                    thumb,
                    media_extractor=media_extractor,
                    media_processor=media_processor,
                    output_dir=output_root,
                )
                result["thumbnail_evidence"] = thumb_result
                if thumb_result.get("status") == "decoded":
                    result["status"] = "thumbnail_decoded"
                    result["thumbnail_used"] = True
        results.append(result)
        if msg_id:
            processed_msg_ids.add(msg_id)

    # Some WeChat image messages have a thumbnail record but no usable original
    # attachment in the DB snapshot. Keep one preview per message so the UI does
    # not silently hide those image messages.
    for item in items:
        if len(results) >= max_images:
            break
        if _attachment_type(item) != "thumbnail":
            continue
        msg_id = str(item.get("msg_id") or "")
        if msg_id and msg_id in processed_msg_ids:
            continue
        source = str(item.get("path") or "")
        if not source or source in seen_paths:
            continue
        seen_paths.add(source)

        result = inspect_single_image(
            item,
            media_extractor=media_extractor,
            media_processor=media_processor,
            output_dir=output_root,
        )
        result["thumbnail_used"] = True
        if result.get("status") == "decoded":
            result["status"] = "thumbnail_decoded"
        results.append(result)
        if msg_id:
            processed_msg_ids.add(msg_id)
    return results


def _attachment_type(item: Dict[str, Any]) -> str:
    kind = str(item.get("type") or "").strip().lower()
    if kind in {"image", "thumbnail"}:
        return kind
    name = str(item.get("name") or item.get("path") or "").lower()
    if name.endswith("_t.dat") or "_thumb" in name:
        return "thumbnail"
    if name.endswith((".dat", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")):
        return "image"
    return kind or "attachment"


def inspect_single_image(
    attachment: Dict[str, Any],
    *,
    media_extractor: Any = None,
    media_processor: Any = None,
    output_dir: Path,
) -> Dict[str, Any]:
    source = Path(str(attachment.get("path") or ""))
    result: Dict[str, Any] = {
        "source_path": str(source),
        "source_name": attachment.get("name") or source.name,
        "msg_id": attachment.get("msg_id", ""),
        "time": attachment.get("time", ""),
        "sender": attachment.get("sender", ""),
        "source_kind": attachment.get("kind", ""),
        "source_type": _attachment_type(attachment),
        "status": "pending",
        "decoded_path": "",
        "format": "",
        "width": None,
        "height": None,
        "sha256": "",
        "ocr_text": "",
        "ocr_status": "not_run",
        "error": "",
    }

    if not source.exists():
        result.update({"status": "missing", "error": "源图片文件不存在"})
        return result

    try:
        if media_extractor and source.suffix.lower() == ".dat":
            decoded_path = output_dir / f"{source.stem}.decoded"
            decoded_path = media_extractor.decrypt_image_to_file(source, decoded_path)
        else:
            decoded_path = source
        result["decoded_path"] = str(decoded_path)
        result["sha256"] = sha256_file(decoded_path)

        with Image.open(decoded_path) as image:
            result["format"] = image.format or ""
            result["width"], result["height"] = image.size
        result["status"] = "decoded"
    except Exception as exc:
        result.update({"status": "decode_failed", "error": str(exc)})
        return result

    if media_processor:
        try:
            text = media_processor.image_ocr(result["decoded_path"])
            result["ocr_text"] = text.strip()
            result["ocr_status"] = "ok" if result["ocr_text"] else "empty"
        except Exception as exc:
            result["ocr_status"] = "failed"
            result["ocr_error"] = str(exc)

    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_image_evidence_for_llm(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "图片证据识别：未识别到可处理的原始图片文件。"

    decoded_count = sum(1 for item in items if item.get("status") in {"decoded", "thumbnail_decoded"})
    ocr_count = sum(1 for item in items if item.get("ocr_text"))
    failed_count = sum(1 for item in items if item.get("status") == "decode_failed")
    lines = [
        "图片证据识别结果：",
        f"共发现图片 {len(items)} 张，已解码/可预览 {decoded_count} 张，OCR 有文本 {ocr_count} 张，解码失败 {failed_count} 张。",
    ]
    needs_aes_guidance = False

    for idx, item in enumerate(items, 1):
        lines.append(
            f"{idx}. [{item.get('time')}] {item.get('sender')} "
            f"{item.get('source_name')} 状态={item.get('status')} "
            f"格式={item.get('format') or '未知'} 尺寸={item.get('width')}x{item.get('height')}"
        )
        if item.get("decoded_path"):
            lines.append(f"   解码文件：{item.get('decoded_path')}")
        if item.get("sha256"):
            lines.append(f"   SHA256：{item.get('sha256')}")
        if item.get("ocr_text"):
            lines.append(f"   OCR：{item.get('ocr_text')[:500]}")
        elif item.get("ocr_status") == "failed":
            lines.append(f"   OCR失败：{item.get('ocr_error', '')}")
        if item.get("error"):
            lines.append(f"   错误：{item.get('error')}")
            if "新版图片 AES Key" in str(item.get("error")):
                needs_aes_guidance = True

        thumb = item.get("thumbnail_evidence") or {}
        if thumb:
            lines.append(
                f"   缩略图兜底：状态={thumb.get('status')} "
                f"格式={thumb.get('format') or '未知'} 尺寸={thumb.get('width')}x{thumb.get('height')}"
            )
            if thumb.get("decoded_path"):
                lines.append(f"   缩略图解码文件：{thumb.get('decoded_path')}")
            if thumb.get("ocr_text"):
                lines.append(f"   缩略图OCR：{thumb.get('ocr_text')[:500]}")
            if thumb.get("error"):
                lines.append(f"   缩略图错误：{thumb.get('error')}")
                if "新版图片 AES Key" in str(thumb.get("error")):
                    needs_aes_guidance = True

    if needs_aes_guidance:
        lines.extend([
            "",
            "图片识别操作建议：检测到新版微信图片 AES Key 未获取。",
            "请提示用户在微信客户端中打开/预览最近一张或目标聊天图片，保持微信不关闭，然后回到本软件重新识别图片或重新分析。",
            "这一步的目的，是让微信把图片解密 key 加载到内存，软件随后会再次尝试自动提取。",
        ])
    return "\n".join(lines)
