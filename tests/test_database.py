import time
import pytest
import database


def _init():
    database.initialize_db()


def test_insert_and_fetch_dns_result():
    _init()
    ts = time.time()
    database.insert_dns_result(ts, "Google", "google.com", 12.5, True)
    rows = database.get_dns_results(ts - 1)
    assert len(rows) == 1
    row = rows[0]
    assert row["dns_provider"] == "Google"
    assert row["domain"] == "google.com"
    assert abs(row["response_time_ms"] - 12.5) < 0.01
    assert row["success"] == 1


def test_dns_result_failed():
    _init()
    ts = time.time()
    database.insert_dns_result(ts, "Quad9", "example.com", None, False)
    rows = database.get_dns_results(ts - 1)
    assert rows[0]["success"] == 0
    assert rows[0]["response_time_ms"] is None


def test_get_dns_results_since_filter():
    _init()
    old_ts = time.time() - 1000
    new_ts = time.time()
    database.insert_dns_result(old_ts, "G", "a.com", 10.0, True)
    database.insert_dns_result(new_ts, "G", "b.com", 10.0, True)
    rows = database.get_dns_results(new_ts - 1)
    assert len(rows) == 1
    assert rows[0]["domain"] == "b.com"


def test_insert_and_fetch_ip_log():
    _init()
    ts = time.time()
    database.insert_ip_log(ts, "1.2.3.4", "Comcast", "AS7922 Comcast", "::1")
    rows = database.get_ip_log()
    assert len(rows) == 1
    assert rows[0]["public_ip"] == "1.2.3.4"
    assert rows[0]["isp_name"] == "Comcast"
    assert rows[0]["public_ipv6"] == "::1"


def test_get_latest_ip_empty():
    _init()
    assert database.get_latest_ip() is None


def test_get_latest_ip():
    _init()
    ts = time.time()
    database.insert_ip_log(ts - 10, "1.1.1.1", "ISP A", None, None)
    database.insert_ip_log(ts,      "2.2.2.2", "ISP B", None, None)
    latest = database.get_latest_ip()
    assert latest["public_ip"] == "2.2.2.2"


def test_insert_ip_failure():
    _init()
    ts = time.time()
    database.insert_ip_failure(ts)
    rows = database.get_ip_failures()
    assert len(rows) == 1


def test_get_distinct_isps():
    _init()
    ts = time.time()
    database.insert_ip_log(ts,      "1.1.1.1", "Comcast", None, None)
    database.insert_ip_log(ts + 1,  "2.2.2.2", "AT&T",    None, None)
    database.insert_ip_log(ts + 2,  "3.3.3.3", "Comcast", None, None)
    isps = database.get_distinct_isps()
    assert sorted(isps) == ["AT&T", "Comcast"]


def test_purge_old_records():
    _init()
    ancient = time.time() - 20 * 86400   # 20 days ago
    recent  = time.time()
    database.insert_dns_result(ancient, "G", "old.com", 10.0, True)
    database.insert_dns_result(recent,  "G", "new.com", 10.0, True)
    database.purge_old_records(max_age_days=10)
    rows = database.get_dns_results(0)
    assert len(rows) == 1
    assert rows[0]["domain"] == "new.com"
