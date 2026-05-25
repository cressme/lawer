from __future__ import annotations

import ctypes
import re
import struct
import time
from collections import Counter
from pathlib import Path

import pymem.process
import yara
from Crypto.Cipher import AES


ROOT = Path(r"C:\Users\danxiao\Documents\xwechat_files\wxid_yru025lka77c52_4b83\msg")
IMAGE = Path(
    r"C:\Users\danxiao\Documents\xwechat_files\wxid_yru025lka77c52_4b83\msg\attach"
    r"\4b9bdbc24fa456514c7239718ceb1090\2025-08\Img"
    r"\13f6786a71341a0a2551d24e12fbadf3_t.dat"
)


def month_key(path: Path) -> str:
    match = re.search(r"(\d{4}-\d{2})", str(path))
    return match.group(1) if match else "0000-00"


def verify(encrypted: bytes, key: bytes) -> bool:
    try:
        text = AES.new(key[:16], AES.MODE_ECB).decrypt(encrypted)
    except Exception:
        return False
    return text.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"wxgf"))


def open_process(pid: int):
    return ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, pid)


def read_process_memory(handle, address: int, size: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(bytes_read),
    )
    return buffer.raw if ok else None


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
    ]


def get_memory_regions(handle):
    regions = []
    mbi = MemoryBasicInformation()
    address = 0
    while ctypes.windll.kernel32.VirtualQueryEx(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(mbi),
        ctypes.sizeof(mbi),
    ):
        if mbi.State == 0x1000 and mbi.Type == 0x20000:
            regions.append((int(mbi.BaseAddress or address), int(mbi.RegionSize or 0)))
        size = int(mbi.RegionSize or 0)
        if size <= 0:
            break
        address = int(mbi.BaseAddress or address) + size
        if address > 0x7FFFFFFFFFFF:
            break
    return regions


def main() -> None:
    started = time.monotonic()
    files = sorted(ROOT.rglob("*_t.dat"), key=month_key, reverse=True)
    tails = []
    for file in files[:16]:
        with file.open("rb") as fh:
            fh.seek(-2, 2)
            tails.append(fh.read(2))
    x, y = Counter(tails).most_common(1)[0][0]
    xor_key = x ^ 0xFF
    print("xor", xor_key, "valid", xor_key == (y ^ 0xD9), "templates", len(files))

    with IMAGE.open("rb") as fh:
        signature, aes_size, xor_size = struct.unpack("<6sLLx", fh.read(0xF))
        ciphertext = fh.read(16)
    print("header", signature, aes_size, xor_size, ciphertext.hex())

    rules = yara.compile(
        source=r"""
        rule AesKey {
            strings:
                $pattern = /[^a-z0-9][a-z0-9]{32}[^a-z0-9]/
            condition:
                $pattern
        }
        """
    )

    pids = []
    for proc in pymem.process.list_processes():
        exe = bytes(proc.szExeFile).split(b"\x00", 1)[0].decode("utf-8", "ignore").lower()
        if exe in {"weixin.exe", "wechat.exe", "wechatappex.exe"}:
            pids.append(int(proc.th32ProcessID))
    print("pids", pids)

    for pid in pids:
        handle = open_process(pid)
        regions = get_memory_regions(handle)
        print("pid", pid, "regions", len(regions))
        checked = 0
        candidates = 0
        for base, size in regions:
            if checked >= 800:
                break
            if size <= 0 or size > 64 * 1024 * 1024:
                continue
            data = read_process_memory(handle, base, size)
            checked += 1
            if not data:
                continue
            for match in rules.match(data=data):
                for string in match.strings:
                    for instance in string.instances:
                        candidate = instance.matched_data[1:-1]
                        candidates += 1
                        if verify(ciphertext, candidate):
                            print("FOUND", pid, candidate[:16])
                            return
        print("checked", checked, "candidates", candidates, "elapsed", round(time.monotonic() - started, 1), flush=True)

    print("NOT FOUND")


if __name__ == "__main__":
    main()
