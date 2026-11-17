"""Convert VLESS subscription entries into a sing-box client config.

The VPS cannot reach Telegram directly, so RAUNDO sends its Bot API traffic
through a local sing-box sidecar. This module turns one `vless://` URI (as
produced by subscription links used in v2rayTun/NekoBox) into a minimal,
fail-closed sing-box config: a mixed inbound on localhost/Compose network and a
single VLESS outbound as the route default (no direct fallback).

Secrets (UUID, server address, Reality key) live only in the generated config
file on the server; they are never logged here.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

DEFAULT_LISTEN_PORT = 1080
SUPPORTED_SECURITY = {"tls", "reality", "none", ""}


class VlessConfigError(ValueError):
    """Subscription entry cannot be converted safely."""


@dataclass(frozen=True)
class VlessProfile:
    uuid: str
    host: str
    port: int
    remark: str = ""
    security: str = ""
    sni: str = ""
    fingerprint: str = ""
    flow: str = ""
    public_key: str = ""
    short_id: str = ""
    alpn: str = ""


def decode_subscription(body: str) -> list[str]:
    """Return `vless://` lines from a raw or base64 subscription body."""
    text = body.strip()
    if not text:
        return []
    if "://" not in text:
        compact = "".join(text.split())
        padding = "=" * (-len(compact) % 4)
        try:
            text = base64.urlsafe_b64decode(compact + padding).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            return []
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]


def parse_vless_uri(uri: str) -> VlessProfile:
    parsed = urlparse(uri.strip())
    if parsed.scheme != "vless":
        raise VlessConfigError("not a VLESS link")
    if not parsed.username:
        raise VlessConfigError("VLESS link has no user id")
    if not parsed.hostname:
        raise VlessConfigError("VLESS link has no host")
    query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    transport = (query.get("type") or "tcp").lower()
    if transport not in {"tcp", ""}:
        raise VlessConfigError(f"unsupported transport '{transport}' (expected TCP)")
    security = (query.get("security") or "").lower()
    if security not in SUPPORTED_SECURITY:
        raise VlessConfigError(f"unsupported security '{security}'")
    return VlessProfile(
        uuid=unquote(parsed.username),
        host=parsed.hostname,
        port=parsed.port or 443,
        remark=unquote(parsed.fragment or ""),
        security=security,
        sni=query.get("sni") or query.get("host") or parsed.hostname,
        fingerprint=query.get("fp") or "",
        flow=query.get("flow") or "",
        public_key=query.get("pbk") or "",
        short_id=query.get("sid") or "",
        alpn=query.get("alpn") or "",
    )


def pick_profile(entries: list[str], index: int = 0) -> VlessProfile:
    if not entries:
        raise VlessConfigError("subscription has no VLESS entries")
    if index < 0 or index >= len(entries):
        raise VlessConfigError(f"entry index {index} is out of range (total {len(entries)})")
    return parse_vless_uri(entries[index])


def to_sing_box_config(profile: VlessProfile, listen_port: int = DEFAULT_LISTEN_PORT) -> dict:
    tls: dict[str, object] = {"enabled": False}
    if profile.security in {"tls", "reality"}:
        tls["enabled"] = True
        tls["server_name"] = profile.sni or profile.host
        if profile.fingerprint:
            tls["utls"] = {"enabled": True, "fingerprint": profile.fingerprint}
        if profile.alpn:
            tls["alpn"] = [item.strip() for item in profile.alpn.split(",") if item.strip()]
        if profile.security == "reality":
            if not profile.public_key:
                raise VlessConfigError("Reality entry has no public key (pbk)")
            tls["reality"] = {
                "enabled": True,
                "public_key": profile.public_key,
                "short_id": profile.short_id,
            }

    outbound: dict[str, object] = {
        "type": "vless",
        "tag": "proxy",
        "server": profile.host,
        "server_port": profile.port,
        "uuid": profile.uuid,
        "tls": tls,
    }
    if profile.flow:
        outbound["flow"] = profile.flow

    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "raundo-in",
                "listen": "0.0.0.0",
                "listen_port": listen_port,
            }
        ],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        # Fail-closed: default route is the tunnel, never the direct outbound.
        "route": {"final": "proxy"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VLESS subscription -> sing-box config")
    parser.add_argument("--in", dest="source", default="-", help="subscription file or - for stdin")
    parser.add_argument("--out", dest="target", default=None, help="sing-box config path")
    parser.add_argument("--index", type=int, default=0, help="which entry to use (default 0)")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--list", action="store_true", help="only print entry count and exit")
    args = parser.parse_args(argv)
    if not args.list and not args.target:
        parser.error("--out is required unless --list is used")

    raw = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8")
    entries = decode_subscription(raw)
    if args.list:
        print(f"vless_entries={len(entries)}")
        return 0 if entries else 2
    profile = pick_profile(entries, args.index)
    config = to_sing_box_config(profile, listen_port=args.listen_port)
    target = Path(args.target)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)
    # No identifiers, no host, no uuid: only the shape of the selected entry.
    print(
        "vless_profile_written "
        f"transport=tcp security={profile.security or 'none'} "
        f"reality={bool(profile.public_key)} flow={'yes' if profile.flow else 'no'} "
        f"entries={len(entries)} index={args.index}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
