"""
媒体文件提取与解密模块

处理微信本地存储的加密媒体文件：
1. 图片解密：.dat 文件使用 XOR 加密，通过文件头特征推断密钥
2. 语音提取：.silk 格式转换为 .wav/.mp3
3. 视频提取：.mp4 文件直接拷贝
4. 附件提取：其他文件类型的提取

支持 Windows 和 macOS 平台上的微信客户端本地文件。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量：已知图片文件格式的魔术字节（文件头特征）
# ---------------------------------------------------------------------------

# 已知文件头 -> (扩展名, 描述)
KNOWN_HEADERS: dict[bytes, tuple[str, str]] = {
    b"\xFF\xD8\xFF": (".jpg", "JPEG"),
    b"\x89\x50\x4E\x47": (".png", "PNG"),
    b"\x47\x49\x46\x38": (".gif", "GIF"),
    b"\x52\x49\x46\x46": (".webp", "WebP"),  # RIFF 头，WebP 文件
    b"\x42\x4D": (".bmp", "BMP"),
    b"wxgf": (".hevc", "WeChat HEVC"),
}

WECHAT_V4_SIGNATURES = {
    b"\x07\x08V1\x08\x07": 1,
    b"\x07\x08V2\x08\x07": 2,
}

# silk 语音格式的文件头标记
SILK_HEADER = b"\x02\x23\x21\x53\x49\x4C\x4B"  # #!SILK


class MediaExtractor:
    """
    微信媒体文件提取与解密器

    处理微信本地存储的加密/编码媒体文件，包括：
    - 图片 (.dat 文件的 XOR 解密)
    - 语音 (.silk 转 .wav)
    - 视频 (.mp4 提取)
    - 附件（通用文件提取）

    用法示例::

        extractor = MediaExtractor()
        image_data = extractor.decrypt_image(Path("xxx.dat"))
        voice_path = extractor.extract_voice(Path("xxx.silk"))
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        image_aes_key: Optional[str | bytes] = None,
    ) -> None:
        """
        初始化媒体提取器。

        Args:
            output_dir: 提取文件的默认输出目录。若不指定则使用当前目录下的 media_output/。
        """
        self._output_dir = output_dir or Path.cwd() / "media_output"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._image_xor_key: Optional[int] = None
        self._image_aes_key: Optional[bytes] = self._normalize_image_aes_key(image_aes_key)
        self._image_aes_key_error: Optional[str] = None

    # ------------------------------------------------------------------
    # 图片解密：XOR 还原 .dat 文件
    # ------------------------------------------------------------------

    def decrypt_image(self, dat_path: Path) -> bytes:
        """
        解密微信图片 .dat 文件。

        微信PC客户端将图片文件使用单字节 XOR 加密存储为 .dat 文件。
        解密方法：用 .dat 文件的第一个字节与已知图片格式的文件头字节异或，
        得到 XOR 密钥后，对整个文件进行异或还原。

        Args:
            dat_path: .dat 加密图片文件路径。

        Returns:
            解密后的图片原始字节数据。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: 无法识别文件格式（无法确定 XOR 密钥）。
        """
        dat_path = Path(dat_path)
        if not dat_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {dat_path}")

        encrypted_data = dat_path.read_bytes()
        if len(encrypted_data) < 4:
            raise ValueError(f"文件过小，不是有效的加密图片: {dat_path}")

        decrypted, ext = self._decrypt_image_bytes(dat_path, encrypted_data)

        logger.info(
            "图片解密成功: %s -> %s 格式, %d bytes",
            dat_path.name, ext, len(decrypted),
        )
        return decrypted

    @staticmethod
    def _normalize_image_aes_key(value: Optional[str | bytes]) -> Optional[bytes]:
        if not value:
            return None
        if isinstance(value, bytes):
            return value[:16]
        text = str(value).strip()
        if not text:
            return None
        if len(text) >= 32 and re.fullmatch(r"[0-9a-fA-F]{32,}", text):
            return bytes.fromhex(text[:32])
        return text.encode("utf-8")[:16]

    def decrypt_image_to_file(
        self, dat_path: Path, output_path: Optional[Path] = None
    ) -> Path:
        """
        解密图片并保存到文件。

        Args:
            dat_path: 加密图片路径。
            output_path: 输出路径，不指定则自动生成。

        Returns:
            保存后的图片文件路径。
        """
        dat_path = Path(dat_path)
        encrypted_data = dat_path.read_bytes()
        decrypted, ext = self._decrypt_image_bytes(dat_path, encrypted_data)

        if output_path is None:
            output_path = self._output_dir / f"{dat_path.stem}{ext}"
        elif output_path.suffix.lower() in {"", ".decoded", ".bin"} and ext != ".bin":
            output_path = output_path.with_suffix(ext)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(decrypted)
        return output_path

    def _decrypt_image_bytes(self, dat_path: Path, encrypted_data: bytes) -> Tuple[bytes, str]:
        version = self._detect_wechat_v4_image_version(encrypted_data)
        if version:
            return self._decrypt_wechat_v4_image(dat_path, encrypted_data, version)

        xor_key, ext = self._detect_image_xor_key(encrypted_data)
        if xor_key is None:
            raise ValueError(f"无法识别加密图片格式，无法确定 XOR 密钥: {dat_path}")
        decrypted = bytes(b ^ xor_key for b in encrypted_data)
        return decrypted, ext

    @staticmethod
    def _detect_wechat_v4_image_version(data: bytes) -> int:
        signature = data[:6]
        return WECHAT_V4_SIGNATURES.get(signature, 0)

    def _decrypt_wechat_v4_image(
        self,
        dat_path: Path,
        encrypted_data: bytes,
        version: int,
    ) -> Tuple[bytes, str]:
        xor_key = self._get_image_xor_key(dat_path)
        aes_key = b"cfcd208495d565ef" if version == 1 else self._get_image_aes_key(dat_path, encrypted_data)
        if version == 2 and not aes_key:
            raise RuntimeError("新版图片需要微信进程中的图片 AES Key，但未能自动提取")

        decrypted = self._decrypt_wechat_v4_payload(encrypted_data, xor_key, aes_key)
        if decrypted.startswith(b"wxgf"):
            decrypted = self._convert_wxgf_to_image(decrypted)
        ext = self._detect_plain_image_ext(decrypted) or ".jpg"
        logger.info(
            "新版微信图片解密成功: %s -> %s, version=%s, %d bytes",
            dat_path.name,
            ext,
            version,
            len(decrypted),
        )
        return decrypted, ext

    @staticmethod
    def _decrypt_wechat_v4_payload(data: bytes, xor_key: int, aes_key: bytes) -> bytes:
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        header, body = data[:0xF], data[0xF:]
        _, aes_size, xor_size = struct.unpack("<6sLLx", header)
        aes_size += AES.block_size - aes_size % AES.block_size
        aes_data = body[:aes_size]
        raw_data = body[aes_size:]

        cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        decrypted_data = Padding.unpad(cipher.decrypt(aes_data), AES.block_size)

        if xor_size > 0:
            raw_data = body[aes_size:-xor_size]
            xor_data = body[-xor_size:]
            xored_data = bytes(b ^ xor_key for b in xor_data)
        else:
            xored_data = b""
        return decrypted_data + raw_data + xored_data

    def _get_image_xor_key(self, dat_path: Path) -> int:
        if self._image_xor_key is not None:
            return self._image_xor_key

        root = self._guess_wechat_media_root(dat_path)
        template_files = sorted(
            root.rglob("*_t.dat"),
            key=lambda item: self._month_sort_key(item),
            reverse=True,
        )
        if not template_files:
            raise RuntimeError("未找到新版图片缩略图模板文件，无法推导 XOR Key")

        tails = []
        for file in template_files[:64]:
            try:
                with file.open("rb") as fh:
                    fh.seek(-2, os.SEEK_END)
                    tails.append(fh.read(2))
            except OSError:
                continue
        if not tails:
            raise RuntimeError("未能读取新版图片缩略图尾部，无法推导 XOR Key")

        x, y = Counter(tails).most_common(1)[0][0]
        xor_key = x ^ 0xFF
        if xor_key != (y ^ 0xD9):
            raise RuntimeError("未能从缩略图尾部推导有效 XOR Key")
        self._image_xor_key = xor_key
        return xor_key

    def _get_image_aes_key(self, dat_path: Path, encrypted_data: bytes) -> bytes:
        if self._image_aes_key:
            return self._image_aes_key
        if self._image_aes_key_error:
            raise RuntimeError(self._image_aes_key_error)

        try:
            _, aes_size, _ = struct.unpack("<6sLLx", encrypted_data[:0xF])
            aes_size += 16 - aes_size % 16
            ciphertext = encrypted_data[0xF:0xF + aes_size][:16]
            aes_key = self._find_wechat_image_aes_key(ciphertext)
        except Exception as exc:
            self._image_aes_key_error = f"提取新版图片 AES Key 失败: {exc}"
            raise RuntimeError(self._image_aes_key_error) from exc

        self._image_aes_key = aes_key
        return aes_key

    @staticmethod
    def _find_wechat_image_aes_key(ciphertext: bytes) -> bytes:
        import pymem
        import pymem.exception
        import pymem.memory
        import pymem.process
        from pymem.ressources.structure import (
            MEMORY_PROTECTION,
            MEMORY_STATE,
            MEMORY_TYPES,
        )
        from Crypto.Cipher import AES

        def verify(candidate: bytes) -> bool:
            if len(candidate) < 16:
                return False
            try:
                plain = AES.new(candidate[:16], AES.MODE_ECB).decrypt(ciphertext)
            except Exception:
                return False
            return plain.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"wxgf"))

        process_names = {"wechat.exe", "weixin.exe", "wechatappex.exe"}
        pids: list[int] = []
        for proc in pymem.process.list_processes():
            exe = bytes(proc.szExeFile).split(b"\x00", 1)[0].decode("utf-8", "ignore")
            if exe.lower() in process_names:
                pids.append(int(proc.th32ProcessID))

        if not pids:
            raise RuntimeError("未找到正在运行的微信进程")

        readable = {
            MEMORY_PROTECTION.PAGE_READONLY.value,
            MEMORY_PROTECTION.PAGE_READWRITE.value,
            MEMORY_PROTECTION.PAGE_WRITECOPY.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_READ.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_WRITECOPY.value,
        }
        guard = MEMORY_PROTECTION.PAGE_GUARD.value
        noaccess = MEMORY_PROTECTION.PAGE_NOACCESS.value
        ascii_pattern = re.compile(rb"(?<![A-Za-z0-9])([A-Za-z0-9]{32})(?![A-Za-z0-9])")
        utf16_pattern = re.compile(
            rb"(?<![A-Za-z0-9]\x00)((?:[A-Za-z0-9]\x00){32})(?![A-Za-z0-9]\x00)"
        )
        max_region_size = 64 * 1024 * 1024
        max_checked_per_process = 300
        deadline = time.monotonic() + float(os.environ.get("WECHAT_IMAGE_AES_SCAN_TIMEOUT", "8"))
        checked_total = 0
        candidate_total = 0
        seen_candidates: set[bytes] = set()

        for pid in pids:
            if time.monotonic() > deadline:
                break
            pm = None
            try:
                pm = pymem.Pymem(pid)
                address = 0
                checked = 0
                while (
                    address < 0x7FFFFFFFFFFF
                    and checked < max_checked_per_process
                    and time.monotonic() <= deadline
                ):
                    try:
                        mbi = pymem.memory.virtual_query(pm.process_handle, address)
                    except Exception:
                        address += 0x10000
                        continue

                    base = int(mbi.BaseAddress)
                    size = int(mbi.RegionSize)
                    if size <= 0:
                        address += 0x10000
                        continue
                    address = base + size

                    protect = int(mbi.Protect)
                    if (
                        int(mbi.State) != MEMORY_STATE.MEM_COMMIT.value
                        or int(mbi.Type) != MEMORY_TYPES.MEM_PRIVATE.value
                        or protect & guard
                        or protect & noaccess
                        or (protect & 0xFF) not in readable
                        or size > max_region_size
                    ):
                        continue

                    checked += 1
                    checked_total += 1
                    try:
                        data = pm.read_bytes(base, size)
                    except Exception:
                        continue

                    for candidate in MediaExtractor._iter_image_aes_candidates(
                        data,
                        ascii_pattern,
                        utf16_pattern,
                        include_raw=False,
                    ):
                        if candidate in seen_candidates:
                            continue
                        seen_candidates.add(candidate)
                        candidate_total += 1
                        if verify(candidate):
                            logger.info(
                                "成功提取新版微信图片 AES Key: pid=%s, checked_regions=%d, candidates=%d",
                                pid,
                                checked_total,
                                candidate_total,
                            )
                            return candidate[:16]
            except (pymem.exception.ProcessNotFound, pymem.exception.CouldNotOpenProcess):
                continue
            except Exception as exc:
                logger.debug("扫描微信图片 AES Key 失败: pid=%s, error=%s", pid, exc)
                continue
            finally:
                if pm is not None:
                    try:
                        pm.close_process()
                    except Exception:
                        pass

        raise RuntimeError(
            f"未在微信进程内存中找到图片 AES Key，已扫描进程 {len(pids)} 个、"
            f"内存区域 {checked_total} 个、候选 {candidate_total} 个"
        )

    @staticmethod
    def _iter_image_aes_candidates(
        data: bytes,
        ascii_pattern: re.Pattern[bytes],
        utf16_pattern: re.Pattern[bytes],
        include_raw: bool = False,
    ):
        for match in ascii_pattern.finditer(data):
            yield match.group(1)[:16]

        for match in utf16_pattern.finditer(data):
            raw = match.group(1)
            try:
                yield raw.decode("utf-16le").encode("ascii")[:16]
            except UnicodeError:
                continue

        if include_raw:
            # Some builds may keep the AES key as raw 16 bytes, but scanning
            # every possible window is too expensive for the normal UI path.
            for offset in range(0, max(0, len(data) - 16), 4):
                yield data[offset:offset + 16]

    def _get_image_xor_key(self, dat_path: Path) -> int:
        if self._image_xor_key is not None:
            return self._image_xor_key

        root = self._guess_wechat_media_root(dat_path)
        votes: list[int] = []
        for file in sorted(
            root.rglob("*.dat"),
            key=lambda item: self._month_sort_key(item),
            reverse=True,
        )[:128]:
            try:
                data = file.read_bytes()
            except OSError:
                continue
            if len(data) >= 0x20 and self._detect_wechat_v4_image_version(data) == 2:
                votes.append(data[-1] ^ 0xD9)
                if len(votes) >= 10:
                    break

        if not votes:
            raise RuntimeError("未找到可用于推导 XOR Key 的新版图片样本")

        xor_key, _ = Counter(votes).most_common(1)[0]
        self._image_xor_key = xor_key
        return xor_key

    def _get_image_aes_key(self, dat_path: Path, encrypted_data: bytes) -> bytes:
        if self._image_aes_key:
            return self._image_aes_key
        if self._image_aes_key_error:
            raise RuntimeError(self._image_aes_key_error)

        try:
            templates = self._collect_v2_template_ciphertexts(dat_path, encrypted_data)
            try:
                aes_key = self._derive_wechat_image_aes_key(dat_path, templates)
            except Exception as derive_exc:
                logger.debug("派生新版微信图片 AES Key 失败，改用内存扫描: %s", derive_exc)
                aes_key = self._scan_wechat_image_aes_key(templates)
        except Exception as exc:
            self._image_aes_key_error = f"提取新版图片 AES Key 失败: {exc}"
            raise RuntimeError(self._image_aes_key_error) from exc

        self._image_aes_key = aes_key
        return aes_key

    def _collect_v2_template_ciphertexts(self, dat_path: Path, encrypted_data: bytes) -> list[bytes]:
        templates: list[bytes] = []
        seen: set[bytes] = set()

        def add_from_bytes(data: bytes) -> None:
            if len(data) < 0x1F or self._detect_wechat_v4_image_version(data) != 2:
                return
            block = data[0xF:0x1F]
            if len(block) == 16 and block not in seen:
                seen.add(block)
                templates.append(block)

        add_from_bytes(encrypted_data)
        root = self._guess_wechat_media_root(dat_path)
        candidates = sorted(
            list(root.rglob("*_t.dat")) + list(root.rglob("*.dat")),
            key=lambda item: self._month_sort_key(item),
            reverse=True,
        )
        for file in candidates[:128]:
            if len(templates) >= 3:
                break
            try:
                with file.open("rb") as fh:
                    add_from_bytes(fh.read(0x1F))
            except OSError:
                continue
        if not templates:
            raise RuntimeError("未找到新版图片 V2 AES 校验样本")
        return templates

    @staticmethod
    def _scan_wechat_image_aes_key(ciphertexts: list[bytes]) -> bytes:
        import pymem
        import pymem.exception
        import pymem.memory
        import pymem.process
        from pymem.ressources.structure import MEMORY_PROTECTION, MEMORY_STATE
        from Crypto.Cipher import AES

        headers = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"wxgf", b"RIFF", b"BM")

        def verify(candidate: bytes) -> bool:
            if len(candidate) < 16:
                return False
            try:
                cipher = AES.new(candidate[:16], AES.MODE_ECB)
                return all(cipher.decrypt(block).startswith(headers) for block in ciphertexts)
            except Exception:
                return False

        process_names = {"wechat.exe", "weixin.exe", "wechatappex.exe"}
        pids: list[int] = []
        for proc in pymem.process.list_processes():
            exe = bytes(proc.szExeFile).split(b"\x00", 1)[0].decode("utf-8", "ignore")
            if exe.lower() in process_names:
                pids.append(int(proc.th32ProcessID))
        if not pids:
            raise RuntimeError("未找到正在运行的微信进程")

        readable = {
            MEMORY_PROTECTION.PAGE_READONLY.value,
            MEMORY_PROTECTION.PAGE_READWRITE.value,
            MEMORY_PROTECTION.PAGE_WRITECOPY.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_READ.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE.value,
            MEMORY_PROTECTION.PAGE_EXECUTE_WRITECOPY.value,
        }
        guard = MEMORY_PROTECTION.PAGE_GUARD.value
        noaccess = MEMORY_PROTECTION.PAGE_NOACCESS.value
        ascii32_pattern = re.compile(rb"(?<![A-Za-z0-9])([A-Za-z0-9]{32})(?![A-Za-z0-9])")
        ascii16_pattern = re.compile(rb"(?<![A-Za-z0-9])([A-Za-z0-9]{16})(?![A-Za-z0-9])")
        utf16_pattern = re.compile(
            rb"(?<![A-Za-z0-9]\x00)((?:[A-Za-z0-9]\x00){32})(?![A-Za-z0-9]\x00)"
        )

        max_region_size = 50 * 1024 * 1024
        max_checked_per_process = int(os.environ.get("WECHAT_IMAGE_AES_MAX_REGIONS", "2500"))
        deadline = time.monotonic() + float(os.environ.get("WECHAT_IMAGE_AES_SCAN_TIMEOUT", "30"))
        checked_total = 0
        candidate_total = 0
        seen_candidates: set[bytes] = set()

        for pid in pids:
            if time.monotonic() > deadline:
                break
            pm = None
            try:
                pm = pymem.Pymem(pid)
                address = 0
                checked = 0
                while (
                    address < 0x7FFFFFFFFFFF
                    and checked < max_checked_per_process
                    and time.monotonic() <= deadline
                ):
                    try:
                        mbi = pymem.memory.virtual_query(pm.process_handle, address)
                    except Exception:
                        address += 0x10000
                        continue

                    base = int(mbi.BaseAddress)
                    size = int(mbi.RegionSize)
                    address = base + size if size > 0 else address + 0x10000
                    protect = int(mbi.Protect)
                    if (
                        int(mbi.State) != MEMORY_STATE.MEM_COMMIT.value
                        or protect & guard
                        or protect & noaccess
                        or (protect & 0xFF) not in readable
                        or size <= 0
                        or size > max_region_size
                    ):
                        continue

                    checked += 1
                    checked_total += 1
                    try:
                        data = pm.read_bytes(base, size)
                    except Exception:
                        continue

                    for candidate in MediaExtractor._iter_image_aes_candidates(
                        data,
                        ascii32_pattern,
                        ascii16_pattern,
                        utf16_pattern,
                    ):
                        if candidate in seen_candidates:
                            continue
                        seen_candidates.add(candidate)
                        candidate_total += 1
                        if verify(candidate):
                            logger.info(
                                "成功提取新版微信图片 AES Key: pid=%s, checked_regions=%d, candidates=%d",
                                pid,
                                checked_total,
                                candidate_total,
                            )
                            return candidate[:16]
            except (pymem.exception.ProcessNotFound, pymem.exception.CouldNotOpenProcess):
                continue
            except Exception as exc:
                logger.debug("扫描微信图片 AES Key 失败: pid=%s, error=%s", pid, exc)
                continue
            finally:
                if pm is not None:
                    try:
                        pm.close_process()
                    except Exception:
                        pass

        raise RuntimeError(
            f"未在微信进程内存中找到图片 AES Key，已扫描进程 {len(pids)} 个、"
            f"内存区域 {checked_total} 个、候选 {candidate_total} 个"
        )

    @staticmethod
    def _iter_image_aes_candidates(
        data: bytes,
        ascii32_pattern: re.Pattern[bytes],
        ascii16_pattern: re.Pattern[bytes],
        utf16_pattern: re.Pattern[bytes],
        include_raw: bool = False,
    ):
        for match in ascii32_pattern.finditer(data):
            yield match.group(1)[:16]
        for match in ascii16_pattern.finditer(data):
            yield match.group(1)
        for match in utf16_pattern.finditer(data):
            raw = match.group(1)
            try:
                yield raw.decode("utf-16le").encode("ascii")[:16]
            except UnicodeError:
                continue
        if include_raw:
            for offset in range(0, max(0, len(data) - 16), 4):
                yield data[offset:offset + 16]

    def _derive_wechat_image_aes_key(self, dat_path: Path, ciphertexts: list[bytes]) -> bytes:
        """Derive WeChat 4.x image AES key from xwechat account directory.

        Account folders such as ``wxid_xxxxx_4b83`` encode enough information
        to reduce the UIN search space. The result is verified against real V2
        image AES blocks before being accepted.
        """
        import hashlib
        from Crypto.Cipher import AES

        account_dir = self._guess_wechat_account_dir(dat_path)
        raw_wxid = account_dir.name
        match = re.match(r"^(wxid_[A-Za-z0-9]+)_([0-9a-fA-F]{4})$", raw_wxid)
        if not match:
            raise RuntimeError(f"account dir has no derivable wxid suffix: {raw_wxid}")

        base_wxid, suffix = match.group(1), match.group(2).lower()
        xor_key = self._get_image_xor_key(dat_path)
        wxid_candidates = [base_wxid, raw_wxid]
        headers = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"wxgf", b"RIFF", b"BM")

        def verify(candidate: bytes) -> bool:
            try:
                cipher = AES.new(candidate[:16], AES.MODE_ECB)
                return all(cipher.decrypt(block).startswith(headers) for block in ciphertexts[:3])
            except Exception:
                return False

        def scan_range(start: int, end: int) -> bytes | None:
            for high in range(start, end):
                uin = (high << 8) | xor_key
                if hashlib.md5(str(uin).encode("ascii")).hexdigest()[:4] != suffix:
                    continue
                for wxid in wxid_candidates:
                    digest = hashlib.md5(f"{uin}{wxid}".encode("utf-8")).hexdigest()
                    candidate = digest[:16].encode("ascii")
                    if verify(candidate):
                        logger.info("成功派生新版微信图片 AES Key: wxid=%s, uin=%s", wxid, uin)
                        return candidate
            return None

        workers = max(1, min(16, os.cpu_count() or 4))
        space = 1 << 24
        step = (space + workers - 1) // workers
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(scan_range, start, min(start + step, space))
                for start in range(0, space, step)
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    for pending in futures:
                        pending.cancel()
                    return result
        raise RuntimeError("could not derive image AES key from account suffix")

    @staticmethod
    def _guess_wechat_account_dir(dat_path: Path) -> Path:
        resolved = dat_path.resolve()
        for parent in resolved.parents:
            if parent.name.lower() == "msg":
                return parent.parent
        return resolved.parent

    @staticmethod
    def _convert_wxgf_to_image(data: bytes) -> bytes:
        import ctypes
        from ctypes import POINTER, byref, c_int, c_int64, create_string_buffer

        dll_path = MediaExtractor._find_voip_engine_dll()
        if not dll_path:
            raise RuntimeError("decrypted wxgf image but VoipEngine.dll was not found")

        voip_engine = ctypes.WinDLL(str(dll_path))
        convert = voip_engine.wxam_dec_wxam2pic_5
        convert.argtypes = [c_int64, c_int, c_int64, POINTER(c_int), c_int64]
        convert.restype = c_int64

        class WxAMConfig(ctypes.Structure):
            _fields_ = [("mode", c_int), ("reserved", c_int)]

        errors: list[str] = []
        for mode in (0, 3):
            try:
                input_buffer = create_string_buffer(data, len(data))
                output_buffer = create_string_buffer(52 * 1024 * 1024)
                output_size = c_int(len(output_buffer))
                config = WxAMConfig()
                config.mode = mode
                config.reserved = 0
                result = convert(
                    ctypes.addressof(input_buffer),
                    len(data),
                    ctypes.addressof(output_buffer),
                    byref(output_size),
                    ctypes.addressof(config),
                )
                output = output_buffer.raw[: output_size.value]
                if result == 0 and output.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8")):
                    return output
                errors.append(f"mode={mode}, result={result}, size={output_size.value}")
            except Exception as exc:
                errors.append(f"mode={mode}, error={exc}")
        raise RuntimeError("wxgf conversion failed: " + "; ".join(errors))

    @staticmethod
    def _find_voip_engine_dll() -> Optional[Path]:
        candidates: list[Path] = []
        env_path = os.environ.get("WECHAT_VOIP_ENGINE_DLL")
        if env_path:
            candidates.append(Path(env_path))
        roots = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tencent" / "Weixin",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tencent" / "Weixin",
        ]
        for root in roots:
            if root.exists():
                candidates.extend(root.glob("*/VoipEngine.dll"))
                candidates.extend(root.glob("VoipEngine.dll"))
        existing = [item for item in candidates if item.is_file()]
        if not existing:
            return None
        existing.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return existing[0]

    @staticmethod
    def _guess_wechat_media_root(dat_path: Path) -> Path:
        parts = list(dat_path.resolve().parents)
        for parent in parts:
            if parent.name.lower() == "msg":
                return parent
        return dat_path.resolve().parent

    @staticmethod
    def _month_sort_key(path: Path) -> str:
        match = re.search(r"(\d{4}-\d{2})", str(path))
        return match.group(1) if match else "0000-00"

    @staticmethod
    def _detect_plain_image_ext(data: bytes) -> str:
        for header_bytes, (ext, _) in KNOWN_HEADERS.items():
            if data.startswith(header_bytes):
                return ext
        return ""

    @staticmethod
    def _detect_image_xor_key(
        data: bytes,
    ) -> Tuple[Optional[int], str]:
        """
        通过与已知文件头异或来检测 XOR 密钥。

        将加密数据的第一个字节分别与 JPEG/PNG/GIF/BMP 等格式的
        文件头首字节异或，如果得到的密钥能使后续字节也匹配文件头，
        则确认该密钥有效。

        Args:
            data: 加密的文件数据。

        Returns:
            (xor_key, extension) 元组。无法识别时返回 (None, "")。
        """
        first_byte = data[0]

        for header_bytes, (ext, desc) in KNOWN_HEADERS.items():
            # 用第一个字节推算候选密钥
            candidate_key = first_byte ^ header_bytes[0]

            # 验证：用候选密钥解密前几个字节，检查是否匹配完整文件头
            match = True
            for i in range(min(len(header_bytes), len(data))):
                if (data[i] ^ candidate_key) != header_bytes[i]:
                    match = False
                    break

            if match:
                if ext == ".bmp" and len(data) >= 14:
                    decoded_prefix = bytes(b ^ candidate_key for b in data[:14])
                    file_size = int.from_bytes(decoded_prefix[2:6], "little", signed=False)
                    reserved = decoded_prefix[6:10]
                    if reserved != b"\x00\x00\x00\x00" or file_size > len(data) * 2:
                        continue
                return candidate_key, ext

        return None, ""

    # ------------------------------------------------------------------
    # 语音提取：silk 转 wav
    # ------------------------------------------------------------------

    def extract_voice(
        self,
        silk_path: Path,
        output_format: str = "wav",
    ) -> Path:
        """
        将微信语音 .silk 文件转换为 .wav 或 .mp3 格式。

        微信语音消息使用 SILK 编码格式存储。本方法尝试以下转换方式：
        1. 使用 pilk 库（纯 Python）
        2. 使用 silk-v3-decoder 命令行工具
        3. 使用 ffmpeg（如果已安装 silk 解码插件）

        Args:
            silk_path: .silk 语音文件路径。
            output_format: 输出格式，支持 "wav" 或 "mp3"。

        Returns:
            转换后的音频文件路径。

        Raises:
            FileNotFoundError: 源文件不存在。
            RuntimeError: 转换失败（缺少依赖工具）。
        """
        silk_path = Path(silk_path)
        if not silk_path.exists():
            raise FileNotFoundError(f"语音文件不存在: {silk_path}")

        output_path = self._output_dir / f"{silk_path.stem}.{output_format}"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 读取文件，跳过微信自定义的头部（前一个字节 0x02）
        raw_data = silk_path.read_bytes()
        if raw_data and raw_data[0:1] == b"\x02":
            raw_data = raw_data[1:]  # 去掉微信添加的前缀字节

        # 方式一：尝试使用 pilk 库
        if self._convert_with_pilk(raw_data, output_path, silk_path):
            return output_path

        # 方式二：尝试使用 silk-v3-decoder 命令行工具
        if self._convert_with_decoder(silk_path, output_path):
            return output_path

        raise RuntimeError(
            "语音转换失败。请安装 pilk (pip install pilk) 或 "
            "silk-v3-decoder (https://github.com/nicedayzhu/silk-v3-decoder)。"
        )

    @staticmethod
    def _convert_with_pilk(
        silk_data: bytes,
        output_path: Path,
        original_path: Path,
    ) -> bool:
        """尝试使用 pilk 库转换 silk 到 pcm 再到 wav。"""
        try:
            import pilk
        except ImportError:
            logger.debug("pilk 库未安装，跳过")
            return False

        try:
            # pilk 需要文件路径，写入临时文件
            import tempfile
            import wave

            with tempfile.NamedTemporaryFile(
                suffix=".silk", delete=False
            ) as tmp_silk:
                tmp_silk.write(silk_data)
                tmp_silk_path = tmp_silk.name

            pcm_path = str(output_path.with_suffix(".pcm"))

            # silk 解码为 pcm
            duration = pilk.decode(tmp_silk_path, pcm_path)
            logger.info("silk 解码完成，时长: %d ms", duration)

            # pcm 转 wav
            with open(pcm_path, "rb") as pcm_file:
                pcm_data = pcm_file.read()

            with wave.open(str(output_path), "wb") as wav_file:
                wav_file.setnchannels(1)        # 单声道
                wav_file.setsampwidth(2)         # 16-bit
                wav_file.setframerate(24000)     # 24kHz 采样率
                wav_file.writeframes(pcm_data)

            # 清理临时文件
            Path(tmp_silk_path).unlink(missing_ok=True)
            Path(pcm_path).unlink(missing_ok=True)

            return True
        except Exception as e:
            logger.warning("pilk 转换失败: %s", e)
            return False

    @staticmethod
    def _convert_with_decoder(
        silk_path: Path,
        output_path: Path,
    ) -> bool:
        """尝试使用 silk-v3-decoder 命令行工具转换。"""
        decoder_names = ["silk_v3_decoder", "silk-v3-decoder", "decoder"]

        for decoder in decoder_names:
            try:
                pcm_path = output_path.with_suffix(".pcm")
                result = subprocess.run(
                    [decoder, str(silk_path), str(pcm_path)],
                    capture_output=True,
                    timeout=30,
                )
                if result.returncode == 0 and pcm_path.exists():
                    # 使用 ffmpeg 将 pcm 转为目标格式
                    ffmpeg_result = subprocess.run(
                        [
                            "ffmpeg", "-y",
                            "-f", "s16le",
                            "-ar", "24000",
                            "-ac", "1",
                            "-i", str(pcm_path),
                            str(output_path),
                        ],
                        capture_output=True,
                        timeout=30,
                    )
                    pcm_path.unlink(missing_ok=True)
                    if ffmpeg_result.returncode == 0:
                        return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        return False

    # ------------------------------------------------------------------
    # 视频提取
    # ------------------------------------------------------------------

    def extract_video(self, video_path: Path) -> Path:
        """
        提取微信视频文件。

        微信视频以 .mp4 格式存储在 FileStorage/Video 目录下，
        通常不需要解密，直接拷贝即可。

        Args:
            video_path: 视频源文件路径。

        Returns:
            拷贝到输出目录后的视频文件路径。

        Raises:
            FileNotFoundError: 源文件不存在。
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        output_path = self._output_dir / video_path.name

        # 避免重复拷贝
        if output_path.exists() and output_path.stat().st_size == video_path.stat().st_size:
            logger.debug("视频已存在，跳过拷贝: %s", output_path)
            return output_path

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, output_path)
        logger.info("视频提取完成: %s -> %s", video_path.name, output_path)
        return output_path

    # ------------------------------------------------------------------
    # 批量提取
    # ------------------------------------------------------------------

    def batch_extract(
        self,
        media_list: List[dict],
        output_dir: Optional[Path] = None,
    ) -> List[Path]:
        """
        批量提取媒体文件。

        根据每个媒体项的类型自动选择对应的提取方法。

        Args:
            media_list: 媒体项列表，每个字典应包含:
                        - path (str | Path): 源文件路径
                        - type (str): 媒体类型，可选值: image, voice, video, file
            output_dir: 输出目录，不指定则使用默认输出目录。

        Returns:
            成功提取的文件路径列表。
        """
        if output_dir:
            original_output = self._output_dir
            self._output_dir = Path(output_dir)
            self._output_dir.mkdir(parents=True, exist_ok=True)

        extracted: List[Path] = []
        errors: List[str] = []

        for item in media_list:
            source_path = Path(item.get("path", ""))
            media_type = item.get("type", "").lower()

            try:
                if media_type == "image":
                    result = self.decrypt_image_to_file(source_path)
                elif media_type == "voice":
                    result = self.extract_voice(source_path)
                elif media_type == "video":
                    result = self.extract_video(source_path)
                elif media_type == "file":
                    result = self._extract_file(source_path)
                else:
                    logger.warning("未知媒体类型 '%s': %s", media_type, source_path)
                    continue

                extracted.append(result)

            except Exception as e:
                error_msg = f"提取失败 ({media_type}) {source_path}: {e}"
                errors.append(error_msg)
                logger.error(error_msg)

        if output_dir:
            self._output_dir = original_output

        logger.info(
            "批量提取完成: 成功 %d / 总计 %d, 失败 %d",
            len(extracted), len(media_list), len(errors),
        )
        return extracted

    def _extract_file(self, file_path: Path) -> Path:
        """
        提取普通附件文件（直接拷贝）。

        Args:
            file_path: 源文件路径。

        Returns:
            输出文件路径。
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        output_path = self._output_dir / file_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, output_path)
        logger.info("文件提取完成: %s", file_path.name)
        return output_path
