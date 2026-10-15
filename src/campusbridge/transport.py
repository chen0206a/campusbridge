"""Explicit reverse-proxy boundary; actor identity always comes from database auth."""
import ipaddress
import socket

from fastapi import HTTPException


def trusted_proxy(peer, settings):
    if not settings.trusted_proxy_host:
        return False
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(settings.trusted_proxy_host, None, type=socket.SOCK_STREAM)}
    except OSError:
        raise HTTPException(503, "TRUSTED_PROXY_DNS_UNAVAILABLE") from None
    return peer in addresses


def require_transport(request, settings):
    peer = request.client.host if request.client else None
    allowed = {"127.0.0.1", "::1"} | ({"testclient"} if settings.environment == "test" else set())
    if peer not in allowed and not trusted_proxy(peer, settings):
        raise HTTPException(403, "LOCAL_DEVELOPMENT_ONLY")


def rate_peer(request, settings):
    peer = request.client.host if request.client else None
    if trusted_proxy(peer, settings):
        try:
            # The configured proxy overwrites this header, never appends it.
            return str(ipaddress.ip_address(request.headers.get("X-Real-IP", "")))
        except ValueError:
            raise HTTPException(400, "INVALID_PROXY_CLIENT_IP") from None
    return peer
