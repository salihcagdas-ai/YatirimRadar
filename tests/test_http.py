"""ingestion/http.py — DNS-pinning transport birim testleri (ağsız)."""

from __future__ import annotations

import ssl
from unittest.mock import Mock

import httpx
import pytest

from yatirimradar.ingestion.http import DnsPinningTransport, PoliteClient


def test_transport_sistem_sertifikalariyla_host_dogrular(monkeypatch: pytest.MonkeyPatch) -> None:
    fabrika = Mock(return_value=httpx.MockTransport(lambda req: httpx.Response(200)))
    monkeypatch.setattr(httpx, "HTTPTransport", fabrika)
    DnsPinningTransport({}).close()
    baglam = fabrika.call_args.kwargs["verify"]
    assert isinstance(baglam, ssl.SSLContext)
    assert baglam.check_hostname and baglam.verify_mode == ssl.CERT_REQUIRED


def test_dns_pinning_rewrites_host_keeps_sni_and_host_header() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["host_header"] = request.headers.get("Host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    transport = DnsPinningTransport(
        {"cbsapi.tkgm.gov.tr": "192.0.2.1"}, inner=httpx.MockTransport(handler)
    )
    with httpx.Client(transport=transport) as client:
        r = client.get("https://cbsapi.tkgm.gov.tr/api/x")

    assert r.status_code == 200
    assert captured["host"] == "192.0.2.1"  # IP'ye bağlandı
    assert captured["host_header"] == "cbsapi.tkgm.gov.tr"  # Host header korundu
    assert captured["sni"] == "cbsapi.tkgm.gov.tr"  # SNI = gerçek host (cert doğrulaması)


def test_dns_pinning_passthrough_for_unlisted_host() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        return httpx.Response(200)

    transport = DnsPinningTransport({}, inner=httpx.MockTransport(handler))
    with httpx.Client(transport=transport) as client:
        client.get("https://other.example/")

    assert captured["host"] == "other.example"  # override yok → dokunulmadı


def test_throttle_jitter_araliginda(monkeypatch: pytest.MonkeyPatch) -> None:
    # Jitter varsayılanı gecikmenin yarısı; bekleme [delay, delay+jitter] aralığında.
    c = PoliteClient(dns_overrides={}, delay_seconds=2.0)
    assert c.jitter_seconds == 1.0
    uyku: list[float] = []
    monkeypatch.setattr("yatirimradar.ingestion.http.time.sleep", lambda s: uyku.append(s))
    monkeypatch.setattr("yatirimradar.ingestion.http.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("yatirimradar.ingestion.http.random.uniform", lambda a, b: b)
    c._last_request_at = 1000.0  # son istek tam şimdi → tam hedef kadar beklemeli
    c._throttle()
    assert uyku == [3.0]  # delay 2.0 + jitter 1.0
    c.close()
