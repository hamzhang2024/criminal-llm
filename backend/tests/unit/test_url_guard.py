"""
URL 安全校验（SSRF 防护）单元测试

测试目标：
1. validate_external_url - 拒绝内网/回环/链路本地/特殊网段地址
2. validate_external_url - 允许合法公网 URL
3. allow_loopback 选项 - 本地服务模式放行回环
4. 协议与空值校验

安全属性：任何指向内网/元数据服务的 URL 必须被拒绝。
"""

import socket
from unittest.mock import patch

import pytest

from utils.url_guard import SSRFError, validate_external_url


def _fake_resolve(ips: list[str]):
    """构造一个假的 getaddrinfo 返回值，ip 列表对应解析结果。"""
    infos = []
    for ip in ips:
        # IPv6 地址 getaddrinfo 返回 5 元组，sockaddr 是 (ip, port, flowinfo, scopeid)
        if ":" in ip:
            infos.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
        else:
            infos.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
    return infos


class TestProtocolAndEmpty:
    """协议与空值校验"""

    def test_empty_url_rejected(self):
        with pytest.raises(SSRFError, match="为空"):
            validate_external_url("")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(SSRFError, match="协议"):
            validate_external_url("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(SSRFError, match="协议"):
            validate_external_url("ftp://example.com/file")

    def test_missing_host_rejected(self):
        with pytest.raises(SSRFError, match="主机名"):
            validate_external_url("http:///path")


class TestPublicUrlsAllowed:
    """合法公网 URL 应放行"""

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_public_ipv4_allowed(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["93.184.216.34"])
        # 不抛异常即通过
        validate_external_url("https://example.com")

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_public_ipv6_allowed(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["2606:2800:220:1:248:1893:25c8:1946"])
        validate_external_url("https://example.com")


class TestPrivateNetworksRejected:
    """各内网/特殊网段必须拒绝（SSRF 核心防护）"""

    @pytest.mark.parametrize("ip", [
        "10.0.0.1",        # 私有 A 类
        "10.255.255.255",
        "172.16.0.1",      # 私有 B 类
        "172.31.255.255",
        "192.168.1.1",     # 私有 C 类
        "169.254.169.254", # 云元数据服务（最危险的 SSRF 目标）
        "0.0.0.0",
        "100.64.0.1",      # 运营商级 NAT
    ])
    @patch("utils.url_guard.socket.getaddrinfo")
    def test_private_ipv4_rejected(self, mock_resolve, ip):
        mock_resolve.return_value = _fake_resolve([ip])
        with pytest.raises(SSRFError, match="受限"):
            validate_external_url(f"http://attacker.com")

    @pytest.mark.parametrize("ip", [
        "::1",       # IPv6 回环
        "fc00::1",   # IPv6 唯一本地
        "fe80::1",   # IPv6 链路本地
    ])
    @patch("utils.url_guard.socket.getaddrinfo")
    def test_private_ipv6_rejected(self, mock_resolve, ip):
        mock_resolve.return_value = _fake_resolve([ip])
        with pytest.raises(SSRFError):
            validate_external_url("http://attacker.com")


class TestLoopbackOption:
    """allow_loopback 选项：本地服务模式放行回环"""

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_loopback_rejected_by_default(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["127.0.0.1"])
        with pytest.raises(SSRFError):
            validate_external_url("http://localhost:8080")

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_loopback_allowed_with_flag(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["127.0.0.1"])
        # allow_loopback=True 放行回环（本地 MinerU 等服务）
        validate_external_url("http://localhost:8080", allow_loopback=True)

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_ipv6_loopback_allowed_with_flag(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["::1"])
        validate_external_url("http://localhost:8080", allow_loopback=True)

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_loopback_flag_does_not_allow_other_private(self, mock_resolve):
        """allow_loopback 只放行回环，不放行其他内网"""
        mock_resolve.return_value = _fake_resolve(["10.0.0.1"])
        with pytest.raises(SSRFError):
            validate_external_url("http://internal", allow_loopback=True)


class TestDnsResolution:
    """DNS 解析行为"""

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_dns_failure_rejected(self, mock_resolve):
        mock_resolve.side_effect = socket.gaierror("DNS failed")
        with pytest.raises(SSRFError, match="无法解析"):
            validate_external_url("http://nonexistent.invalid")

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_multiple_resolutions_all_checked(self, mock_resolve):
        """主机解析到多个 IP 时，任一为内网即拒绝"""
        mock_resolve.return_value = _fake_resolve(["93.184.216.34", "10.0.0.1"])
        with pytest.raises(SSRFError):
            validate_external_url("http://example.com")

    @patch("utils.url_guard.socket.getaddrinfo")
    def test_all_public_multiple_resolutions_allowed(self, mock_resolve):
        mock_resolve.return_value = _fake_resolve(["93.184.216.34", "1.1.1.1"])
        validate_external_url("http://example.com")
