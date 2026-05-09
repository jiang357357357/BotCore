"""
配置管理
读取和管理配置
"""

import json
from src.System.Logs import get_logger
from typing import Any, Dict, Optional
from pathlib import Path

logger = get_logger(__name__)

class Config:
    """配置管理类"""
    
    def __init__(self, config_file: str = "config/napcat.json"):
        self.config_file = Path(config_file)
        self.config_data = {}
        self.default_config = {
            "napcat": {
                "enabled": True,
                "auto_reply": True,
                "response_style": "tsundere"
            },
            "plugins": {
                "text_plugin": {
                    "enabled": True,
                    "keyword_responses": True
                },
                "voice_plugin": {
                    "enabled": False,
                    "tts_enabled": False,
                    "stt_enabled": False
                },
                "command_plugin": {
                    "enabled": True,
                    "commands": ["napcat", "help", "test", "history", "ping", "status"]
                },
                "image_plugin": {
                    "enabled": False,
                    "recognition_enabled": False,
                    "generation_enabled": False
                }
            },
            "storage": {
                "data_dir": "data/napcat",
                "backup_enabled": True,
                "backup_interval": 24  # 小时
            },
            "logging": {
                "level": "INFO",
                "file_enabled": True,
                "console_enabled": True
            }
        }
        
        self.load_config()
    
    def load_config(self) -> bool:
        """
        加载配置文件
        
        Returns:
            加载是否成功
        """
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config_data = json.load(f)
                logger.info(f"配置文件已加载: {self.config_file}")
            else:
                # 使用默认配置
                self.config_data = self.default_config.copy()
                self.save_config()  # 保存默认配置
                logger.info("使用默认配置并已保存")
            
            return True
            
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self.config_data = self.default_config.copy()
            return False
    
    def save_config(self) -> bool:
        """
        保存配置文件
        
        Returns:
            保存是否成功
        """
        try:
            # 确保配置目录存在
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"配置文件已保存: {self.config_file}")
            return True
            
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键，支持点号分隔的嵌套键
            default: 默认值
            
        Returns:
            配置值
        """
        try:
            keys = key.split('.')
            value = self.config_data
            
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            
            return value
            
        except Exception as e:
            logger.error(f"获取配置失败 {key}: {e}")
            return default
    
    def set(self, key: str, value: Any) -> bool:
        """
        设置配置值
        
        Args:
            key: 配置键，支持点号分隔的嵌套键
            value: 配置值
            
        Returns:
            设置是否成功
        """
        try:
            keys = key.split('.')
            config = self.config_data
            
            # 创建嵌套结构
            for k in keys[:-1]:
                if k not in config:
                    config[k] = {}
                config = config[k]
            
            # 设置值
            config[keys[-1]] = value
            
            logger.debug(f"配置已设置: {key} = {value}")
            return True
            
        except Exception as e:
            logger.error(f"设置配置失败 {key}: {e}")
            return False
    
    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """
        获取插件配置
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            插件配置字典
        """
        return self.get(f"plugins.{plugin_name}", {})
    
    def is_plugin_enabled(self, plugin_name: str) -> bool:
        """
        检查插件是否启用
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            插件是否启用
        """
        return self.get(f"plugins.{plugin_name}.enabled", False)
    
    def enable_plugin(self, plugin_name: str, enabled: bool = True) -> bool:
        """
        启用/禁用插件
        
        Args:
            plugin_name: 插件名称
            enabled: 是否启用
            
        Returns:
            设置是否成功
        """
        return self.set(f"plugins.{plugin_name}.enabled", enabled)
    
    def get_all_plugins(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有插件配置
        
        Returns:
            插件配置字典
        """
        return self.get("plugins", {})
    
    def reset_to_default(self) -> bool:
        """
        重置为默认配置
        
        Returns:
            重置是否成功
        """
        try:
            self.config_data = self.default_config.copy()
            return self.save_config()
        except Exception as e:
            logger.error(f"重置配置失败: {e}")
            return False
    
    def validate_config(self) -> bool:
        """
        验证配置有效性
        
        Returns:
            配置是否有效
        """
        try:
            # 检查必要的配置项
            required_keys = ["napcat", "plugins", "storage"]
            for key in required_keys:
                if key not in self.config_data:
                    logger.error(f"缺少必要配置项: {key}")
                    return False
            
            # 检查插件配置
            plugins = self.config_data.get("plugins", {})
            for plugin_name, plugin_config in plugins.items():
                if not isinstance(plugin_config, dict):
                    logger.error(f"插件配置格式错误: {plugin_name}")
                    return False
            
            logger.info("配置验证通过")
            return True
            
        except Exception as e:
            logger.error(f"配置验证失败: {e}")
            return False
