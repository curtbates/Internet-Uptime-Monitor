import pytest
from ip_tracker import _is_valid_ipv4


def test_valid_ipv4():
    assert _is_valid_ipv4("192.168.1.1")
    assert _is_valid_ipv4("8.8.8.8")
    assert _is_valid_ipv4("255.255.255.255")
    assert _is_valid_ipv4("0.0.0.0")
    assert _is_valid_ipv4("1.2.3.4")


def test_invalid_ipv4_text():
    assert not _is_valid_ipv4("")
    assert not _is_valid_ipv4("not-an-ip")
    assert not _is_valid_ipv4("abc.def.ghi.jkl")


def test_invalid_ipv4_missing_octets():
    assert not _is_valid_ipv4("192.168.1")
    assert not _is_valid_ipv4("192.168")
    assert not _is_valid_ipv4("192")


def test_invalid_ipv4_html_response():
    # Guards against an HTML error page being stored as an IP
    assert not _is_valid_ipv4("<html><body>Error</body></html>")


def test_invalid_ipv4_with_port():
    assert not _is_valid_ipv4("8.8.8.8:53")


def test_invalid_ipv4_ipv6_address():
    assert not _is_valid_ipv4("2001:db8::1")
