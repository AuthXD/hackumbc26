from app import pairing


def test_phone_mode_url_preserves_query_and_replaces_phone_flag():
    assert pairing.phone_mode_url("https://demo.example/path?x=1&phone=0#secret") == (
        "https://demo.example/path?x=1&phone=1"
    )


def test_phone_mode_url_rejects_non_web_urls():
    assert pairing.phone_mode_url("javascript:alert(1)") is None
    assert pairing.phone_mode_url("localhost:5173") is None


def test_configured_https_link_wins_over_lan(monkeypatch):
    monkeypatch.setattr(pairing, "local_ipv4", lambda: "192.168.1.23")
    result = pairing.phone_link("https://camera.example")
    assert result == {
        "url": "https://camera.example/?phone=1",
        "lanUrl": "http://192.168.1.23:5173/?phone=1",
        "secure": True,
        "source": "configured",
    }


def test_lan_fallback_is_explicitly_insecure(monkeypatch):
    monkeypatch.setattr(pairing, "local_ipv4", lambda: "10.0.0.8")
    result = pairing.phone_link("")
    assert result["url"] == "http://10.0.0.8:5173/?phone=1"
    assert result["secure"] is False
    assert result["source"] == "lan"
