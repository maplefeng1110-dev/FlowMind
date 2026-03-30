import importlib


def test_web_auth_invalid_session_ttl_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("FLOWMIND_SESSION_TTL_SECONDS", "not-a-number")

    import client.web_auth as web_auth

    reloaded = importlib.reload(web_auth)
    try:
        assert reloaded.SESSION_COOKIE_MAX_AGE == 7 * 24 * 60 * 60
    finally:
        importlib.reload(web_auth)
