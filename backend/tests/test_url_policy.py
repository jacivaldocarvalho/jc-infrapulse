import pytest
from pydantic import HttpUrl, SecretStr

from app.core.config import Settings
from app.core.url_policy import validate_destination


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST.",
        "foo.localhost",
        "db",
        "api.internal",
        "printer.local",
        "api.home",
        "api.lan",
        "api.test",
        "api.invalid",
        "0.0.0.0",
        "127.0.0.1",
        "127.1",
        "2130706433",
        "0x7f000001",
        "0177.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "[::]",
        "[::1]",
        "[fc00::1]",
        "[fe80::1]",
        "[ff02::1]",
        "[2001:db8::1]",
        "[::ffff:127.0.0.1]",
        "[2002:7f00:1::]",
    ],
)
def test_reject_internal_destinations(host: str) -> None:
    settings = Settings(_env_file=None, postgres_password=SecretStr("test-only"))
    with pytest.raises(ValueError):
        validate_destination(HttpUrl(f"http://{host}/"), settings)


@pytest.mark.parametrize(
    "url", ["https://api.example.com", "https://8.8.8.8", "https://[2606:4700::1111]"]
)
def test_public_destinations(url: str) -> None:
    validate_destination(
        HttpUrl(url), Settings(_env_file=None, postgres_password=SecretStr("test-only"))
    )


def test_explicit_internal_allowlist() -> None:
    settings = Settings(
        _env_file=None,
        postgres_password=SecretStr("test-only"),
        service_internal_hosts=["10.0.0.1", "api.internal"],
    )
    validate_destination(HttpUrl("http://10.0.0.1"), settings)
    validate_destination(HttpUrl("http://api.internal"), settings)
    for url in ["http://user:secret@api.internal", "http://user@10.0.0.1", "http://10.0.0.2"]:
        with pytest.raises(ValueError):
            validate_destination(HttpUrl(url), settings)


@pytest.mark.parametrize(
    "host", ["localhost.localdomain", "router.home.arpa", "api.onion", "api.alt"]
)
def test_reserved_domain_suffixes(host: str) -> None:
    with pytest.raises(ValueError):
        validate_destination(
            HttpUrl(f"http://{host}"),
            Settings(_env_file=None, postgres_password=SecretStr("test-only")),
        )
