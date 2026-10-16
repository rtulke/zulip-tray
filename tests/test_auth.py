import json

import pytest

from zulip_tray import auth

# Mirror of zerver/lib/mobile_auth_otp.py's encryption side, so the test
# proves the client decrypts exactly what the server encrypts.
def _otp_encrypt_api_key(api_key: str, otp: str) -> str:
    hex_key = api_key.encode().hex()
    return "".join(f"{int(x, 16) ^ int(y, 16):x}" for x, y in zip(hex_key, otp))


SERVER_SETTINGS = json.loads("""{
  "authentication_methods": {"password": false, "ldap": false, "openid connect": true},
  "email_auth_enabled": false, "require_email_format_usernames": true,
  "realm_url": "https://chat.example.com",
  "external_authentication_methods": [{"name": "oidc:example", "display_name": "Example IdP", "display_icon": null,
    "login_url": "/accounts/login/social/oidc/example", "signup_url": "/accounts/register/social/oidc/example"}]
}""")


def test_otp_roundtrip_against_server_algorithm():
    otp = auth.generate_otp()
    assert len(otp) == 64 and int(otp, 16) >= 0
    api_key = "abcDEF0123456789abcDEF0123456789"
    assert auth.decrypt_api_key(_otp_encrypt_api_key(api_key, otp), otp) == api_key


def test_decrypt_rejects_wrong_lengths():
    with pytest.raises(ValueError):
        auth.decrypt_api_key("ab", auth.generate_otp())


def test_browser_login_url_and_method():
    method = auth.browser_login_method(SERVER_SETTINGS)
    assert method["display_name"] == "Example IdP"
    url = auth.build_browser_login_url("https://chat.example.com/", method["login_url"], "f" * 64)
    assert url == "https://chat.example.com/accounts/login/social/oidc/example?mobile_flow_otp=" + "f" * 64
    assert auth.password_login_available(SERVER_SETTINGS) is False
    assert auth.password_login_available({"email_auth_enabled": True}) is True
    assert auth.browser_login_method({}) is None


def test_parse_login_uri():
    params = auth.parse_login_uri(
        "zulip://login?otp_encrypted_api_key=00ff&email=alice%40example.com&user_id=12&realm=https%3A%2F%2Fchat.example.com"
    )
    assert params == {"otp_encrypted_api_key": "00ff", "email": "alice@example.com", "user_id": "12",
                      "realm": "https://chat.example.com"}
    assert auth.parse_login_uri("https://chat.example.com/") is None
    assert auth.parse_login_uri("zulip://other?x=1") is None
