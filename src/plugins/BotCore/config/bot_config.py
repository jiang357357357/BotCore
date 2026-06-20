"""
机器人配置类
管理机器人的各种配置项
"""

import os
from typing import List, Dict, Any
from dataclasses import dataclass, field

@dataclass
class BotConfig:
    """机器人配置类"""
    
    # 机器人基本信息
    bot_name: str = ""
    bot_nicknames: List[str] = field(default_factory=list)
    bot_description: str = ""
    
    # 消息处理配置
    command_prefix: str = "/"
    enable_global_commands: bool = True  # 是否启用全局命令（不需要@机器人）
    enable_mention_reply: bool = True    # 是否启用@机器人回复
    enable_name_mention: bool = True     # 是否启用名字提及回复
    
    # 回复配置
    default_reply: str = "哼！本小姐收到了你的消息，但不知道如何处理..."
    error_reply: str = "哼！本小姐处理消息时出了点小问题..."
    private_reply: str = "哼！你私聊本小姐说：{message}\n本小姐收到了！"
    voice_mode_enabled: bool = True
    
    # 关键词回复配置
    keyword_responses: Dict[str, str] = field(default_factory=lambda: {
        "ping": "哼！本小姐在线着呢！Pong！",
        "你好": "你好！本小姐很高兴见到你～",
        "hi": "Hi！本小姐很高兴见到你～",
        "napcat": "哼！你提到本小姐的NapCat了！有什么需要帮助的吗？",
        "帮助": "本小姐的可用命令：\n/napcat - 查看状态\n/help - 帮助\n/test - 测试",
        "状态": "本小姐运行正常！NapCat消息处理功能已启用！",
        "测试": "哼！测试功能正常！本小姐工作得很好！"
    })
    
    # 命令配置
    available_commands: List[str] = field(default_factory=lambda: [
        "帮助", "角色", "语音", "好感", "好感排行", "记忆", "记忆列表"
    ])
    
    # 日志配置
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # 其他配置
    max_message_length: int = 1000
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    split_multiline_reply: bool = True
    split_reply_interval_seconds: float = 0.35
    max_reply_segments: int = 8
    split_reply_sentence_count: int = 2

    # 本地白黑名单/许可开关（在后端 supported_contacts/groups 过滤之前额外生效）
    # 许可开关含义：
    # - *_default_permit=True：默认处理，命中拒绝列表则拒绝
    # - *_default_permit=False：默认不处理，仅认可列表允许处理
    group_default_permit: bool = True
    private_default_permit: bool = True
    group_allow_list: List[str] = field(default_factory=list)    # 群聊认可列表（群号字符串）
    group_deny_list: List[str] = field(default_factory=list)     # 群聊拒绝列表（群号字符串）
    private_allow_list: List[str] = field(default_factory=list)  # 私聊认可列表（QQ号字符串）
    private_deny_list: List[str] = field(default_factory=list)   # 私聊拒绝列表（QQ号字符串）
    
    def __post_init__(self):
        """初始化后处理。Bot 身份由 NapCat/Server 提供，本地只保留运行开关。"""
        self.bot_name = str(self.bot_name or "").strip()
        self.bot_nicknames = [
            str(name).strip()
            for name in (self.bot_nicknames or [])
            if str(name or "").strip()
        ]
    
    def get_all_names(self) -> List[str]:
        """获取运行时机器人名字（身份不再来自本地持久化配置）。"""
        names: List[str] = []
        for name in [self.bot_name] + list(self.bot_nicknames or []):
            text = str(name or "").strip()
            if text and text not in names:
                names.append(text)
        return names
    
    def is_bot_name(self, name: str) -> bool:
        """检查是否是机器人名字"""
        name_lower = name.lower()
        for bot_name in self.get_all_names():
            if bot_name.lower() == name_lower:
                return True
        return False
    
    def contains_bot_name(self, message: str) -> bool:
        """检查消息中是否包含机器人名字"""
        message_lower = message.lower()
        for name in self.get_all_names():
            # 单字昵称极易误触发，例如“唯一”命中“唯”，名字触发至少需要两个字符。
            if len(name.strip()) < 2:
                continue
            if name.lower() in message_lower:
                return True
        return False
    
    def get_keyword_response(self, keyword: str) -> str:
        """获取关键词回复"""
        return self.keyword_responses.get(keyword.lower(), "")
    
    def is_valid_command(self, command: str) -> bool:
        """检查是否是有效命令"""
        return command.lower() in [cmd.lower() for cmd in self.available_commands]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "command_prefix": self.command_prefix,
            "enable_global_commands": self.enable_global_commands,
            "enable_mention_reply": self.enable_mention_reply,
            "enable_name_mention": self.enable_name_mention,
            "default_reply": self.default_reply,
            "error_reply": self.error_reply,
            "private_reply": self.private_reply,
            "voice_mode_enabled": self.voice_mode_enabled,
            "keyword_responses": self.keyword_responses,
            "available_commands": self.available_commands,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "max_message_length": self.max_message_length,
            "rate_limit_enabled": self.rate_limit_enabled,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "split_multiline_reply": self.split_multiline_reply,
            "split_reply_interval_seconds": self.split_reply_interval_seconds,
            "max_reply_segments": self.max_reply_segments,
            "split_reply_sentence_count": self.split_reply_sentence_count,

            "group_default_permit": self.group_default_permit,
            "private_default_permit": self.private_default_permit,
            "group_allow_list": self.group_allow_list,
            "group_deny_list": self.group_deny_list,
            "private_allow_list": self.private_allow_list,
            "private_deny_list": self.private_deny_list,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BotConfig':
        """从字典创建配置"""
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {key: value for key, value in (data or {}).items() if key in valid_keys}
        return cls(**filtered)
    
    @classmethod
    def load_from_file(cls, file_path: str) -> 'BotConfig':
        """从文件加载配置"""
        import json
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return cls.from_dict(data)
        except FileNotFoundError:
            # 如果文件不存在，返回默认配置
            return cls()
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return cls()
    
    def save_to_file(self, file_path: str) -> bool:
        """保存配置到文件"""
        import json
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False

# 创建默认配置实例
default_config = BotConfig()
