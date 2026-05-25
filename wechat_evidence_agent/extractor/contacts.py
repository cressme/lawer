"""
联系人管理模块

提供联系人数据的封装、搜索和显示名称解析功能。
支持按关键字模糊搜索，以及按联系人类型筛选。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional


class ContactType(IntEnum):
    """微信联系人类型枚举"""
    FRIEND = 3          # 好友
    GROUP = 2           # 群聊
    OFFICIAL = 33       # 公众号
    ENTERPRISE = 64     # 企业微信联系人
    UNKNOWN = 0


@dataclass
class Contact:
    """单个联系人的数据模型"""
    user_name: str          # 微信内部ID（wxid_xxx 或群ID）
    alias: str = ""         # 微信号（用户自定义）
    remark: str = ""        # 备注名
    nick_name: str = ""     # 昵称
    contact_type: int = 0   # 联系人类型标记
    extra: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """返回最佳显示名称：备注 > 昵称 > 微信号 > wxid"""
        return self.remark or self.nick_name or self.alias or self.user_name


class ContactManager:
    """
    联系人管理器

    封装联系人列表，提供搜索、筛选和显示名称解析功能。
    数据源来自 WeChatDBExtractor.get_contacts() 的返回结果。

    用法示例::

        extractor = WeChatDBExtractor()
        raw_contacts = extractor.get_contacts()
        manager = ContactManager(raw_contacts)
        results = manager.search_contact("张三")
    """

    def __init__(self, contacts: Optional[List[dict]] = None) -> None:
        """
        初始化联系人管理器。

        Args:
            contacts: 联系人字典列表，每个字典应包含以下字段：
                      UserName, Alias, Remark, NickName, Type
        """
        self._contacts: List[Contact] = []
        if contacts:
            self.load_contacts(contacts)

    def load_contacts(self, contacts: List[dict]) -> None:
        """
        加载联系人数据。

        Args:
            contacts: 从数据库提取的原始联系人字典列表。
        """
        self._contacts = []
        for raw in contacts:
            contact = Contact(
                user_name=raw.get("UserName", ""),
                alias=raw.get("Alias", ""),
                remark=raw.get("Remark", ""),
                nick_name=raw.get("NickName", ""),
                contact_type=raw.get("Type", 0),
                extra={k: v for k, v in raw.items()
                       if k not in ("UserName", "Alias", "Remark", "NickName", "Type")},
            )
            self._contacts.append(contact)

    @property
    def count(self) -> int:
        """联系人总数"""
        return len(self._contacts)

    # ------------------------------------------------------------------
    # 搜索功能
    # ------------------------------------------------------------------

    def search_contact(self, keyword: str) -> List[Contact]:
        """
        模糊搜索联系人。

        在备注名、昵称、微信号、wxid 中查找包含关键字的联系人。
        搜索不区分大小写。

        Args:
            keyword: 搜索关键字，支持正则表达式。

        Returns:
            匹配的联系人列表。
        """
        if not keyword or not keyword.strip():
            return []

        keyword = keyword.strip()

        # 尝试按正则匹配，如果关键字不是有效正则则退回到普通包含匹配
        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error:
            # 关键字包含特殊正则字符时，转义后再编译
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        results: List[Contact] = []
        for contact in self._contacts:
            searchable_fields = [
                contact.user_name,
                contact.alias,
                contact.remark,
                contact.nick_name,
            ]
            if any(pattern.search(f) for f in searchable_fields if f):
                results.append(contact)

        return results

    # ------------------------------------------------------------------
    # 显示名称解析
    # ------------------------------------------------------------------

    def get_contact_display_name(self, wxid: str) -> str:
        """
        获取联系人的最佳显示名称。

        优先级：备注名 > 昵称 > 微信号 > wxid

        Args:
            wxid: 微信内部ID（UserName字段）。

        Returns:
            最佳显示名称字符串。若找不到对应联系人则直接返回wxid。
        """
        for contact in self._contacts:
            if contact.user_name == wxid:
                return contact.display_name
        # 未找到联系人记录，直接返回原始ID
        return wxid

    # ------------------------------------------------------------------
    # 列表与筛选
    # ------------------------------------------------------------------

    def list_contacts(self, contact_type: Optional[int] = None) -> List[Contact]:
        """
        列出所有联系人，可按类型筛选。

        Args:
            contact_type: 联系人类型（参考 ContactType 枚举）。
                          为 None 时返回所有联系人。

        Returns:
            联系人列表。
        """
        if contact_type is None:
            return list(self._contacts)

        return [c for c in self._contacts if c.contact_type == contact_type]

    def get_contact(self, wxid: str) -> Optional[Contact]:
        """
        按wxid精确获取单个联系人。

        Args:
            wxid: 微信内部ID。

        Returns:
            Contact 对象，未找到时返回 None。
        """
        for contact in self._contacts:
            if contact.user_name == wxid:
                return contact
        return None

    def list_groups(self) -> List[Contact]:
        """列出所有群聊。群聊的UserName通常以 '@chatroom' 结尾。"""
        return [c for c in self._contacts if c.user_name.endswith("@chatroom")]

    def __repr__(self) -> str:
        return f"<ContactManager contacts={self.count}>"
