"""
音频处理工具
用于下载和处理音频文件
"""

import os
import aiohttp
import aiofiles
from src.System.Logs import get_logger
from typing import Optional
from pathlib import Path

logger = get_logger(__name__)

# 临时音频文件目录
AUDIO_TEMP_DIR = Path("data/temp/audio")
AUDIO_TEMP_DIR.mkdir(parents=True, exist_ok=True)


async def download_audio(audio_url: str, timeout: int = 30) -> Optional[str]:
    """
    下载音频文件到本地临时目录
    
    Args:
        audio_url: 音频文件的URL（完整URL）
        timeout: 下载超时时间（秒）
        
    Returns:
        本地文件路径，如果下载失败则返回 None
    """
    try:
        # 从URL提取文件名
        url_path = audio_url.split('?')[0]  # 移除查询参数
        filename = os.path.basename(url_path)
        if not filename:
            # 如果没有文件名，使用时间戳生成
            import time
            filename = f"audio_{int(time.time())}.wav"
        
        # 确保文件名安全
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        
        # 本地文件路径
        local_path = AUDIO_TEMP_DIR / filename
        
        logger.info(f"开始下载音频: {audio_url} -> {local_path}")
        
        # 下载文件
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(audio_url) as response:
                if response.status == 200:
                    # 保存文件
                    async with aiofiles.open(local_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                    
                    # 检查文件大小
                    file_size = local_path.stat().st_size
                    if file_size == 0:
                        logger.warning(f"下载的音频文件为空: {local_path}")
                        local_path.unlink(missing_ok=True)
                        return None
                    
                    logger.info(f"音频下载成功: {local_path} (大小: {file_size} 字节)")
                    return str(local_path)
                else:
                    logger.error(f"下载音频失败: HTTP {response.status}, URL: {audio_url}")
                    return None
                    
    except aiohttp.ClientError as e:
        logger.error(f"下载音频时网络错误: {e}, URL: {audio_url}")
        return None
    except Exception as e:
        logger.error(f"下载音频时出错: {e}, URL: {audio_url}", exc_info=True)
        return None


def cleanup_audio_file(file_path: Optional[str]):
    """
    清理临时音频文件
    
    Args:
        file_path: 音频文件路径
    """
    if file_path:
        try:
            path = Path(file_path)
            if path.exists() and path.is_file():
                path.unlink()
                logger.debug(f"已清理临时音频文件: {file_path}")
        except Exception as e:
            logger.warning(f"清理临时音频文件失败: {e}, 文件: {file_path}")

