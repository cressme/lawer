"""
微信消息解析模块

负责将从数据库提取的原始消息记录解析为结构化的 ParsedMessage 对象。
支持的消息类型：
- Type 1:  文本消息
- Type 3:  图片
- Type 34: 语音
- Type 43: 视频
- Type 47: 表情/贴纸
- Type 48: 位置 (XML)
- Type 49: 应用消息 (文件/小程序/引用/转账/红包)
- Type 10000: 系统消息 (撤回、群变更等)
- Type 10002: 系统消息 (群邀请等)
"""

from __future__ import annotations

import enum
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 消息类型枚举
# ---------------------------------------------------------------------------

class MessageType(enum.Enum):
    """微信消息类型枚举。"""
    TEXT = 1
    IMAGE = 3
    VOICE = 34
    VIDEO = 43
    EMOJI = 47
    LOCATION = 48
    APP = 49
    SYSTEM = 10000
    SYSTEM_EXT = 10002
    UNKNOWN = -1


class AppSubType(enum.IntEnum):
    """Type 49 应用消息的子类型。"""
    FILE = 6
    MINI_PROGRAM = 33
    MINI_PROGRAM_ALT = 36
    REFERENCE = 57
    TRANSFER = 2000
    RED_PACKET = 2001


# ---------------------------------------------------------------------------
# 解析结果数据类
# ---------------------------------------------------------------------------

@dataclass
class ParsedMessage:
    """
    解析后的微信消息结构。

    Attributes:
        msg_id: 消息的服务端唯一 ID (MsgSvrID)。
        sender: 发送者标识。
        receiver: 接收者标识。
        timestamp: 消息创建时间 (Unix 时间戳)。
        msg_type: 消息类型枚举。
        content: 消息文本内容。
        media_path: 关联媒体文件路径 (图片/语音/视频等)。
        extra_data: 消息的附加结构化数据 (因消息类型而异)。
        is_sender: 是否为当前用户发送的消息。
    """
    msg_id: int
    sender: str
    receiver: str
    timestamp: int
    msg_type: MessageType
    content: str = ""
    media_path: str = ""
    extra_data: Dict[str, Any] = field(default_factory=dict)
    is_sender: bool = False


# ---------------------------------------------------------------------------
# 消息解析器
# ---------------------------------------------------------------------------

