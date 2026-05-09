"""
数据存储接口
提供数据存储功能
"""

import json
from src.System.Logs import get_logger
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = get_logger(__name__)

class Storage:
    """数据存储类"""
    
    def __init__(self, data_dir: str = "data/napcat"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache = {}  # 内存缓存
    
    def save(self, key: str, data: Any) -> bool:
        """
        保存数据
        
        Args:
            key: 数据键
            data: 数据内容
            
        Returns:
            保存是否成功
        """
        try:
            # 保存到内存缓存
            self.cache[key] = data
            
            # 保存到文件
            file_path = self.data_dir / f"{key}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"数据已保存: {key}")
            return True
            
        except Exception as e:
            logger.error(f"保存数据失败 {key}: {e}")
            return False
    
    def load(self, key: str) -> Optional[Any]:
        """
        加载数据
        
        Args:
            key: 数据键
            
        Returns:
            数据内容，如果不存在则返回None
        """
        try:
            # 先从内存缓存获取
            if key in self.cache:
                return self.cache[key]
            
            # 从文件加载
            file_path = self.data_dir / f"{key}.json"
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.cache[key] = data  # 缓存到内存
                    return data
            
            return None
            
        except Exception as e:
            logger.error(f"加载数据失败 {key}: {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """
        删除数据
        
        Args:
            key: 数据键
            
        Returns:
            删除是否成功
        """
        try:
            # 从内存缓存删除
            if key in self.cache:
                del self.cache[key]
            
            # 从文件删除
            file_path = self.data_dir / f"{key}.json"
            if file_path.exists():
                file_path.unlink()
            
            logger.debug(f"数据已删除: {key}")
            return True
            
        except Exception as e:
            logger.error(f"删除数据失败 {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """
        检查数据是否存在
        
        Args:
            key: 数据键
            
        Returns:
            数据是否存在
        """
        return key in self.cache or (self.data_dir / f"{key}.json").exists()
    
    def list_keys(self) -> List[str]:
        """
        列出所有数据键
        
        Returns:
            数据键列表
        """
        try:
            keys = []
            for file_path in self.data_dir.glob("*.json"):
                key = file_path.stem
                keys.append(key)
            return keys
        except Exception as e:
            logger.error(f"列出数据键失败: {e}")
            return []
    
    def clear_cache(self):
        """清空内存缓存"""
        self.cache.clear()
        logger.info("内存缓存已清空")
    
    def get_cache_size(self) -> int:
        """获取缓存大小"""
        return len(self.cache)
    
    def backup(self, backup_dir: str = "data/napcat/backup") -> bool:
        """
        备份数据
        
        Args:
            backup_dir: 备份目录
            
        Returns:
            备份是否成功
        """
        try:
            import shutil
            from datetime import datetime
            
            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = backup_path / f"backup_{timestamp}.json"
            
            # 将所有数据合并到一个备份文件
            backup_data = {}
            for key in self.list_keys():
                data = self.load(key)
                if data is not None:
                    backup_data[key] = data
            
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"数据已备份到: {backup_file}")
            return True
            
        except Exception as e:
            logger.error(f"备份数据失败: {e}")
            return False
