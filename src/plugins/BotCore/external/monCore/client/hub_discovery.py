"""
MonHub 服务发现
通过 MonHub 查询 MonCore 后端的地址信息（替代 UDP 广播发现）
"""

import json
import uuid
import time
from dataclasses import dataclass
from typing import Optional
from src.System.Logs import get_logger

logger = get_logger(__name__)


@dataclass
class ServerInfo:
    """MonCore 服务器信息"""
    ip: str
    ws_port: int
    http_port: int


def discover_via_hub(hub_address: str, timeout: float = 5.0) -> Optional[ServerInfo]:
    """
    通过 MonHub 查询 MonCore 地址

    Args:
        hub_address: MonHub ZMQ 地址，如 tcp://127.0.0.1:40051
        timeout: 等待超时秒数

    Returns:
        ServerInfo 或 None
    """
    try:
        import zmq
    except ImportError:
        logger.warning("pyzmq 未安装，无法使用 MonHub 服务发现")
        return None

    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt_string(zmq.IDENTITY, f"monbot-discover-{uuid.uuid4().hex[:8]}")

    try:
        socket.connect(hub_address)
        logger.info(f"正在通过 MonHub 查询 MonCore ({hub_address})")

        msg_id = str(uuid.uuid4())
        request = {
            "protocol": "MonHub",
            "version": "2.0.0",
            "msg_id": msg_id,
            "type": "SERVICE_QUERY",
            "source": "MonBot",
            "target": "MonHub",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": {
                "service_name": "MonCore",
                "watch": True,
                "watch_timeout": timeout,
            },
        }
        socket.send_multipart([b"", json.dumps(request).encode("utf-8")])

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                remaining = int((deadline - time.time()) * 1000)
                if remaining <= 0:
                    break
                if socket.poll(remaining):
                    multipart = socket.recv_multipart()
                    if len(multipart) < 2:
                        continue
                    payload_raw = multipart[1].decode("utf-8")
                    response = json.loads(payload_raw)

                    if response.get("correlation_id") != msg_id:
                        continue

                    response_type = response.get("type")
                    if response_type in ("TRANSPORT_ACK", "ROUTING_ACK"):
                        logger.debug(f"忽略 MonHub ACK 响应: {response_type}")
                        continue

                    if response_type not in ("REPLY", "SERVICE_LIST"):
                        logger.debug(f"忽略非服务查询响应: {response_type}")
                        continue

                    resp_payload = response.get("payload", {})
                    if resp_payload.get("pending"):
                        logger.info("MonCore 尚未注册，已等待 Hub 上线通知")
                        continue

                    if not resp_payload.get("success"):
                        logger.warning(f"MonHub 返回失败: {resp_payload}")
                        return None

                    service = resp_payload.get("service", {})
                    server_info = _extract_server_info(service)
                    if server_info:
                        logger.info(f"通过 MonHub 发现 MonCore: {server_info}")
                        return server_info
                    else:
                        logger.warning("MonCore 服务信息中未找到 WebSocket 端点")
                        return None
            except Exception:
                continue

        logger.warning(f"MonHub 查询超时 ({timeout}s)")
        return None

    except Exception as e:
        logger.error(f"MonHub 查询失败: {e}")
        return None
    finally:
        socket.close(linger=0)
        context.term()


def _extract_server_info(service: dict) -> Optional[ServerInfo]:
    """从 ServiceDescriptor 中提取 MonCore 的 WebSocket 和 HTTP 端点"""
    endpoints = service.get("endpoints", [])
    ws_endpoint = None
    http_endpoint = None

    for ep in endpoints:
        protocol = ep.get("protocol", "")
        if protocol in ("websocket", "ws", "wss") and ws_endpoint is None:
            ws_endpoint = ep
        if protocol in ("http", "https") and http_endpoint is None:
            http_endpoint = ep

    if not ws_endpoint:
        logger.warning("MonCore 服务未注册 WebSocket 端点")
        return None

    ip = ws_endpoint.get("host", "127.0.0.1")
    ws_port = ws_endpoint.get("port", 8000)
    http_port = http_endpoint.get("port", ws_port) if http_endpoint else ws_port

    return ServerInfo(ip=ip, ws_port=ws_port, http_port=http_port)