class MessageParser:
    """
    微信消息解析器

    将从数据库提取的原始消息字典解析为结构化的 ParsedMessage 对象。

    用法示例::

        parser = MessageParser()
        parsed = parser.parse_message(raw_msg_dict)
        print(parsed.msg_type, parsed.content)
    """

    # Type 值到 MessageType 枚举的映射
    _TYPE_MAP: Dict[int, MessageType] = {
        1: MessageType.TEXT,
        3: MessageType.IMAGE,
        34: MessageType.VOICE,
        43: MessageType.VIDEO,
        47: MessageType.EMOJI,
        48: MessageType.LOCATION,
        49: MessageType.APP,
        10000: MessageType.SYSTEM,
        10002: MessageType.SYSTEM_EXT,
    }

    def parse_message(self, raw_msg: dict) -> ParsedMessage:
        """
        解析单条原始消息记录。

        Args:
            raw_msg: 从数据库提取的消息字典，应包含以下字段:
                     MsgSvrID, Type, SubType, IsSender, CreateTime,
                     StrTalker, StrContent, DisplayContent 等。

        Returns:
            解析后的 ParsedMessage 对象。
        """
        type_code = raw_msg.get("Type", -1)
        msg_type = self._TYPE_MAP.get(type_code, MessageType.UNKNOWN)

        is_sender = bool(raw_msg.get("IsSender", 0))
        str_talker = raw_msg.get("StrTalker", "")
        str_content = raw_msg.get("StrContent", "") or ""

        # 对于群聊消息，非本人发送时 StrContent 以 "wxid_xxx:\n" 开头
        sender = ""
        receiver = ""
        content = str_content

        if is_sender:
            sender = "self"
            receiver = str_talker
        else:
            sender = str_talker
            receiver = "self"
            # 群聊消息解析实际发送者
            if "chatroom" in str_talker and ":\n" in str_content:
                parts = str_content.split(":\n", 1)
                sender = parts[0]
                content = parts[1] if len(parts) > 1 else ""

        parsed = ParsedMessage(
            msg_id=raw_msg.get("MsgSvrID", 0),
            sender=sender,
            receiver=receiver,
            timestamp=raw_msg.get("CreateTime", 0),
            msg_type=msg_type,
            content=content,
            is_sender=is_sender,
        )

        # 根据消息类型执行特定解析
        try:
            handler = self._get_type_handler(msg_type)
            if handler:
                handler(raw_msg, parsed)
        except Exception as e:
            logger.warning(
                "消息类型特定解析失败 (msg_id=%s, type=%s): %s",
                parsed.msg_id, msg_type, e,
            )

        return parsed

    def parse_messages(self, raw_messages: List[dict]) -> List[ParsedMessage]:
        """
        批量解析消息列表。

        Args:
            raw_messages: 原始消息字典列表。

        Returns:
            解析后的 ParsedMessage 列表。
        """
        results: List[ParsedMessage] = []
        for raw_msg in raw_messages:
            try:
                results.append(self.parse_message(raw_msg))
            except Exception as e:
                logger.error(
                    "解析消息失败 (MsgSvrID=%s): %s",
                    raw_msg.get("MsgSvrID", "?"), e,
                )
        return results

    # ------------------------------------------------------------------
    # 类型处理器分发
    # ------------------------------------------------------------------

    def _get_type_handler(self, msg_type: MessageType):
        """根据消息类型返回对应的处理方法。"""
        handlers = {
            MessageType.IMAGE: self._parse_image,
            MessageType.VOICE: self._parse_voice,
            MessageType.VIDEO: self._parse_video,
            MessageType.EMOJI: self._parse_emoji,
            MessageType.LOCATION: self._parse_location,
            MessageType.APP: self._parse_app_message,
            MessageType.SYSTEM: self._parse_system,
            MessageType.SYSTEM_EXT: self._parse_system,
        }
        return handlers.get(msg_type)

    # ------------------------------------------------------------------
    # 各类型消息的具体解析
    # ------------------------------------------------------------------

    def _parse_image(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析图片消息，提取媒体路径。"""
        xml_data = self.parse_xml_content(parsed.content)
        if xml_data:
            parsed.extra_data["xml"] = xml_data
        # 图片实际路径通常需要结合 MsgSvrID 和微信本地目录查找
        parsed.content = "[图片]"

    def _parse_voice(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析语音消息，提取语音时长。"""
        xml_data = self.parse_xml_content(parsed.content)
        if xml_data:
            parsed.extra_data["xml"] = xml_data
            # 语音时长存储在 voicemsg 节点的 voicelength 属性中 (单位：毫秒)
            # XML 结构: <msg><voicemsg voicelength="3000" .../></msg>
            voicemsg = xml_data.get("voicemsg", xml_data)
            if isinstance(voicemsg, dict):
                voice_length = voicemsg.get("voicelength", "")
            else:
                voice_length = xml_data.get("voicelength", "")
            if voice_length:
                try:
                    duration_ms = int(voice_length)
                    parsed.extra_data["voice_duration_ms"] = duration_ms
                    parsed.extra_data["voice_duration_sec"] = round(
                        duration_ms / 1000.0, 1
                    )
                except (ValueError, TypeError):
                    pass
        parsed.content = "[语音]"

    def _parse_video(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析视频消息。"""
        xml_data = self.parse_xml_content(parsed.content)
        if xml_data:
            parsed.extra_data["xml"] = xml_data
        parsed.content = "[视频]"

    def _parse_emoji(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析表情/贴纸消息。"""
        xml_data = self.parse_xml_content(parsed.content)
        if xml_data:
            parsed.extra_data["xml"] = xml_data
            emoji_node = xml_data.get("emoji", xml_data)
            if isinstance(emoji_node, dict):
                parsed.extra_data["emoji_md5"] = emoji_node.get("md5", "")
            else:
                parsed.extra_data["emoji_md5"] = xml_data.get("md5", "")
        parsed.content = "[表情]"

    def _parse_location(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析位置消息，提取经纬度和地址。"""
        xml_data = self.parse_xml_content(parsed.content)
        if xml_data:
            parsed.extra_data["xml"] = xml_data
            parsed.extra_data["latitude"] = xml_data.get("x", "")
            parsed.extra_data["longitude"] = xml_data.get("y", "")
            parsed.extra_data["address"] = xml_data.get("label", "")
            parsed.extra_data["poi_name"] = xml_data.get("poiname", "")

            label = xml_data.get("label", "")
            poi = xml_data.get("poiname", "")
            parsed.content = f"[位置] {poi} {label}".strip()
        else:
            parsed.content = "[位置]"

    def _parse_app_message(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """
        解析 Type 49 应用消息。

        SubType 决定具体子类型：
        - 6:    文件
        - 33/36: 小程序
        - 57:   引用/回复
        - 2000: 转账
        - 2001: 红包
        """
        sub_type = raw_msg.get("SubType", 0)
        parsed.extra_data["sub_type"] = sub_type

        xml_data = self.parse_xml_content(parsed.content)
        if not xml_data:
            return

        parsed.extra_data["xml"] = xml_data

        # 应用消息的实际数据通常在 <msg><appmsg>...</appmsg></msg> 结构中
        # 提取 appmsg 节点作为主要数据源，同时保留完整 xml_data
        appmsg = xml_data.get("appmsg", xml_data)
        if not isinstance(appmsg, dict):
            appmsg = xml_data

        if sub_type == AppSubType.FILE:
            self._parse_file_message(appmsg, parsed)
        elif sub_type in (AppSubType.MINI_PROGRAM, AppSubType.MINI_PROGRAM_ALT):
            self._parse_mini_program(appmsg, parsed)
        elif sub_type == AppSubType.REFERENCE:
            self._parse_reference_message(appmsg, parsed)
        elif sub_type == AppSubType.TRANSFER:
            self._parse_transfer_message(appmsg, parsed)
        elif sub_type == AppSubType.RED_PACKET:
            self._parse_red_packet(appmsg, parsed)
        else:
            # 其他应用消息 (链接分享等)
            title = appmsg.get("title", "")
            des = appmsg.get("des", "")
            parsed.content = title or des or "[应用消息]"
            parsed.extra_data["title"] = title
            parsed.extra_data["description"] = des
            parsed.extra_data["url"] = appmsg.get("url", "")

    def _parse_file_message(
        self, xml_data: dict, parsed: ParsedMessage
    ) -> None:
        """解析文件消息，提取文件名和大小。"""
        title = xml_data.get("title", "")
        parsed.extra_data["filename"] = title

        # 文件大小通常在 appattach/totallen 节点
        appattach = xml_data.get("appattach", {})
        if isinstance(appattach, dict):
            totallen = appattach.get("totallen", "0")
            try:
                file_size = int(totallen)
                parsed.extra_data["filesize"] = file_size
                parsed.extra_data["filesize_human"] = self._human_size(
                    file_size
                )
            except (ValueError, TypeError):
                parsed.extra_data["filesize"] = 0
            parsed.extra_data["file_ext"] = appattach.get("fileext", "")

        parsed.content = f"[文件] {title}"

    def _parse_mini_program(
        self, xml_data: dict, parsed: ParsedMessage
    ) -> None:
        """解析小程序消息。"""
        title = xml_data.get("title", "")
        source_name = xml_data.get("sourcedisplayname", "")
        parsed.extra_data["mini_program_title"] = title
        parsed.extra_data["mini_program_source"] = source_name
        parsed.content = f"[小程序] {source_name}: {title}"

    def _parse_reference_message(
        self, xml_data: dict, parsed: ParsedMessage
    ) -> None:
        """解析引用/回复消息，提取被引用内容。"""
        title = xml_data.get("title", "")

        # 被引用消息在 refermsg 节点中
        refer = xml_data.get("refermsg", {})
        if isinstance(refer, dict):
            parsed.extra_data["quoted_content"] = refer.get("content", "")
            parsed.extra_data["quoted_sender"] = refer.get(
                "displayname", ""
            )
            parsed.extra_data["quoted_msg_id"] = refer.get("svrid", "")
            parsed.extra_data["quoted_type"] = refer.get("type", "")
            quoted_display = refer.get("displayname", "")
            quoted_text = refer.get("content", "")
            parsed.content = (
                f"{title}\n"
                f"  > {quoted_display}: {quoted_text}"
            )
        else:
            parsed.content = title or "[引用消息]"

    def _parse_transfer_message(
        self, xml_data: dict, parsed: ParsedMessage
    ) -> None:
        """解析转账消息，提取金额、交易号和备注。"""
        wcpayinfo = xml_data.get("wcpayinfo", {})
        if isinstance(wcpayinfo, dict):
            # 金额以分为单位存储
            fee_str = wcpayinfo.get("feedesc", "")
            parsed.extra_data["amount"] = fee_str

            # 也可能在 pay_memo 中
            parsed.extra_data["pay_memo"] = wcpayinfo.get(
                "pay_memo", ""
            )
            parsed.extra_data["transaction_id"] = wcpayinfo.get(
                "transferid", ""
            )
            parsed.extra_data["receiver_username"] = wcpayinfo.get(
                "receiver_username", ""
            )
            parsed.extra_data["payer_username"] = wcpayinfo.get(
                "payer_username", ""
            )

            parsed.content = f"[转账] {fee_str}"
        else:
            parsed.content = "[转账]"

    def _parse_red_packet(
        self, xml_data: dict, parsed: ParsedMessage
    ) -> None:
        """解析红包消息。"""
        wcpayinfo = xml_data.get("wcpayinfo", {})
        if isinstance(wcpayinfo, dict):
            sender_title = wcpayinfo.get("sendertitle", "恭喜发财")
            parsed.extra_data["red_packet_title"] = sender_title
            parsed.extra_data["inner_type"] = wcpayinfo.get(
                "innertype", ""
            )
            parsed.content = f"[红包] {sender_title}"
        else:
            parsed.content = "[红包]"

    def _parse_system(self, raw_msg: dict, parsed: ParsedMessage) -> None:
        """解析系统消息 (撤回、群变更等)。"""
        content = parsed.content
        # 系统消息通常包含 XML 标签，尝试提取纯文本
        if "<" in content and ">" in content:
            # 移除 XML 标签保留文本
            clean = re.sub(r"<[^>]+>", "", content).strip()
            if clean:
                parsed.extra_data["system_raw"] = content
                parsed.content = clean

    # ------------------------------------------------------------------
    # XML 解析工具
    # ------------------------------------------------------------------

    @staticmethod
    def parse_xml_content(xml_str: str) -> Dict[str, Any]:
        """
        解析消息中的 XML 内容。

        微信多种消息类型（位置、应用消息、语音等）在 StrContent 字段中
        存储 XML 格式的结构化数据。本方法将 XML 递归解析为嵌套字典。

        Args:
            xml_str: XML 格式的字符串内容。

        Returns:
            解析后的字典。解析失败时返回空字典。
        """
        if not xml_str or "<" not in xml_str:
            return {}

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            # 某些消息的 XML 不规范，尝试包裹后解析
            try:
                root = ET.fromstring(f"<root>{xml_str}</root>")
            except ET.ParseError:
                logger.debug("XML 解析失败: %s...", xml_str[:100])
                return {}

        return MessageParser._xml_element_to_dict(root)

    @staticmethod
    def _xml_element_to_dict(element: ET.Element) -> Dict[str, Any]:
        """
        将 XML Element 递归转换为字典。

        处理策略：
        - 叶子节点：使用 text 值
        - 有子节点：递归为嵌套字典
        - 节点属性合并到字典中
        - 同名子节点合并为列表

        Args:
            element: XML Element 节点。

        Returns:
            嵌套字典结构。
        """
        result: Dict[str, Any] = {}

        # 合并节点属性
        if element.attrib:
            result.update(element.attrib)

        # 处理子节点
        children = list(element)
        if not children:
            # 叶子节点：如果有属性则将 text 放入 "_text" 键
            if result:
                if element.text and element.text.strip():
                    result["_text"] = element.text.strip()
            else:
                return element.text.strip() if element.text and element.text.strip() else ""
            return result

        for child in children:
            child_data = MessageParser._xml_element_to_dict(child)
            tag = child.tag

            if tag in result:
                # 同名节点转为列表
                existing = result[tag]
                if not isinstance(existing, list):
                    result[tag] = [existing]
                result[tag].append(child_data)
            else:
                result[tag] = child_data

        # 如果根节点自身也有 text（子节点之间的文本）
        if element.text and element.text.strip():
            result["_text"] = element.text.strip()

        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """将字节数转为人类可读的文件大小字符串。"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
