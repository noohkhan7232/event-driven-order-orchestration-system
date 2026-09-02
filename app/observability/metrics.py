from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "path"])


def metrics_response() -> bytes:
    return generate_latest()
