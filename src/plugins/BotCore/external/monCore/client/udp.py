"""
UDP 发现服务
通过 UDP 广播发现 MonCore 后端服务器
"""

from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
import asyncio
from typing import Optional, Any

from src.System.Logs import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DiscoveredMonCoreServer:
    """UDP 广播发现结果（尽量携带端口信息，向后兼容仅返回 IP 的旧协议）。"""

    ip: str
    ws_port: Optional[int] = None
    http_port: Optional[int] = None


class UDPDiscovery:
    """UDP 发现服务类"""
    
    # 消息类型
    MSG_PING = b"PING"
    
    def __init__(self, broadcast_port: int = 8888, timeout: float = 3.0):
        """
        初始化 UDP 发现服务
        
        Args:
            broadcast_port: UDP 广播端口（默认 8888）
            timeout: 超时时间（秒）
        """
        self.broadcast_port = broadcast_port
        self.timeout = timeout
    
    async def discover_server(self) -> Optional[str]:
        """
        发现服务器 IP 地址（兼容旧接口，仅返回 IP）
        
        Returns:
            服务器 IP 地址，如果发现失败则返回 None
        """
        info = await self.discover_server_info()
        return info.ip if info else None

    def _parse_port(self, value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            port = int(value)
            if 1 <= port <= 65535:
                return port
        except Exception:
            return None
        return None

    def _parse_response(self, data: bytes) -> Optional[DiscoveredMonCoreServer]:
        # 1) 4 字节 IP（网络字节序）
        if len(data) == 4:
            ip_address = socket.inet_ntoa(data)
            return DiscoveredMonCoreServer(ip=ip_address)

        # 2) 尝试 UTF-8 文本
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = ""

        # 2.1) JSON（新协议推荐）：{"ip":"1.2.3.4","ws_port":8000,"http_port":8000}
        if text:
            try:
                import json

                response = json.loads(text)
                if isinstance(response, dict) and "ip" in response:
                    ip_address = str(response.get("ip", "")).strip()
                    socket.inet_aton(ip_address)  # validate

                    ws_port = (
                        self._parse_port(response.get("ws_port"))
                        or self._parse_port(response.get("websocket_port"))
                        or self._parse_port(response.get("wsPort"))
                        or self._parse_port(response.get("port"))
                    )
                    http_port = (
                        self._parse_port(response.get("http_port"))
                        or self._parse_port(response.get("httpPort"))
                        or self._parse_port(response.get("api_port"))
                    )

                    return DiscoveredMonCoreServer(ip=ip_address, ws_port=ws_port, http_port=http_port)
            except Exception:
                pass

        # 2.2) 纯 IP（旧协议）
        if text:
            try:
                socket.inet_aton(text)
                return DiscoveredMonCoreServer(ip=text)
            except ValueError:
                pass

        # 2.3) "ip:port"（兼容一些实现）
        if text and ":" in text:
            ip_part, port_part = text.rsplit(":", 1)
            try:
                socket.inet_aton(ip_part)
                port = self._parse_port(port_part)
                return DiscoveredMonCoreServer(ip=ip_part, ws_port=port)
            except Exception:
                pass

        return None

    async def discover_server_info(self) -> Optional[DiscoveredMonCoreServer]:
        """
        监听并接收后端广播的服务器信息（推荐使用，支持返回端口）。

        约定：MonCore 后端需要周期性向局域网广播（UDP）服务器信息，
        QQBot 作为“接收广播的一方”监听本端口并解析。

        新 UDP 协议建议广播 JSON：
        {"ip":"192.168.1.102","ws_port":8000,"http_port":8000}
        """
        try:
            logger.info(f"开始监听 UDP 广播发现服务器 (端口: {self.broadcast_port})")
            
            # 创建 UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(self.timeout)
            
            try:
                # 监听本地端口，等待后端广播包
                sock.bind(("", self.broadcast_port))
                logger.debug("已绑定 UDP 监听端口，等待后端广播...")
                
                # 等待响应
                try:
                    data, addr = sock.recvfrom(1024)
                    logger.info(f"收到服务器响应: {addr[0]}")

                    info = self._parse_response(data)
                    if not info:
                        logger.warning(f"无法解析服务器响应: {data!r}")
                        return None

                    if info.ws_port or info.http_port:
                        logger.info(
                            f"发现服务器: ip={info.ip}, ws_port={info.ws_port or '-'}, http_port={info.http_port or '-'}"
                        )
                    else:
                        logger.info(f"发现服务器 IP: {info.ip}")

                    return info
                    
                except socket.timeout:
                    logger.warning(f"UDP 发现超时（{self.timeout}秒），未收到服务器响应")
                    return None
                    
            finally:
                sock.close()
                
        except Exception as e:
            logger.error(f"UDP 发现过程中出错: {e}")
            return None
    
    async def ping_server(self, server_ip: str) -> bool:
        """
        向服务器发送 PING 消息（用于测试连接）
        
        Args:
            server_ip: 服务器 IP 地址
            
        Returns:
            是否收到响应
        """
        try:
            logger.debug(f"向服务器发送 PING: {server_ip}")
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout)
            
            try:
                # 发送 PING 消息
                server_addr = (server_ip, self.broadcast_port)
                sock.sendto(self.MSG_PING, server_addr)
                
                # 等待响应
                try:
                    data, addr = sock.recvfrom(1024)
                    logger.debug(f"收到 PING 响应: {addr[0]}")
                    return True
                except socket.timeout:
                    logger.warning("PING 超时")
                    return False
                    
            finally:
                sock.close()
                
        except Exception as e:
            logger.error(f"PING 服务器时出错: {e}")
            return False

