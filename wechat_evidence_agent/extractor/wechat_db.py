"""
微信数据库解密与消息提取模块

负责：
1. 定位微信客户端的本地数据目录（Windows / macOS）
2. 从微信进程内存中提取数据库解密密钥
3. 使用 SQLCipher 解密微信 SQLite 数据库
4. 读取消息记录和联系人信息

依赖：pymem (Windows 进程内存读取), pysqlcipher3 (SQLCipher 解密)
支持 Windows 和 macOS 平台。
"""

from __future__ import annotations

import ctypes
import concurrent.futures
import hashlib
import hmac
import logging
import os
import platform
import re
import shutil
import sqlite3
import struct
import tempfile
import threading
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# 当前操作系统
CURRENT_PLATFORM = platform.system()  # "Windows" / "Darwin" / "Linux"

# 微信进程名称
WECHAT_PROCESS_NAME = "WeChat.exe"
WECHAT_PROCESS_NAMES_WIN = ("WeChat.exe", "Weixin.exe")
WECHAT_PROCESS_NAME_MAC = "WeChat"

# SQLCipher 解密参数（微信PC版使用的加密配置）
SQLCIPHER_PAGE_SIZE = 4096
SQLCIPHER_KDF_ITER = 64000
SQLCIPHER_CIPHER_COMPAT = 3

# WeChat/Weixin 4.x (xwechat_files/db_storage) uses WCDB-style SQLCipher
# pages. These constants match the on-disk page format used by the new
# Windows client.
WCDB_V4_PAGE_SIZE = 4096
WCDB_V4_SALT_SIZE = 16
WCDB_V4_KEY_SIZE = 32
WCDB_V4_IV_SIZE = 16
WCDB_V4_HMAC_SIZE = 64
WCDB_V4_AES_BLOCK_SIZE = 16
WCDB_V4_KDF_ITER = 256000
SQLITE_HEADER = b"SQLite format 3\x00"

# 消息数据库文件名模式
# Windows: MSG0.db ~ MSG9.db (位于 Multi/ 目录)
MSG_DB_PATTERN_WIN = "MSG{n}.db"
# macOS: msg_0.db ~ msg_9.db (位于 Message/ 目录)
MSG_DB_PATTERN_MAC = "msg_{n}.db"
MSG_DB_COUNT = 10

# 联系人数据库
MICRO_MSG_DB = "MicroMsg.db"                  # Windows
CONTACT_DB_MAC = "wccontact_new2.db"          # macOS

# 数据库密钥长度（32字节 = 256位 AES）
KEY_LENGTH = 32

# 在微信进程内存中搜索密钥时使用的特征模式
# 密钥前通常有固定的标记字节，用于定位密钥在内存中的位置
KEY_SEARCH_PATTERN = b"\x00" * 4  # 密钥前的空字节标记


def _ensure_supported_platform() -> str:
    """
    检查当前平台是否受支持（Windows 或 macOS）。

    Returns:
        平台名称字符串 ("Windows" 或 "Darwin")。

    Raises:
        OSError: 平台不受支持。
    """
    if CURRENT_PLATFORM not in ("Windows", "Darwin"):
        raise OSError(
            f"当前平台 ({CURRENT_PLATFORM}) 不受支持。"
            "本模块仅支持 Windows 和 macOS 平台。"
        )
    return CURRENT_PLATFORM


def _verify_weixin_v4_key_bytes(passphrase: bytes, db_path: str) -> bool:
    """Process-pool friendly HMAC check for Weixin 4.x database keys."""
    if len(passphrase) != KEY_LENGTH:
        return False
    try:
        with open(db_path, "rb") as f:
            first_page = f.read(WCDB_V4_PAGE_SIZE)
    except OSError:
        return False
    if len(first_page) < WCDB_V4_PAGE_SIZE:
        return False
    return _verify_weixin_v4_key_material(passphrase, first_page, raw=True) or _verify_weixin_v4_key_material(passphrase, first_page, raw=False)


