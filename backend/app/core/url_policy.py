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
    """Registration guard only; a future HTTP transport MUST validate resolved addresses."""
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
