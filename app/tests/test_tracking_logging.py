import logging


def test_uvicorn_and_httpx_access_logs_never_expose_tracking_tokens(caplog, monkeypatch):
    from app.core.logging import configure_logging

    configure_logging()
    # Alembic's fileConfig disables existing loggers in earlier migration tests.
    for name in ("uvicorn.access", "httpx"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)
        caplog.set_level(logging.INFO, logger=name)
    caplog.clear()
    token = "secret-tracking-link"
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", "/api/v1/public/track/" + token + "?other=private", "1.1", 200
    )
    logging.getLogger("httpx").info("HTTP Request: %s %s", "GET", "http://example.com/api/v1/public/track/" + token)
    assert token not in caplog.text and "other=private" not in caplog.text
    records = [record for record in caplog.records if record.name in {"uvicorn.access", "httpx"}]
    assert {record.name for record in records} == {"uvicorn.access", "httpx"}
    assert all("{token}" in record.getMessage() for record in records)
