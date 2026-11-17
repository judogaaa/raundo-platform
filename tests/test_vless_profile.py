"""VLESS subscription parsing and fail-closed sing-box config generation."""

from __future__ import annotations

import base64
import json

import pytest

from raundo.services.vless_profile import (
    VlessConfigError,
    decode_subscription,
    parse_vless_uri,
    pick_profile,
    to_sing_box_config,
)

UUID = "11111111-2222-3333-4444-555555555555"
TLS_URI = (
    f"vless://{UUID}@lt.example.net:443"
    "?encryption=none&security=tls&sni=lt.example.net&fp=chrome&type=tcp#Lithuania"
)
REALITY_URI = (
    f"vless://{UUID}@lt.example.net:8443"
    "?encryption=none&security=reality&sni=cdn.example.org&fp=chrome"
    "&pbk=PUBLICKEYPUBLICKEYPUBLICKEY&sid=ab12&flow=xtls-rprx-vision&type=tcp#LT-Reality"
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_decode_raw_and_base64_subscription() -> None:
    raw = f"#comment\n{TLS_URI}\n{REALITY_URI}\n"
    assert len(decode_subscription(raw)) == 2
    assert decode_subscription(_b64(raw)) == [TLS_URI, REALITY_URI]
    assert decode_subscription("") == []


def test_parse_tls_profile() -> None:
    profile = parse_vless_uri(TLS_URI)
    assert profile.uuid == UUID
    assert profile.host == "lt.example.net"
    assert profile.port == 443
    assert profile.security == "tls"
    assert profile.sni == "lt.example.net"
    assert profile.remark == "Lithuania"


def test_parse_reality_profile() -> None:
    profile = parse_vless_uri(REALITY_URI)
    assert profile.security == "reality"
    assert profile.public_key == "PUBLICKEYPUBLICKEYPUBLICKEY"
    assert profile.short_id == "ab12"
    assert profile.flow == "xtls-rprx-vision"


def test_unsupported_transport_is_rejected() -> None:
    uri = f"vless://{UUID}@host:443?type=ws&security=tls#ws"
    with pytest.raises(VlessConfigError):
        parse_vless_uri(uri)


def test_reality_without_key_is_rejected() -> None:
    profile = parse_vless_uri(f"vless://{UUID}@host:443?security=reality&type=tcp#no-key")
    with pytest.raises(VlessConfigError):
        to_sing_box_config(profile)


def test_config_is_fail_closed() -> None:
    config = to_sing_box_config(parse_vless_uri(TLS_URI))
    assert config["route"]["final"] == "proxy"
    outbounds = {item["tag"]: item for item in config["outbounds"]}
    assert outbounds["proxy"]["type"] == "vless"
    assert outbounds["proxy"]["tls"]["enabled"] is True
    assert "utls" in outbounds["proxy"]["tls"]
    inbound = config["inbounds"][0]
    assert inbound["type"] == "mixed"
    assert inbound["listen_port"] == 1080
    # Serialised config must stay valid JSON (the server writes it as-is).
    assert json.loads(json.dumps(config))["route"]["final"] == "proxy"


def test_plain_vless_has_tls_disabled() -> None:
    config = to_sing_box_config(parse_vless_uri(f"vless://{UUID}@host:80?type=tcp#plain"))
    assert config["outbounds"][0]["tls"]["enabled"] is False


def test_pick_profile_index_bounds() -> None:
    with pytest.raises(VlessConfigError):
        pick_profile([], 0)
    with pytest.raises(VlessConfigError):
        pick_profile([TLS_URI], 3)
