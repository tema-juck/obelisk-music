from urllib.parse import parse_qs, urlparse

from obelisk_music.auth import authorization_url, make_challenge, make_verifier


def test_pkce_challenge_is_url_safe():
    challenge = make_challenge(make_verifier())
    assert len(challenge) == 43
    assert "=" not in challenge


def test_auth_url_contains_required_parameters():
    url = authorization_url("abc", "http://127.0.0.1:8765/callback", "verifier", "state123")
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["abc"]
    assert qs["state"] == ["state123"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "user-modify-playback-state" in qs["scope"][0]
