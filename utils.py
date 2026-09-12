def calculate_score(ok_count: int, total: int, response_times_ms: list[float]) -> float:
    """Compute the connectivity score (0–100) from DNS poll results.

    Blends success rate (50 %) with response-time quality (50 %). A 20 ms
    average response time earns a perfect RT score; 500 ms earns zero.
    """
    success_rate = ok_count / total if total else 0.0
    if response_times_ms:
        avg_rt = sum(response_times_ms) / len(response_times_ms)
        rt_score = max(0.0, min(1.0, 1.0 - (avg_rt - 20) / 480))
    else:
        rt_score = 0.0
    return min(100.0, (success_rate * 0.5 + rt_score * 0.5) * 100)
