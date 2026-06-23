"""URL 安全校验工具 - 防 SSRF

校验用户可控的 URL 不指向内网/链路本地/回环地址（本地服务模式除外）。
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 内网/特殊网段，禁止作为外部服务请求目标
PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # 链路本地（含云元数据 169.254.169.254）
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # 运营商级 NAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 唯一本地
    ipaddress.ip_network("fe80::/10"),  # IPv6 链路本地
]


class SSRFError(ValueError):
    """URL 校验失败（疑似 SSRF）"""


def _ip_is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # 无法解析为 IP 时按危险处理
    # is_private 已覆盖大部分，但显式校验元数据网段以防遗漏
    if any(addr in net for net in PRIVATE_NETWORKS):
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


def validate_external_url(url: str, *, allow_loopback: bool = False) -> None:
    """校验 URL 可作为外部服务目标，拒绝内网/回环/链路本地地址。

    Args:
        url: 待校验的完整 URL
        allow_loopback: 是否允许 127.0.0.1/localhost（用于本地部署的服务，如本地 MinerU）

    Raises:
        SSRFError: URL 非法或指向受限地址
    """
    if not url:
        raise SSRFError("URL 为空")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"不允许的协议: {parsed.scheme}")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL 缺少主机名")

    # 解析主机名到 IP，校验所有解析结果
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"无法解析主机名: {host}") from exc

    for info in infos:
        ip = info[4][0]
        if _ip_is_private(ip):
            # 允许回环（本地服务模式）
            if allow_loopback and ip in ("127.0.0.1", "::1"):
                continue
            raise SSRFError(f"URL 指向受限内网地址 {ip}（host={host}）")