def _verify_weixin_v4_key_material(key_material: bytes, first_page: bytes, raw: bool) -> bool:
    try:
        from Crypto.Hash import SHA512
        from Crypto.Protocol.KDF import PBKDF2
    except ImportError:
        return False
    salt = first_page[:WCDB_V4_SALT_SIZE]
    if raw:
        key = key_material
    else:
        key = PBKDF2(
            key_material,
            salt,
            dkLen=WCDB_V4_KEY_SIZE,
            count=WCDB_V4_KDF_ITER,
            hmac_hash_module=SHA512,
        )
    mac_salt = bytes(x ^ 0x3A for x in salt)
    mac_key = PBKDF2(key, mac_salt, dkLen=WCDB_V4_KEY_SIZE, count=2, hmac_hash_module=SHA512)
    reserve = ((WCDB_V4_IV_SIZE + WCDB_V4_HMAC_SIZE + WCDB_V4_AES_BLOCK_SIZE - 1) // WCDB_V4_AES_BLOCK_SIZE) * WCDB_V4_AES_BLOCK_SIZE
    mac_start = WCDB_V4_PAGE_SIZE - reserve + WCDB_V4_IV_SIZE
    mac_data = first_page[WCDB_V4_SALT_SIZE : WCDB_V4_PAGE_SIZE - reserve + WCDB_V4_IV_SIZE]
    digest = hmac.new(mac_key, mac_data, "sha512")
    digest.update(struct.pack("<I", 1))
    return digest.digest() == first_page[mac_start : mac_start + WCDB_V4_HMAC_SIZE]


class WeChatDBExtractor:
    """
    微信数据库解密与消息提取器

    负责从微信客户端（Windows / macOS）的本地加密数据库中提取聊天记录和联系人信息。

    典型用法::

        extractor = WeChatDBExtractor()
        key = extractor.get_decrypt_key()
        contacts = extractor.get_contacts()
        messages = extractor.get_messages("wxid_xxxx", start_date="2024-01-01")
        extractor.close()

    或使用上下文管理器::

        with WeChatDBExtractor() as ext:
            contacts = ext.get_contacts()
    """

    def __init__(self, wechat_dir: Optional[Path] = None) -> None:
        """
        初始化提取器。

        Args:
            wechat_dir: 微信数据目录路径。若不指定则自动查找。
        """
        self._wechat_dir: Optional[Path] = (
            self._resolve_wechat_dir(wechat_dir) if wechat_dir else None
        )
        self._decrypt_key: Optional[bytes] = None
        self._v4_candidate_keys: List[bytes] = []
        self._v4_raw_keys: set[bytes] = set()
        self._v4_raw_keys_by_db: Dict[str, bytes] = {}
        self._decrypted_dbs: Dict[str, Path] = {}  # 已解密的数据库缓存
        self._decrypted_db_signatures: Dict[str, Tuple[int, int]] = {}
        self._temp_dir: Optional[Path] = None       # 解密数据库的临时目录
        self._connections: Dict[tuple[int, str], sqlite3.Connection] = {}  # 数据库连接池

    def __enter__(self) -> "WeChatDBExtractor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 查找微信数据目录
    # ------------------------------------------------------------------

    def find_wechat_dir(self) -> Path:
        """
        查找微信客户端的本地数据目录（自动检测平台）。

        - Windows 默认位置: %USERPROFILE%/Documents/WeChat Files/{wxid}/Msg/
        - macOS 默认位置: ~/Library/Containers/com.tencent.xinWeChat/Data/
          Library/Application Support/com.tencent.xinWeChat/{version_hash}/{account_hash}/

        Returns:
            微信消息数据库所在目录的 Path 对象。

        Raises:
            OSError: 平台不受支持。
            FileNotFoundError: 找不到微信数据目录。
        """
        plat = _ensure_supported_platform()

        if self._wechat_dir and self._wechat_dir.exists():
            return self._wechat_dir

        if plat == "Darwin":
            return self._find_wechat_dir_mac()
        else:
            return self._find_wechat_dir_windows()

    # ------------------------------------------------------------------
    # 平台特定：查找数据目录
    # ------------------------------------------------------------------

    def _find_wechat_dir_windows(self) -> Path:
        """查找 Windows 平台上的微信数据目录。"""
        # Prefer both classic "WeChat Files" and newer "xwechat_files" roots.
        search_roots = [
            Path(os.environ.get("USERPROFILE", "")) / "Documents" / "WeChat Files",
            Path(os.environ.get("USERPROFILE", "")) / "Documents" / "xwechat_files",
            Path(os.environ.get("USERPROFILE", "")) / "OneDrive" / "Documents" / "WeChat Files",
            Path(os.environ.get("USERPROFILE", "")) / "OneDrive" / "Documents" / "xwechat_files",
        ]
        wxid_dirs = []
        for root in search_roots:
            if not root.exists():
                continue
            try:
                wxid_dirs.append(self._resolve_wechat_dir(root))
            except FileNotFoundError:
                continue

        if wxid_dirs:
            if len(wxid_dirs) > 1:
                logger.warning(
                    "Found multiple WeChat data directories: %s; using latest modified",
                    [str(d) for d in wxid_dirs],
                )
                wxid_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            self._wechat_dir = wxid_dirs[0]
            logger.info("Located WeChat data directory (Windows): %s", self._wechat_dir)
            return self._wechat_dir

        # 查找 Documents/WeChat Files/ 下的用户目录
        documents_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
        wechat_files_dir = documents_dir / "WeChat Files"

        if not wechat_files_dir.exists():
            # 也检查 OneDrive 路径
            onedrive_docs = Path(os.environ.get("USERPROFILE", "")) / "OneDrive" / "Documents"
            wechat_files_dir = onedrive_docs / "WeChat Files"
            if not wechat_files_dir.exists():
                raise FileNotFoundError(
                    f"未找到微信数据目录。已搜索: {documents_dir / 'WeChat Files'}, "
                    f"{onedrive_docs / 'WeChat Files'}"
                )

        # 遍历用户目录，查找包含 Msg 子目录的 wxid 目录
        wxid_dirs = []
        for item in wechat_files_dir.iterdir():
            if item.is_dir() and (item / "Msg").is_dir():
                wxid_dirs.append(item)

        if not wxid_dirs:
            raise FileNotFoundError(
                f"在 {wechat_files_dir} 下未找到有效的微信用户数据目录"
            )

        if len(wxid_dirs) > 1:
            logger.warning(
                "发现多个微信用户目录: %s，使用最近修改的目录",
                [d.name for d in wxid_dirs],
            )
            wxid_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        msg_dir = wxid_dirs[0] / "Msg"
        self._wechat_dir = msg_dir
        logger.info("定位到微信数据目录 (Windows): %s", msg_dir)
        return msg_dir

    def _resolve_wechat_dir(self, path: Path) -> Path:
        """Resolve a supplied WeChat path to the database layout root."""
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"WeChat data directory does not exist: {path}")

        if (path / "db_storage" / "message").is_dir():
            return path
        if path.name.lower() == "db_storage" and (path / "message").is_dir():
            return path.parent
        if (path / "Msg").is_dir():
            return path / "Msg"
        if path.name.lower() == "msg" and (path / "Multi").is_dir():
            return path

        account_dirs: list[Path] = []
        for item in path.iterdir():
            if not item.is_dir():
                continue
            if (item / "db_storage" / "message").is_dir():
                account_dirs.append(item)
            elif (item / "Msg").is_dir():
                account_dirs.append(item / "Msg")

        if account_dirs:
            account_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            return account_dirs[0]

        raise FileNotFoundError(f"No valid WeChat account database directory under {path}")

    def _is_new_windows_layout(self, wechat_dir: Path) -> bool:
        """Return True for xwechat_files/db_storage based Windows layout."""
        return (wechat_dir / "db_storage" / "message").is_dir()

    def _find_wechat_dir_mac(self) -> Path:
        """
        查找 macOS 平台上的微信数据目录。

        macOS 路径结构:
        ~/Library/Containers/com.tencent.xinWeChat/Data/
          Library/Application Support/com.tencent.xinWeChat/{version_hash}/{account_hash}/
        """
        base_dir = (
            Path.home()
            / "Library"
            / "Containers"
            / "com.tencent.xinWeChat"
            / "Data"
            / "Library"
            / "Application Support"
            / "com.tencent.xinWeChat"
        )

        if not base_dir.exists():
            raise FileNotFoundError(
                f"未找到 macOS 微信数据目录: {base_dir}"
            )

        # 结构: {version_hash}/{account_hash}/
        # 遍历二级目录，查找包含 Message/ 子目录的账号目录
        account_dirs: list[Path] = []
        for version_dir in base_dir.iterdir():
            if not version_dir.is_dir():
                continue
            for account_dir in version_dir.iterdir():
                if account_dir.is_dir() and (account_dir / "Message").is_dir():
                    account_dirs.append(account_dir)

        if not account_dirs:
            raise FileNotFoundError(
                f"在 {base_dir} 下未找到有效的微信用户数据目录"
            )

        if len(account_dirs) > 1:
            logger.warning(
                "发现多个微信用户目录: %s，使用最近修改的目录",
                [d.name for d in account_dirs],
            )
            account_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        # macOS 的数据根目录是账号目录本身（Message/ 和 Contacts/ 在其下）
        data_dir = account_dirs[0]
        self._wechat_dir = data_dir
        logger.info("定位到微信数据目录 (macOS): %s", data_dir)
        return data_dir

    # ------------------------------------------------------------------
    # 从进程内存中获取解密密钥
    # ------------------------------------------------------------------

    def get_decrypt_key(self) -> bytes:
        """
        获取数据库解密密钥（自动检测平台）。

        - Windows: 从运行中的微信进程内存中提取。
        - macOS: 尝试从 Keychain 读取，失败则提供手动输入指引。

        Returns:
            32 字节的解密密钥。

        Raises:
            OSError: 平台不受支持。
            RuntimeError: 无法提取密钥。
        """
        plat = _ensure_supported_platform()

        if self._decrypt_key:
            return self._decrypt_key

        env_key = os.environ.get("WECHAT_DB_KEY", "").strip()
        if env_key:
            key = self._parse_key_string(env_key)
            if key and len(key) == KEY_LENGTH:
                self._decrypt_key = key
                logger.info("从环境变量 WECHAT_DB_KEY 获取解密密钥")
                return key
            raise RuntimeError(
                "环境变量 WECHAT_DB_KEY 无效。应为 64 位十六进制字符串"
                "或 32 字节原始密钥。"
            )

        if plat == "Darwin":
            return self._get_decrypt_key_mac()
        else:
            return self._get_decrypt_key_windows()

    # ------------------------------------------------------------------
    # 平台特定：密钥提取
    # ------------------------------------------------------------------

    def _get_decrypt_key_windows(self) -> bytes:
        """Extract the WeChat database key from the running Windows client."""
        try:
            import pymem
            import pymem.process
        except ImportError:
            raise RuntimeError("需要安装 pymem 库: pip install pymem")

        wechat_dir = self._wechat_dir or self.find_wechat_dir()
        target_db = self._get_contact_db_path_windows(wechat_dir)

        errors: list[str] = []
        for process_name in WECHAT_PROCESS_NAMES_WIN:
            for pid in self._iter_process_ids(process_name):
                pm = None
                try:
                    pm = pymem.Pymem(pid)
                    if self._is_new_windows_layout(wechat_dir):
                        key = self._scan_process_private_memory_for_v4_key(pm, target_db)
                        if key:
                            self._decrypt_key = key
                            logger.info("成功提取新版微信数据库解密密钥 (%d bytes)", len(key))
                            return key
                        continue

                    module = self._find_wechat_module(pm)
                    if module:
                        logger.info(
                            "%s module base: 0x%X, size: %d bytes",
                            module.name,
                            module.lpBaseOfDll,
                            module.SizeOfImage,
                        )
                        key = self._scan_memory_for_key(
                            pm,
                            module.lpBaseOfDll,
                            module.SizeOfImage,
                            target_db,
                        )
                        if key:
                            self._decrypt_key = key
                            logger.info("成功提取数据库解密密钥 (%d bytes)", len(key))
                            return key
                except Exception as exc:
                    errors.append(f"{process_name}/{pid}: {exc}")
                finally:
                    if pm is not None:
                        try:
                            pm.close_process()
                        except Exception:
                            pass

        detail = "; ".join(errors[-3:])
        if detail:
            detail = f" 最近错误: {detail}"
        raise RuntimeError(
            "无法从微信进程内存中提取数据库密钥。"
            "请确认微信已登录，并尝试以管理员权限运行本程序。" + detail
        )

    def _get_decrypt_key_mac(self) -> bytes:
        """
        在 macOS 上获取微信数据库解密密钥。

        依次尝试以下方式：
        1. 从 macOS Keychain 读取（security find-generic-password）
        2. 从环境变量 WECHAT_DB_KEY 读取（手动预设）
        3. 提示用户手动输入

        Returns:
            32 字节的解密密钥。

        Raises:
            RuntimeError: 所有自动方式失败且无手动输入。
        """
        import subprocess as _sp

        # 方式一：尝试从 macOS Keychain 读取
        try:
            result = _sp.run(
                [
                    "security", "find-generic-password",
                    "-s", "com.tencent.xinWeChat",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                raw_key = result.stdout.strip()
                # Keychain 可能返回 hex 字符串或原始密码
                key = self._parse_key_string(raw_key)
                if key and len(key) == KEY_LENGTH:
                    self._decrypt_key = key
                    logger.info("从 macOS Keychain 成功获取解密密钥")
                    return key
                logger.warning("Keychain 返回的密钥长度不正确 (%d bytes)", len(key) if key else 0)
        except (FileNotFoundError, _sp.TimeoutExpired) as e:
            logger.debug("Keychain 读取失败: %s", e)

        # 方式二：从环境变量获取
        env_key = os.environ.get("WECHAT_DB_KEY", "").strip()
        if env_key:
            key = self._parse_key_string(env_key)
            if key and len(key) == KEY_LENGTH:
                self._decrypt_key = key
                logger.info("从环境变量 WECHAT_DB_KEY 获取解密密钥")
                return key

        # 方式三：提示用户手动输入
        logger.warning(
            "无法自动获取 macOS 微信解密密钥。\n"
            "您可以通过以下方式获取密钥：\n"
            "  1. 使用 lldb 附加到微信进程并读取内存中的密钥\n"
            "  2. 使用第三方微信数据库解密工具提取密钥\n"
            "  3. 设置环境变量 WECHAT_DB_KEY=<hex_key> 后重试\n"
            "密钥应为 64 个十六进制字符（32 字节）。"
        )
        try:
            user_input = input("请输入 64 位十六进制密钥（或按 Enter 跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = ""

        if user_input:
            key = self._parse_key_string(user_input)
            if key and len(key) == KEY_LENGTH:
                self._decrypt_key = key
                logger.info("使用手动输入的解密密钥")
                return key
            raise RuntimeError(
                f"输入的密钥无效。期望 64 个十六进制字符，得到 {len(user_input)} 个字符。"
            )

        raise RuntimeError(
            "未能获取 macOS 微信数据库解密密钥。"
            "请设置环境变量 WECHAT_DB_KEY 或通过 lldb 提取密钥后手动输入。"
        )

    @staticmethod
    def _parse_key_string(raw: str) -> Optional[bytes]:
        """
        将密钥字符串解析为 bytes。

        支持格式：
        - 64 位十六进制字符串（如 "ab01cd..."）
        - 带 0x 前缀的十六进制字符串
        - 原始字符串（直接编码为 bytes）

        Returns:
            解析后的 bytes，解析失败返回 None。
        """
        raw = raw.strip()
        if raw.startswith("0x") or raw.startswith("0X"):
            raw = raw[2:]
        try:
            return bytes.fromhex(raw)
        except ValueError:
            # 非十六进制：尝试作为原始字符串编码
            encoded = raw.encode("utf-8")
            if len(encoded) == KEY_LENGTH:
                return encoded
            return None

    def _scan_memory_for_key(
        self,
        pm: "pymem.Pymem",
        base_addr: int,
        module_size: int,
        db_path: Optional[Path] = None,
    ) -> Optional[bytes]:
        """Scan a module image for classic or Weixin 4.x database keys."""
        chunk_size = 1024 * 1024
        offset = 0
        target_db = Path(db_path) if db_path else self._get_verification_db_path()

        while offset < module_size:
            read_size = min(chunk_size, module_size - offset)
            try:
                data = pm.read_bytes(base_addr + offset, read_size)
            except Exception:
                offset += chunk_size
                continue

            # Weixin 4.x key stub. The first 8 bytes point to the raw key.
            for stub_pos in self._find_v4_key_stubs(data):
                ptr = struct.unpack_from("<Q", data, stub_pos)[0]
                candidate = self._read_process_bytes(pm, ptr, KEY_LENGTH)
                if candidate and self._verify_key(candidate, target_db):
                    return candidate

            offset += chunk_size - KEY_LENGTH

        # Classic fallback only for old WeChat layouts. Running this against the
        # huge Weixin.dll image is too slow because it verifies millions of chunks.
        try:
            wechat_dir = self._wechat_dir or self.find_wechat_dir()
            if self._is_new_windows_layout(wechat_dir):
                return None
        except Exception:
            pass

        offset = 0
        while offset < module_size:
            read_size = min(chunk_size, module_size - offset)
            try:
                data = pm.read_bytes(base_addr + offset, read_size)
            except Exception:
                offset += chunk_size
                continue

            pos = 0
            while pos < len(data) - KEY_LENGTH:
                candidate = data[pos : pos + KEY_LENGTH]
                if candidate != b"\x00" * KEY_LENGTH and len(set(candidate)) > 8:
                    if self._verify_key(candidate, target_db):
                        return candidate
                pos += 1

            offset += chunk_size - KEY_LENGTH

        return None

    def _scan_process_private_memory_for_v4_key(
        self,
        pm: "pymem.Pymem",
        db_path: Path,
        *,
        verify_candidates: bool = True,
    ) -> Optional[bytes]:
        """Scan readable private memory for Weixin 4.x key stubs."""
        try:
            import pymem.exception
            import pymem.memory
            from pymem.ressources.structure import (
                MEMORY_PROTECTION,
                MEMORY_STATE,
                MEMORY_TYPES,
            )
        except ImportError:
            return None

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
        max_region_size = 64 * 1024 * 1024
        checked = 0
        max_checked = 3000
        seen_ptrs: set[int] = set()
        max_candidates = 4000
        candidates: list[bytes] = []
        address = 0

        while address < 0x7FFFFFFFFFFF and checked < max_checked:
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
            data = self._read_process_bytes(pm, base, size)
            if not data:
                continue

            raw_key = self._find_v4_raw_key_in_region(data, db_path)
            if raw_key:
                self._v4_raw_keys.add(raw_key)
                self._v4_raw_keys_by_db[str(db_path)] = raw_key
                return raw_key

            if not verify_candidates:
                continue

            for candidate in self._extract_v4_candidates_from_region(pm, data, seen_ptrs):
                candidates.append(candidate)
                if len(candidates) >= max_candidates:
                    logger.warning("新版微信密钥候选达到上限 %d，开始批量校验", max_candidates)
                    return self._verify_v4_candidates_parallel(candidates, db_path)

        return self._verify_v4_candidates_parallel(candidates, db_path)

        return None

    def _find_weixin_v4_raw_key_for_db(
        self,
        db_path: Path,
        *,
        verify_candidates: bool = True,
    ) -> Optional[bytes]:
        try:
            import pymem
        except ImportError:
            return None
        for process_name in WECHAT_PROCESS_NAMES_WIN:
            for pid in self._iter_process_ids(process_name):
                pm = None
                try:
                    pm = pymem.Pymem(pid)
                    key = self._scan_process_private_memory_for_v4_key(
                        pm,
                        db_path,
                        verify_candidates=verify_candidates,
                    )
                    if key:
                        return key
                except Exception:
                    continue
                finally:
                    if pm is not None:
                        try:
                            pm.close_process()
                        except Exception:
                            pass
        return None

    def _find_v4_raw_key_in_region(self, data: bytes, db_path: Path) -> Optional[bytes]:
        """Find WCDB cached raw keys formatted as x'<64hex_key><32hex_salt>'."""
        try:
            db_salt = db_path.read_bytes()[:WCDB_V4_SALT_SIZE]
        except OSError:
            return None
        salt_hex = db_salt.hex().encode("ascii")
        # Accept both quoted SQL literal form and bare memory fragments.
        for match in re.finditer(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'?", data):
            if match.group(2).lower() == salt_hex:
                try:
                    return bytes.fromhex(match.group(1).decode("ascii"))
                except ValueError:
                    continue
        for match in re.finditer(rb"([0-9a-fA-F]{64})([0-9a-fA-F]{32})", data):
            if match.group(2).lower() == salt_hex:
                try:
                    return bytes.fromhex(match.group(1).decode("ascii"))
                except ValueError:
                    continue
        return None

    def _extract_v4_candidates_from_region(
        self,
        pm: "pymem.Pymem",
        data: bytes,
        seen_ptrs: set[int],
    ) -> Iterable[bytes]:
        for stub_pos in self._find_v4_key_stubs(data):
            ptr = struct.unpack_from("<Q", data, stub_pos)[0]
            if ptr in seen_ptrs:
                continue
            seen_ptrs.add(ptr)
            if not self._looks_like_process_pointer(ptr):
                continue
            candidate = self._read_process_bytes(pm, ptr, KEY_LENGTH)
            if candidate and candidate != b"\x00" * KEY_LENGTH and len(set(candidate)) > 8:
                yield candidate

        # Some Weixin builds keep a small object inline: key bytes followed by
        # the same 0x20/0x2f length markers. Try those 32 bytes as passphrases.
        marker = b"\x20" + b"\x00" * 7 + b"\x2f" + b"\x00" * 7
        start = 0
        while True:
            pos = data.find(marker, start)
            if pos < KEY_LENGTH:
                break
            candidate = data[pos - KEY_LENGTH : pos]
            if candidate and candidate != b"\x00" * KEY_LENGTH and len(set(candidate)) > 8:
                yield candidate
            start = pos + 1

    @staticmethod
    def _find_v4_key_stubs(data: bytes) -> Iterable[int]:
        """Yield offsets matching the Weixin 4.x in-memory raw-key stub."""
        needle = b"\x00\x00" + b"\x00" * 8 + b"\x20" + b"\x00" * 7 + b"\x2f" + b"\x00" * 7
        start = 0
        while True:
            found = data.find(needle, start)
            if found < 6:
                break
            yield found - 6
            start = found + 1

    @staticmethod
    def _read_process_bytes(pm: "pymem.Pymem", address: int, size: int) -> bytes:
        if not address or size <= 0:
            return b""
        try:
            return pm.read_bytes(address, size)
        except Exception:
            return b""

    @staticmethod
    def _looks_like_process_pointer(address: int) -> bool:
        return 0x10000 <= address <= 0x7FFFFFFFFFFF

    def _verify_v4_candidates_parallel(self, candidates: List[bytes], db_path: Path) -> Optional[bytes]:
        unique = list(dict.fromkeys(candidates))
        if not unique:
            return None
        logger.info("开始校验新版微信密钥候选: %d", len(unique))
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 1))) as executor:
            future_map = {
                executor.submit(_verify_weixin_v4_key_bytes, candidate, str(db_path)): candidate
                for candidate in unique
            }
            for future in concurrent.futures.as_completed(future_map):
                candidate = future_map[future]
                try:
                    if future.result():
                        for pending in future_map:
                            pending.cancel()
                        return candidate
                except Exception:
                    continue
        return None

    def _verify_key(self, key: bytes, db_path: Optional[Path] = None) -> bool:
        """Return True when key can open or decrypt the verification database."""
        if len(key) != KEY_LENGTH:
            return False
        target = Path(db_path) if db_path else self._get_verification_db_path()
        if not target.exists():
            return False
        try:
            if self._is_weixin_v4_db(target):
                return self._verify_weixin_v4_key(target, key)
            decrypted = self.decrypt_database(target, key)
            conn = sqlite3.connect(str(decrypted))
            try:
                count = conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()[0]
                return count > 0
            finally:
                conn.close()
        except Exception:
            return False

    def _iter_process_ids(self, process_name: str) -> Iterable[int]:
        try:
            import pymem.process
        except ImportError:
            return []
        wanted = process_name.lower()
        pids: list[int] = []
        for proc in pymem.process.list_processes():
            exe = bytes(proc.szExeFile).split(b"\x00", 1)[0].decode("utf-8", "ignore")
            if exe.lower() == wanted:
                pids.append(int(proc.th32ProcessID))
        return pids

    @staticmethod
    def _find_wechat_module(pm: "pymem.Pymem"):
        try:
            import pymem.process
            for module in pymem.process.enum_process_module(pm.process_handle):
                if module.name and module.name in ("WeChatWin.dll", "Weixin.dll"):
                    return module
        except Exception:
            return None
        return None

    def _get_verification_db_path(self) -> Path:
        wechat_dir = self._wechat_dir or self.find_wechat_dir()
        return self._get_contact_db_path_windows(wechat_dir)

    def _get_contact_db_path_windows(self, wechat_dir: Path) -> Path:
        if self._is_new_windows_layout(wechat_dir):
            return wechat_dir / "db_storage" / "contact" / "contact.db"
        return wechat_dir.parent / MICRO_MSG_DB

    def _is_weixin_v4_db(self, db_path: Path) -> bool:
        try:
            header = db_path.read_bytes()[:WCDB_V4_SALT_SIZE]
        except OSError:
            return False
        return header != SQLITE_HEADER and len(header) == WCDB_V4_SALT_SIZE

    @staticmethod
    def _derive_weixin_v4_keys(passphrase: bytes, salt: bytes) -> Tuple[bytes, bytes]:
        try:
            from Crypto.Hash import SHA512
            from Crypto.Protocol.KDF import PBKDF2
        except ImportError as exc:
            raise RuntimeError("需要安装 pycryptodome 库: pip install pycryptodome") from exc
        key = PBKDF2(
            passphrase,
            salt,
            dkLen=WCDB_V4_KEY_SIZE,
            count=WCDB_V4_KDF_ITER,
            hmac_hash_module=SHA512,
        )
        mac_salt = bytes(x ^ 0x3A for x in salt)
        mac_key = PBKDF2(key, mac_salt, dkLen=WCDB_V4_KEY_SIZE, count=2, hmac_hash_module=SHA512)
        return key, mac_key

    def _verify_weixin_v4_key(self, db_path: Path, passphrase: bytes) -> bool:
        with db_path.open("rb") as f:
            first_page = f.read(WCDB_V4_PAGE_SIZE)
        if len(first_page) < WCDB_V4_PAGE_SIZE:
            return False
        if _verify_weixin_v4_key_material(passphrase, first_page, raw=True):
            self._v4_raw_keys.add(passphrase)
            self._v4_raw_keys_by_db[str(db_path)] = passphrase
            return True
        return _verify_weixin_v4_key_material(passphrase, first_page, raw=False)

    @staticmethod
    def _weixin_v4_reserve_size() -> int:
        reserve = WCDB_V4_IV_SIZE + WCDB_V4_HMAC_SIZE
        return ((reserve + WCDB_V4_AES_BLOCK_SIZE - 1) // WCDB_V4_AES_BLOCK_SIZE) * WCDB_V4_AES_BLOCK_SIZE

    def _decrypt_weixin_v4_database(self, db_path: Path, passphrase: bytes, decrypted_path: Path) -> Path:
        try:
            from Crypto.Cipher import AES
        except ImportError as exc:
            raise RuntimeError("需要安装 pycryptodome 库: pip install pycryptodome") from exc

        reserve = self._weixin_v4_reserve_size()
        with db_path.open("rb") as src, decrypted_path.open("wb") as dst:
            salt = src.read(WCDB_V4_SALT_SIZE)
            if len(salt) != WCDB_V4_SALT_SIZE:
                raise RuntimeError("数据库文件为空或已损坏")
            if passphrase in self._v4_raw_keys:
                key = passphrase
                mac_salt = bytes(x ^ 0x3A for x in salt)
                try:
                    from Crypto.Hash import SHA512
                    from Crypto.Protocol.KDF import PBKDF2
                except ImportError as exc:
                    raise RuntimeError("需要安装 pycryptodome 库: pip install pycryptodome") from exc
                mac_key = PBKDF2(key, mac_salt, dkLen=WCDB_V4_KEY_SIZE, count=2, hmac_hash_module=SHA512)
            else:
                key, mac_key = self._derive_weixin_v4_keys(passphrase, salt)
            dst.write(SQLITE_HEADER)

            page_no = 1
            while True:
                if page_no == 1:
                    tail = src.read(WCDB_V4_PAGE_SIZE - WCDB_V4_SALT_SIZE)
                    if not tail:
                        break
                    page = salt + tail
                    offset = WCDB_V4_SALT_SIZE
                else:
                    page = src.read(WCDB_V4_PAGE_SIZE)
                    if not page:
                        break
                    offset = 0

                if len(page) < WCDB_V4_PAGE_SIZE:
                    raise RuntimeError(f"数据库页不完整: page={page_no}")
                if all(byte == 0 for byte in page):
                    dst.write(page[offset:])
                    break

                mac_data = page[offset : WCDB_V4_PAGE_SIZE - reserve + WCDB_V4_IV_SIZE]
                digest = hmac.new(mac_key, mac_data, "sha512")
                digest.update(struct.pack("<I", page_no))
                mac_start = WCDB_V4_PAGE_SIZE - reserve + WCDB_V4_IV_SIZE
                if digest.digest() != page[mac_start : mac_start + WCDB_V4_HMAC_SIZE]:
                    raise RuntimeError(f"HMAC 校验失败: page={page_no}")

                iv_start = WCDB_V4_PAGE_SIZE - reserve
                iv = page[iv_start : iv_start + WCDB_V4_IV_SIZE]
                encrypted = page[offset : WCDB_V4_PAGE_SIZE - reserve]
                plain = AES.new(key, AES.MODE_CBC, iv).decrypt(encrypted)
                dst.write(plain)
                dst.write(page[WCDB_V4_PAGE_SIZE - reserve :])
                page_no += 1

        try:
            conn = sqlite3.connect(str(decrypted_path))
            conn.execute("PRAGMA schema_version;").fetchone()
            conn.close()
        except Exception as exc:
            if decrypted_path.exists():
                decrypted_path.unlink()
            raise RuntimeError(f"新版微信数据库解密后校验失败: {exc}") from exc
        return decrypted_path

    def decrypt_database(
        self,
        db_path: Path,
        key: bytes,
        *,
        refresh_v4_key: bool = True,
        verify_v4_candidates: bool = True,
    ) -> Path:
        """Decrypt a WeChat SQLCipher/WCDB database into a temporary SQLite file."""
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")

        cache_key = str(db_path)
        source_signature = (db_path.stat().st_mtime_ns, db_path.stat().st_size)
        if cache_key in self._decrypted_dbs:
            cached = self._decrypted_dbs[cache_key]
            if cached.exists() and self._decrypted_db_signatures.get(cache_key) == source_signature:
                return cached
            self._close_connection_for_path(str(cached))
            if cached.exists():
                try:
                    cached.unlink()
                except OSError:
                    logger.debug("无法删除过期解密缓存: %s", cached, exc_info=True)
            self._decrypted_dbs.pop(cache_key, None)
            self._decrypted_db_signatures.pop(cache_key, None)

        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="wechat_decrypt_"))
            logger.info("创建临时解密目录: %s", self._temp_dir)

        decrypted_path = self._temp_dir / f"decrypted_{db_path.name}"

        try:
            if self._is_weixin_v4_db(db_path):
                db_key = self._v4_raw_keys_by_db.get(str(db_path), key)
                try:
                    self._decrypt_weixin_v4_database(db_path, db_key, decrypted_path)
                except Exception:
                    if not refresh_v4_key:
                        raise
                    refreshed_key = self._find_weixin_v4_raw_key_for_db(
                        db_path,
                        verify_candidates=verify_v4_candidates,
                    )
                    if not refreshed_key or refreshed_key == db_key:
                        raise
                    self._decrypt_weixin_v4_database(db_path, refreshed_key, decrypted_path)
            else:
                self._decrypt_sqlcipher_database(db_path, key, decrypted_path)

            self._decrypted_dbs[cache_key] = decrypted_path
            self._decrypted_db_signatures[cache_key] = source_signature
            logger.info("数据库解密成功: %s -> %s", db_path.name, decrypted_path)
            return decrypted_path
        except Exception as e:
            if decrypted_path.exists():
                decrypted_path.unlink()
            raise RuntimeError(f"数据库解密失败 ({db_path.name}): {e}") from e

    def _decrypt_sqlcipher_database(self, db_path: Path, key: bytes, decrypted_path: Path) -> Path:
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher
        except ImportError as exc:
            raise RuntimeError("需要安装 pysqlcipher3 库: pip install pysqlcipher3") from exc

        hex_key = key.hex()
        conn = sqlcipher.connect(str(db_path))
        try:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA key = \"x'{hex_key}'\";")
            cursor.execute(f"PRAGMA cipher_page_size = {SQLCIPHER_PAGE_SIZE};")
            cursor.execute(f"PRAGMA kdf_iter = {SQLCIPHER_KDF_ITER};")
            cursor.execute(f"PRAGMA cipher_compatibility = {SQLCIPHER_CIPHER_COMPAT};")
            cursor.execute("SELECT count(*) FROM sqlite_master;")
            escaped = str(decrypted_path).replace("'", "''")
            cursor.execute(f"ATTACH DATABASE '{escaped}' AS plaintext KEY '';")
            cursor.execute("SELECT sqlcipher_export('plaintext');")
            cursor.execute("DETACH DATABASE plaintext;")
        finally:
            conn.close()
        return decrypted_path

    # ------------------------------------------------------------------
    # 联系人提取
    # ------------------------------------------------------------------

    def get_contacts(self) -> List[dict]:
        """
        提取所有联系人信息（自动检测平台）。

        - Windows: 从 MicroMsg.db 的 Contact 表读取。
        - macOS: 从 wccontact_new2.db 的 WCContact 表读取。

        Returns:
            联系人字典列表，每个字典包含:
            UserName, Alias, Remark, NickName, Type

        Raises:
            OSError: 平台不受支持。
            RuntimeError: 数据库操作失败。
        """
        plat = _ensure_supported_platform()
        wechat_dir = self._wechat_dir or self.find_wechat_dir()
        key = self._decrypt_key or self.get_decrypt_key()

        if plat == "Darwin":
            return self._get_contacts_mac(wechat_dir, key)
        else:
            return self._get_contacts_windows(wechat_dir, key)

    def _get_contacts_windows(self, wechat_dir: Path, key: bytes) -> List[dict]:
        """Extract contacts from the Windows contact database."""
        micro_msg_path = self._get_contact_db_path_windows(wechat_dir)
        if not micro_msg_path.exists():
            raise FileNotFoundError(f"联系人数据库不存在: {micro_msg_path}")

        decrypted_path = self.decrypt_database(micro_msg_path, key)
        conn = self._get_connection(str(decrypted_path))

        try:
            if self._is_new_windows_layout(wechat_dir):
                cursor = conn.execute(
                    "SELECT username, alias, remark, nick_name, local_type "
                    "FROM contact "
                    "WHERE username NOT LIKE 'gh_%' "
                    "AND ifnull(delete_flag, 0) = 0 "
                    "ORDER BY remark, nick_name;"
                )
            else:
                cursor = conn.execute(
                    "SELECT UserName, Alias, Remark, NickName, Type "
                    "FROM Contact "
                    "WHERE UserName NOT LIKE 'gh_%' "
                    "ORDER BY Remark, NickName;"
                )
            columns = ["UserName", "Alias", "Remark", "NickName", "Type"]
            contacts = [dict(zip(columns, row)) for row in cursor.fetchall()]
            logger.info("提取到 %d 个联系人", len(contacts))
            return contacts
        except sqlite3.OperationalError as e:
            raise RuntimeError(f"读取联系人失败: {e}") from e

    def _get_contacts_mac(self, wechat_dir: Path, key: bytes) -> List[dict]:
        """
        从 macOS wccontact_new2.db 提取联系人。

        macOS 联系人表为 WCContact，列名与 Windows 不同：
        userName, dbContactRemark, dbContactNickName, dbContactAlias, type
        """
        contact_db_path = wechat_dir / "Contacts" / CONTACT_DB_MAC
        if not contact_db_path.exists():
            raise FileNotFoundError(
                f"联系人数据库不存在: {contact_db_path}"
            )

        decrypted_path = self.decrypt_database(contact_db_path, key)
        conn = self._get_connection(str(decrypted_path))

        try:
            cursor = conn.execute(
                "SELECT userName, dbContactAlias, dbContactRemark, "
                "dbContactNickName, type "
                "FROM WCContact "
                "WHERE userName NOT LIKE 'gh_%' "
                "ORDER BY dbContactRemark, dbContactNickName;"
            )
            # 映射 macOS 列名到统一的 Windows 列名，保持下游兼容
            contacts = []
            for row in cursor.fetchall():
                contacts.append({
                    "UserName": row[0],
                    "Alias": row[1],
                    "Remark": row[2],
                    "NickName": row[3],
                    "Type": row[4],
                })
            logger.info("提取到 %d 个联系人 (macOS)", len(contacts))
            return contacts
        except sqlite3.OperationalError as e:
            raise RuntimeError(f"读取联系人失败 (macOS): {e}") from e

    # ------------------------------------------------------------------
    # 消息提取
    # ------------------------------------------------------------------

    def _get_messages_windows_new(
        self,
        wechat_dir: Path,
        key: bytes,
        contact_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[dict]:
        """Extract messages from the Weixin 4.x db_storage/message layout."""
        time_conditions = []
        params: list = []
        if start_date:
            time_conditions.append("AND create_time >= ?")
            params.append(int(datetime.strptime(start_date, "%Y-%m-%d").timestamp()))
        if end_date:
            params.append(int(datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp()))
            time_conditions.append("AND create_time <= ?")
        time_clause = " ".join(time_conditions)

        msg_parent_dir = wechat_dir / "db_storage" / "message"
        if not msg_parent_dir.exists():
            logger.warning("消息数据库目录不存在: %s", msg_parent_dir)
            return []

        all_messages: List[dict] = []
        for db_path in sorted(msg_parent_dir.glob("message_*.db")):
            if not db_path.stem.split("_")[-1].isdigit():
                continue
            try:
                decrypted_path = self.decrypt_database(
                    db_path,
                    key,
                    refresh_v4_key=True,
                    verify_v4_candidates=False,
                )
                conn = self._get_connection(str(decrypted_path))
                name_row = conn.execute("SELECT rowid FROM Name2Id WHERE user_name = ?", (contact_id,)).fetchone()
                if not name_row:
                    continue
                self_rowid = self._get_current_account_name2id(conn, wechat_dir)
                table_name = f"Msg_{hashlib.md5(contact_id.encode('utf-8')).hexdigest()}"
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
                ).fetchone()
                if not exists:
                    continue

                for name in [table_name]:
                    query = (
                        f"SELECT local_id, server_id, local_type, real_sender_id, create_time, sort_seq, "
                        f"source, message_content, compress_content, packed_info_data FROM {name} "
                        f"WHERE 1=1 {time_clause} ORDER BY create_time ASC"
                    )
                    try:
                        rows = conn.execute(query, params).fetchall()
                    except sqlite3.OperationalError:
                        continue
                    if not rows:
                        continue
                    for row_msg in rows:
                        real_sender_id = row_msg[3]
                        attachments = self._resolve_message_attachments(
                            wechat_dir=wechat_dir,
                            local_id=row_msg[0],
                            msg_type=row_msg[2],
                            create_time=row_msg[4],
                            source=row_msg[6],
                            message_content=row_msg[7],
                            compress_content=row_msg[8],
                            packed_info_data=row_msg[9],
                        )
                        content = self._decode_message_payload(row_msg[7], row_msg[8], row_msg[2])
                        all_messages.append({
                            "localId": row_msg[0],
                            "TalkerId": name_row[0],
                            "MsgSvrID": row_msg[1],
                            "Type": row_msg[2],
                            "SubType": 0,
                            "IsSender": self._is_windows_new_self_message(real_sender_id, self_rowid, name_row[0]),
                            "RealSenderId": real_sender_id,
                            "CreateTime": row_msg[4],
                            "Sequence": row_msg[5],
                            "StrTalker": contact_id,
                            "StrContent": content,
                            "DisplayContent": content,
                            "Attachments": attachments,
                            "CreateTimeStr": datetime.fromtimestamp(row_msg[4]).strftime("%Y-%m-%d %H:%M:%S") if row_msg[4] else "",
                        })
                    break
            except RuntimeError as e:
                logger.warning("处理 %s 失败: %s", db_path.name, e)
                continue

        all_messages.sort(key=lambda m: m.get("CreateTime", 0))
        logger.info("提取到 %d 条新版微信消息 (联系人: %s)", len(all_messages), contact_id)
        return all_messages

    @staticmethod
    def _get_current_account_wxid(wechat_dir: Path) -> str:
        """Best-effort account wxid from a Windows xwechat account directory."""
        name = wechat_dir.name
        match = re.match(r"^(wxid_[A-Za-z0-9_-]+)_[0-9a-fA-F]{4}$", name)
        if match:
            return match.group(1)
        return name if name.startswith("wxid_") else ""

    def _get_current_account_name2id(self, conn: sqlite3.Connection, wechat_dir: Path) -> Optional[int]:
        account_wxid = self._get_current_account_wxid(wechat_dir)
        if not account_wxid:
            return None
        try:
            row = conn.execute(
                "SELECT rowid FROM Name2Id WHERE user_name = ?",
                (account_wxid,),
            ).fetchone()
        except sqlite3.Error:
            return None
        return int(row[0]) if row else None

    @staticmethod
    def _is_windows_new_self_message(
        real_sender_id: object,
        self_rowid: Optional[int],
        contact_rowid: object,
    ) -> int:
        """Return 1 if a Weixin 4.x message was sent by the current account.

        In the db_storage/message layout, real_sender_id points into Name2Id.
        Direct chats use the contact's rowid for incoming messages and the
        current account's rowid for outgoing messages. Older layouts sometimes
        use 0 for self, so keep that fallback.
        """
        try:
            sender_id = int(real_sender_id)
        except (TypeError, ValueError):
            return 0

        if sender_id == 0:
            return 1
        if self_rowid is not None:
            return 1 if sender_id == int(self_rowid) else 0
        try:
            return 0 if sender_id == int(contact_rowid) else 1
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _decode_message_payload(value: object, compressed: object = None, msg_type: Optional[int] = None) -> str:
        effective_type = (msg_type or 0) & 0xFFFFFFFF
        labels = {
            3: "[图片]",
            34: "[语音]",
            43: "[视频]",
            47: "[表情]",
            48: "[位置]",
            49: "[文件/链接]",
            50: "[通话]",
            10000: "[系统消息]",
        }
        payload = value if value not in (None, b"", "") else compressed
        if effective_type in labels and payload in (None, b"", ""):
            return labels[effective_type]
        value = payload
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        elif isinstance(value, bytes):
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                text = value.decode("utf-8", errors="ignore")
        else:
            text = str(value)

        text = "".join(ch if ch in "\r\n\t" or ord(ch) >= 32 else "" for ch in text)
        text = text.strip()
        if text.startswith("(/`"):
            return labels.get(effective_type, "[非文本消息]")
        if not text:
            return labels.get(effective_type, "")

        sender_markers = list(re.finditer(r"(?:wxid_[A-Za-z0-9_-]+|[A-Za-z0-9_.-]+@(?:chatroom|openim)|gh_[A-Za-z0-9_-]+):\n", text))
        if sender_markers:
            text = text[sender_markers[-1].start():].strip()
        revoke_match = re.search(r"[^<>\s]{1,32}撤回了一条消息", text)
        if revoke_match:
            return revoke_match.group(0)
        return text

    def _resolve_message_attachments(
        self,
        wechat_dir: Path,
        local_id: Optional[int],
        msg_type: Optional[int],
        create_time: Optional[int],
        source: object = None,
        message_content: object = None,
        compress_content: object = None,
        packed_info_data: object = None,
    ) -> List[dict]:
        effective_type = (msg_type or 0) & 0xFFFFFFFF
        if effective_type not in {3, 43, 49}:
            return []

        blobs = [source, message_content, compress_content, packed_info_data]
        ids = self._extract_resource_ids(*blobs)
        candidates: List[Path] = []
        month = datetime.fromtimestamp(create_time).strftime("%Y-%m") if create_time else None

        if effective_type == 3:
            for resource_id in ids:
                candidates.extend(self._find_attachment_files(
                    wechat_dir / "msg" / "attach",
                    resource_id,
                    month=month,
                    preferred_dirs={"Img"},
                ))
            candidates.extend(self._find_cached_image_thumbnails(
                wechat_dir,
                local_id=local_id,
                month=month,
            ))
        elif effective_type == 43:
            for resource_id in ids:
                candidates.extend(self._find_attachment_files(
                    wechat_dir / "msg" / "video",
                    resource_id,
                    month=month,
                ))
                candidates.extend(self._find_attachment_files(
                    wechat_dir / "msg" / "attach",
                    resource_id,
                    month=month,
                    preferred_dirs={"Video"},
                ))
        elif effective_type == 49:
            filenames = self._extract_probable_filenames(*blobs)
            for resource_id in ids:
                candidates.extend(self._find_attachment_files(wechat_dir / "msg" / "attach", resource_id, month=month))
                candidates.extend(self._find_attachment_files(wechat_dir / "msg" / "file", resource_id, month=month))
            for filename in filenames:
                candidates.extend(self._find_attachment_files(wechat_dir / "msg" / "file", filename, month=month, exact=True))
                candidates.extend(self._find_attachment_files(wechat_dir / "msg" / "attach", filename, month=month, exact=True))

        seen: set[str] = set()
        attachments: List[dict] = []
        for path in candidates:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            key = str(resolved).lower()
            if key in seen or not resolved.is_file():
                continue
            seen.add(key)
            stat = resolved.stat()
            attachments.append({
                "path": str(resolved),
                "name": resolved.name,
                "size": stat.st_size,
                "type": self._classify_attachment(resolved, effective_type),
                "mime": mimetypes.guess_type(str(resolved))[0] or "",
            })
        attachments.sort(key=lambda item: (item["type"] == "thumbnail", item["name"]))
        return attachments

    @staticmethod
    def _extract_resource_ids(*values: object) -> List[str]:
        ids: List[str] = []
        for value in values:
            if value in (None, "", b""):
                continue
            if isinstance(value, bytes):
                text = value.decode("utf-8", errors="ignore")
            else:
                text = str(value)
            for match in re.findall(r"(?<![A-Fa-f0-9])([A-Fa-f0-9]{16}|[A-Fa-f0-9]{32})(?![A-Fa-f0-9])", text):
                if match.lower() not in ids:
                    ids.append(match.lower())
        return ids

    @staticmethod
    def _extract_probable_filenames(*values: object) -> List[str]:
        filenames: List[str] = []
        pattern = r"[\w\u4e00-\u9fa5][\w\u4e00-\u9fa5 ._()（）【】\[\]-]{1,120}\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|jpg|jpeg|png|mp4|mov)"
        for value in values:
            if value in (None, "", b""):
                continue
            text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                marker = re.search(r"[\u4e00-\u9fa5A-Za-z0-9][\w\u4e00-\u9fa5 ._()（）【】\[\]-]*\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|jpg|jpeg|png|mp4|mov)$", match.strip(), flags=re.IGNORECASE)
                name = marker.group(0).strip() if marker else match.strip()
                if name not in filenames:
                    filenames.append(name)
        return filenames

    @staticmethod
    def _find_attachment_files(root: Path, token: str, month: Optional[str] = None, preferred_dirs: Optional[set[str]] = None, exact: bool = False) -> List[Path]:
        if not root.exists():
            return []
        search_roots = [root]
        if month:
            monthly_roots = [p for p in root.glob(f"*/{month}") if p.is_dir()]
            if monthly_roots:
                search_roots = monthly_roots
        matches: List[Path] = []
        token_lower = token.lower()
        for base in search_roots:
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                if preferred_dirs and not any(part in preferred_dirs for part in path.parts):
                    continue
                name_lower = path.name.lower()
                if (exact and name_lower == token_lower) or (not exact and token_lower in name_lower):
                    matches.append(path)
        return matches

    @staticmethod
    def _find_cached_image_thumbnails(
        wechat_dir: Path,
        local_id: Optional[int],
        month: Optional[str] = None,
    ) -> List[Path]:
        if local_id is None:
            return []
        root = wechat_dir / "cache"
        if not root.exists():
            return []
        search_roots = [root / month] if month and (root / month).is_dir() else [root]
        matches: List[Path] = []
        pattern = f"{int(local_id)}_*_thumb.*"
        for base in search_roots:
            for path in base.glob(f"Message/*/Thumb/{pattern}"):
                if path.is_file():
                    matches.append(path)
        return matches

    @staticmethod
    def _classify_attachment(path: Path, msg_type: int) -> str:
        name = path.name.lower()
        if msg_type == 3:
            return "thumbnail" if name.endswith("_t.dat") else "image"
        if msg_type == 43:
            return "video"
        return "file"

    def get_messages(
        self,
        contact_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[dict]:
        """
        提取指定联系人的聊天消息。

        - Windows: 遍历 Multi/MSG0.db ~ MSG9.db
        - macOS: 遍历 Message/msg_0.db ~ msg_9.db

        两个平台的消息表名均为 MSG，列结构相同。

        Args:
            contact_id: 联系人的 UserName（wxid 或群ID）。
            start_date: 起始日期，格式 "YYYY-MM-DD"，可选。
            end_date: 结束日期，格式 "YYYY-MM-DD"，可选。

        Returns:
            消息字典列表，按时间排序，每个字典包含:
            localId, TalkerId, MsgSvrID, Type, SubType, IsSender,
            CreateTime, Sequence, StrTalker, StrContent, DisplayContent
        """
        wechat_dir = self._wechat_dir or self.find_wechat_dir()
        key = self._decrypt_key or self.get_decrypt_key()

        if CURRENT_PLATFORM != "Darwin" and self._is_new_windows_layout(wechat_dir):
            return self._get_messages_windows_new(
                wechat_dir, key, contact_id, start_date=start_date, end_date=end_date
            )

        # 构建时间戳过滤条件
        time_conditions = []
        params: list = [contact_id]

        if start_date:
            start_ts = int(
                datetime.strptime(start_date, "%Y-%m-%d").timestamp()
            )
            time_conditions.append("AND CreateTime >= ?")
            params.append(start_ts)

        if end_date:
            # 结束日期取当天 23:59:59
            end_ts = int(
                datetime.strptime(end_date, "%Y-%m-%d")
                .replace(hour=23, minute=59, second=59)
                .timestamp()
            )
            time_conditions.append("AND CreateTime <= ?")
            params.append(end_ts)

        time_clause = " ".join(time_conditions)

        # 消息表的列定义
        msg_columns = [
            "localId", "TalkerId", "MsgSvrID", "Type", "SubType",
            "IsSender", "CreateTime", "Sequence", "StrTalker",
            "StrContent", "DisplayContent",
        ]
        select_cols = ", ".join(msg_columns)

        all_messages: List[dict] = []

        # 根据平台确定数据库文件位置和命名模式
        if CURRENT_PLATFORM == "Darwin":
            msg_parent_dir = wechat_dir / "Message"
            db_pattern = MSG_DB_PATTERN_MAC
        else:
            msg_parent_dir = wechat_dir / "Multi"
            db_pattern = MSG_DB_PATTERN_WIN
            if self._is_new_windows_layout(wechat_dir):
                msg_parent_dir = wechat_dir / "db_storage" / "message"
                db_pattern = "message_{n}.db"

        if not msg_parent_dir.exists():
            logger.warning("消息数据库目录不存在: %s", msg_parent_dir)
            return []

        for n in range(MSG_DB_COUNT):
            db_name = db_pattern.format(n=n)
            db_path = msg_parent_dir / db_name

            if not db_path.exists():
                continue

            try:
                decrypted_path = self.decrypt_database(db_path, key)
                conn = self._get_connection(str(decrypted_path))

                # 查询消息表（表名为 MSG，两个平台相同）
                query = (
                    f"SELECT {select_cols} FROM MSG "
                    f"WHERE StrTalker = ? {time_clause} "
                    "ORDER BY CreateTime ASC;"
                )

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

                for row in rows:
                    msg = dict(zip(msg_columns, row))
                    # 将 Unix 时间戳转换为可读时间
                    if msg.get("CreateTime"):
                        msg["CreateTimeStr"] = datetime.fromtimestamp(
                            msg["CreateTime"]
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    all_messages.append(msg)

            except sqlite3.OperationalError as e:
                # 某些 MSG 数据库可能没有 MSG 表，跳过
                logger.debug("跳过 %s: %s", db_name, e)
                continue
            except RuntimeError as e:
                logger.warning("处理 %s 失败: %s", db_name, e)
                continue

        # 按时间排序（跨数据库合并后重新排序）
        all_messages.sort(key=lambda m: m.get("CreateTime", 0))

        logger.info(
            "提取到 %d 条消息 (联系人: %s)", len(all_messages), contact_id
        )
        return all_messages

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _get_connection(self, db_path: str) -> sqlite3.Connection:
        """
        获取或创建数据库连接（连接池管理）。
        """
        cache_key = (threading.get_ident(), db_path)
        if cache_key not in self._connections:
            conn = sqlite3.connect(db_path)
            conn.text_factory = self._decode_sqlite_text
            conn.row_factory = None
            self._connections[cache_key] = conn
        return self._connections[cache_key]

    def _close_connection_for_path(self, db_path: str) -> None:
        for cache_key, conn in list(self._connections.items()):
            if cache_key[1] != db_path:
                continue
            try:
                conn.close()
            except Exception:
                pass
            self._connections.pop(cache_key, None)

    @staticmethod
    def _decode_sqlite_text(value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------------

    def close(self) -> None:
        """
        关闭所有数据库连接并清理临时文件。

        应在完成所有提取操作后调用，或使用上下文管理器自动调用。
        """
        # 关闭所有数据库连接
        for path, conn in self._connections.items():
            try:
                conn.close()
                logger.debug("关闭数据库连接: %s", path)
            except Exception as e:
                logger.warning("关闭数据库连接失败 (%s): %s", path, e)
        self._connections.clear()

        # 清理临时解密文件
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
                logger.info("已清理临时目录: %s", self._temp_dir)
            except OSError as e:
                logger.warning("清理临时目录失败: %s", e)
            self._temp_dir = None

        self._decrypted_dbs.clear()
        self._decrypted_db_signatures.clear()
        self._decrypt_key = None
