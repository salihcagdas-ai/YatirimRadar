"""Dayanıklı ingestion HTTP istemcisi.

- **Public-DNS override + SNI:** yerel resolver'ın reddettiği host'lar (ör. ``*.tkgm.gov.tr``)
  için 8.8.8.8/1.1.1.1 ile çözüp IP'ye **doğru SNI** ile bağlanır (httpx ``sni_hostname``).
- **Nazik hız** (asgari gecikme) + **tenacity retry** (geçici ağ hataları).
- **Ham yanıt** ``data/raw``'a zaman damgalı kaydedilir (knowledge_time izlenebilirliği).
"""

from __future__ import annotations

import random
import ssl
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import dns.resolver
import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from yatirimradar.config import Settings, get_settings

log = structlog.get_logger(__name__)


def resolve_public_dns(hosts: Iterable[str], servers: list[str]) -> dict[str, str]:
    """Verilen host'ları public DNS sunucularıyla A kaydına çöz (host->ip)."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(servers)
    overrides: dict[str, str] = {}
    for host in hosts:
        try:
            answer = resolver.resolve(host, "A")
            overrides[host] = answer[0].to_text()
        except Exception as exc:  # noqa: BLE001 - tek host başarısızlığı diğerlerini engellemesin
            log.warning("public_dns_resolve_failed", host=host, error=str(exc))
    return overrides


class DnsPinningTransport(httpx.BaseTransport):
    """Override edilen host'larda IP'ye bağlanıp Host header + SNI'yi korur."""

    def __init__(
        self,
        overrides: dict[str, str],
        inner: httpx.BaseTransport | None = None,
        verify: bool = True,
    ) -> None:
        self._overrides = dict(overrides)
        # TLS doğrulama transport'ta yaşar (Client'a özel transport verilince Client
        # verify'ı yok sayılır) → verify buradan geçirilir; DNS pinning'den bağımsız.
        self._inner = inner or httpx.HTTPTransport(
            retries=1, verify=ssl.create_default_context() if verify else False
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ip = self._overrides.get(host)
        if ip and ip != host:
            request.headers["Host"] = host
            request.extensions = {**request.extensions, "sni_hostname": host}
            request.url = request.url.copy_with(host=ip)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def save_raw(
    content: bytes | str, source: str, name: str, settings: Settings | None = None
) -> Path:
    """Ham yanıtı ``data/raw/{source}/{UTC-zaman}_{name}`` yoluna yaz; yolu döndür."""
    settings = settings or get_settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = settings.data_raw_dir / source
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_").replace("\\", "_")
    path = out_dir / f"{stamp}_{safe}"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


class PoliteClient:
    """Hız sınırlı + retry'li + DNS-override'lı httpx istemci sarmalayıcısı."""

    def __init__(
        self,
        settings: Settings | None = None,
        dns_overrides: dict[str, str] | None = None,
        delay_seconds: float | None = None,
        jitter_seconds: float | None = None,
        timeout: float = 30.0,
        verify: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        if dns_overrides is None:
            dns_overrides = resolve_public_dns(
                self.settings.public_dns_hosts, self.settings.public_dns_servers
            )
        self.dns_overrides = dns_overrides
        self.delay_seconds = (
            self.settings.request_delay_seconds if delay_seconds is None else delay_seconds
        )
        # Sabit aralık = bot imzası; rastgele jitter ekle (varsayılan gecikmenin yarısı kadar).
        self.jitter_seconds = (
            0.5 * self.delay_seconds if jitter_seconds is None else max(0.0, jitter_seconds)
        )
        # TLS doğrulamasını özel transport yönetir.
        self._client = httpx.Client(
            timeout=timeout,
            # Tarayıcı-benzeri başlıklar (UA + Accept*): bot parmak izini azaltır → WAF dostu.
            headers={
                "User-Agent": self.settings.http_user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
            },
            transport=DnsPinningTransport(dns_overrides, verify=verify),
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        hedef = self.delay_seconds + random.uniform(0.0, self.jitter_seconds)
        wait = hedef - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def get(self, url: str, **kwargs: object) -> httpx.Response:
        """Nazik (throttle'lı) GET; geçici ağ hatalarında 3 kez dener."""
        self._throttle()
        return self._client.get(url, **kwargs)  # type: ignore[arg-type]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
