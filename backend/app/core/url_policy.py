import ipaddress
import re

from pydantic import HttpUrl

from app.core.config import Settings

_LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home",
    ".lan",
    ".test",
    ".invalid",
    ".arpa",
    ".onion",
    ".alt",
)


def validate_destination(url: HttpUrl, settings: Settings) -> None:
    """Registration guard; execution additionally validates every resolved address."""
    if url.username is not None or url.password is not None or "@" in str(url).split("/")[2]:
        raise ValueError("URL credentials are forbidden")
    host = (url.host or "").strip("[]").lower().rstrip(".")
    if not host or "%" in host:
        raise ValueError("Invalid destination host")
    # Exact host matches only: the exception never bypasses credential validation.
    if host in settings.service_internal_hosts:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (
            "." not in host
            or host == "localhost"
            or host.endswith(_LOCAL_SUFFIXES)
            or re.fullmatch(r"[0-9.]+", host)
            or any(label.startswith("0x") for label in host.split("."))
        ):
            raise ValueError("Local or ambiguous destination hosts are forbidden") from None
    else:
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or isinstance(address, ipaddress.IPv6Address)
            and (address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo)
        ):
            raise ValueError("Destination must be a public unicast address")


def validate_execution_url(url: HttpUrl, settings: Settings) -> str:
    validate_destination(url, settings)
    host = (url.host or "").strip("[]").lower().rstrip(".")
    if url.scheme not in {"http", "https"} or url.port not in settings.monitoring_allowed_ports:
        raise ValueError("Destination scheme or port forbidden")
    if settings.monitoring_allowed_hosts and host not in settings.monitoring_allowed_hosts:
        raise ValueError("Destination host forbidden")
    return host


def validate_resolved_address(host: str, value: str, settings: Settings) -> None:
    address = ipaddress.ip_address(value)
    if (
        "%" in value
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or str(address) in {"168.63.129.16", "100.100.100.200", "fd00:ec2::254"}
        or isinstance(address, ipaddress.IPv6Address)
        and (
            address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo
            or address in ipaddress.ip_network("64:ff9b::/96")
            or address in ipaddress.ip_network("64:ff9b:1::/48")
        )
    ):
        raise ValueError("Forbidden destination address")
    if address.is_global:
        return
    if host in settings.service_internal_hosts and any(
        address in ipaddress.ip_network(network)
        for network in settings.monitoring_internal_networks
    ):
        return
    raise ValueError("Destination address is not authorized")
