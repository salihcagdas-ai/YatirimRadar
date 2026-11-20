"""Lokatör → geometri çözücü (TKGM tabanlı geocode).

``YerLokator`` (ilçe+mahalle+ada+parsel) + il bağlamı → TKGM idari yapı
(il/ilçe/mahalle id çözümü, **cache**'li) → ``parsel`` → geometri. Aktif parsel
doğrudan; pasif parsel soyağacındaki terminal (aktif) geometriyle temsil edilir.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

import httpx
import structlog

from yatirimradar.geocode.yer import YerLokator
from yatirimradar.ingestion.tkgm import ParselSonuc, TkgmClient

log = structlog.get_logger(__name__)


class TkgmRateLimitError(Exception):
    """TKGM 403/429 sinyali (sorgu limiti / engelleme).

    "Parsel bulunamadı" (404 → ``found=False``) gerçek bir sonuçtur ve akışı durdurmaz;
    ama 403/429 demek TKGM isteği reddetti → çağıran (arka plan işçisi) partiyi durdurup
    ilerlemeyi korumalı. Gözlenen başlıca neden: **günlük sorgu kotası** (gövde
    "Günlük sorgu limitini aştınız.") → IP banı değil, TR gece yarısı sıfırlanır.
    Mesaj (``str(exc)``) TKGM'nin döndürdüğü gerçek metni taşır.
    """


def _rate_limit_mi(exc: BaseException) -> bool:
    """Bu istisna TKGM 403/429 reddi mi?"""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 429)


def _tkgm_hata_mesaji(exc: BaseException) -> str:
    """TKGM 403/429 yanıtından gerçek nedeni çıkar (ör. 'Günlük sorgu limitini aştınız.')."""
    if isinstance(exc, httpx.HTTPStatusError):
        kod = exc.response.status_code
        govde = exc.response.text.strip().strip('"').strip()[:150]
        return f"HTTP {kod} — {govde}" if govde else f"HTTP {kod}"
    return str(exc)


class GeocoderProtokol(Protocol):
    """Lokatör → parsel çözen herhangi bir geocoder (test sahteleri için yapısal arayüz)."""

    def coz(self, il: str | None, lokator: YerLokator) -> ParselSonuc | None: ...


def _as_int(value: object) -> int | None:
    """int/str → int; sayısal olmayan string (beklenmedik API yanıtı) sessizce ``None``."""
    if not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def temsili_geometri(sonuc: ParselSonuc) -> dict[str, object] | None:
    """Parsel sonucundan tek temsili GeoJSON geometri (aktif ya da terminal soyağacı)."""
    if isinstance(sonuc.geometry, dict):
        return sonuc.geometry
    for feat in sonuc.terminal_features:
        g = feat.get("geometry")
        if isinstance(g, dict):
            return g
    return None


class TkgmGeocoder:
    """Serbest lokatörü TKGM parseline çözer; idari id aramalarını cache'ler."""

    def __init__(self, client: TkgmClient | None = None) -> None:
        self._owns = client is None
        self._client = client or TkgmClient()
        self._il_liste: list[dict[str, object]] | None = None
        self._il_cache: dict[str, int] = {}
        self._ilce_liste: dict[int, list[dict[str, object]]] = {}
        self._ilce_cache: dict[tuple[int, str], int] = {}
        self._mahalle_liste: dict[int, list[dict[str, object]]] = {}

    def _il_id(self, il: str) -> int | None:
        key = il.casefold()
        if key in self._il_cache:
            return self._il_cache[key]
        if self._il_liste is None:
            self._il_liste = self._client.il_liste()
        iid = self._client.find_id(self._il_liste, il)
        if iid is not None:
            self._il_cache[key] = iid
        return iid

    def _ilce_id(self, il_id: int, ilce: str) -> int | None:
        key = (il_id, ilce.casefold())
        if key in self._ilce_cache:
            return self._ilce_cache[key]
        liste = self._ilce_liste.get(il_id)
        if liste is None:
            liste = self._client.ilce_liste(il_id)
            self._ilce_liste[il_id] = liste
        cid = self._client.find_id(liste, ilce)
        if cid is not None:
            self._ilce_cache[key] = cid
        return cid

    def _mahalle_kimlikleri(self, ilce_id: int, mahalle: str, limit: int) -> list[int]:
        """O ilçedeki mahalle listesinden ada göre **sıralı aday** id'ler (tam→kısmi→fuzzy)."""
        liste = self._mahalle_liste.get(ilce_id)
        if liste is None:
            liste = self._client.mahalle_liste(ilce_id)
            self._mahalle_liste[ilce_id] = liste
        return self._client.find_id_candidates(liste, mahalle, limit=limit)

    def _ilce_listesi(self, il_id: int) -> list[dict[str, object]]:
        liste = self._ilce_liste.get(il_id)
        if liste is None:
            liste = self._client.ilce_liste(il_id)
            self._ilce_liste[il_id] = liste
        return liste

    def _mahalle_adaylari(self, il_id: int, lokator: YerLokator) -> list[int]:
        """Aday mahalle_id'leri: ilçe varsa o ilçe (fuzzy dâhil); yoksa **tüm ilçeler**.

        İlçe biliniyorsa tam ad tutmasa da yakın adaylar denenir (Köyü/Mahallesi, kısaltma,
        birleşme). İlçe-siz durumda mahalle adı birden çok ilçede olabilir (çakışma) → her
        ilçeden en iyi aday alınır; doğru ilçe ``coz``'da parselin gerçekten çözüldüğü ilçedir.
        """
        assert lokator.mahalle
        if lokator.ilce:
            cid = self._ilce_id(il_id, lokator.ilce)
            if cid is None:
                return []
            return self._mahalle_kimlikleri(cid, lokator.mahalle, limit=4)
        adaylar: list[int] = []
        for it in self._ilce_listesi(il_id):
            cid = _as_int(it.get("id"))
            if cid is None:
                continue
            adaylar.extend(self._mahalle_kimlikleri(cid, lokator.mahalle, limit=1))
        return adaylar

    def coz(self, il: str | None, lokator: YerLokator) -> ParselSonuc | None:
        """Lokatörü çöz; geometriyle **doğrulanmış** parsel sonucunu döndür (yoksa ``None``).

        ``il`` = olayın il bağlamı (ÇED İLİ kolonu); yoksa lokatördeki il kullanılır.
        İlçe yoksa mahalle ilin tüm ilçelerinde aranır; **ada/parselin gerçekten çözüldüğü**
        (geometri veren) ilçe seçilir → mahalle-adı çakışmasında yanlış ilçe elenir.
        """
        if not lokator.geocode_edilebilir:
            return None
        il_adi = il or lokator.il
        assert lokator.mahalle and lokator.ada and lokator.parsel
        if not il_adi:
            return None
        try:  # idari yapı çözümü (il/ilçe/mahalle)
            il_id = self._il_id(il_adi)
            if il_id is None:
                return None
            adaylar = self._mahalle_adaylari(il_id, lokator)
        except Exception as exc:  # noqa: BLE001
            if _rate_limit_mi(exc):  # 403/429 → çağırana bildir (parti dursun, ilerleme korunsun)
                raise TkgmRateLimitError(_tkgm_hata_mesaji(exc)) from exc
            log.warning("administrative_lookup_failed", province=il_adi, error=str(exc))
            return None

        ilk_found: ParselSonuc | None = None
        for mahalle_id in adaylar:
            try:
                sonuc = self._client.parsel(mahalle_id, lokator.ada, lokator.parsel)
            except Exception as exc:  # noqa: BLE001 - tek aday hatası akışı durdurmasın
                if _rate_limit_mi(exc):
                    raise TkgmRateLimitError(_tkgm_hata_mesaji(exc)) from exc
                log.warning("parcel_lookup_failed", neighborhood_id=mahalle_id, error=str(exc))
                continue
            if not sonuc.found:
                continue
            if sonuc.has_geometry:  # ada/parsel bu ilçede gerçekten var → doğru aday
                return sonuc
            if ilk_found is None:
                ilk_found = sonuc
        return ilk_found

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> TkgmGeocoder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
