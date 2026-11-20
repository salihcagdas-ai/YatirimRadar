"""TkgmGeocoder fallback doğrulama testi (ağsız — sahte TKGM istemcisi).

Mahalle adı birden çok ilçede olabilir (çakışma). Geocoder, ada/parselin gerçekten
çözüldüğü (geometri veren) ilçeyi seçmeli; yalnız ada eşleşmesine güvenmemeli.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from yatirimradar.geocode.cozucu import TkgmGeocoder, TkgmRateLimitError
from yatirimradar.geocode.yer import YerLokator
from yatirimradar.ingestion.tkgm import ParselSonuc, aday_kimlikleri

_TRIANGLE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
}


def _norm(s: str) -> str:
    return s.translate(str.maketrans("İıŞşĞğÇçÖöÜü", "iissggccoouu")).strip().lower()


class FakeTkgm:
    """İki ilçede adaş mahalle var. Aranan parsel yalnız Denemedoğu'da bulunur."""

    def __init__(self) -> None:
        self.parsel_cagrilari: list[int] = []

    def il_liste(self) -> list[dict[str, object]]:
        return [{"id": 1, "text": "Örnekkent"}]

    def ilce_liste(self, il_id: int) -> list[dict[str, object]]:
        return [{"id": 1, "text": "Denemebatı"}, {"id": 2, "text": "Denemedoğu"}]

    def mahalle_liste(self, ilce_id: int) -> list[dict[str, object]]:
        mid = 100 if ilce_id == 1 else 200
        return [{"id": mid, "text": "Kurgusalova"}]

    def find_id(self, items: list[dict[str, object]], name: str) -> int | None:
        for it in items:
            if _norm(str(it.get("text", ""))) == _norm(name):
                id_val = it.get("id")
                return int(id_val) if isinstance(id_val, int | str) else None
        return None

    def find_id_candidates(
        self, items: list[dict[str, object]], name: str, limit: int = 4
    ) -> list[int]:
        return aday_kimlikleri(items, name, limit=limit)

    def parsel(self, mahalle_id: int, ada: int | str, parsel: int | str) -> ParselSonuc:
        self.parsel_cagrilari.append(mahalle_id)
        if mahalle_id == 200 and str(ada) == "11" and str(parsel) == "3":
            return ParselSonuc(
                mahalle_id=200, ada="11", parsel_no="3", found=True,
                il="Örnekkent", ilce="Denemedoğu", geometry=_TRIANGLE,
            )
        return ParselSonuc(mahalle_id, str(ada), str(parsel), found=False)


def test_ilcesiz_fallback_parsel_dogrulamasiyla_dogru_ilce() -> None:
    fake = FakeTkgm()
    gc = TkgmGeocoder(fake)  # type: ignore[arg-type]
    lok = YerLokator(mahalle="Kurgusalova", ada="11", parsel="3")  # ilçe YOK
    sonuc = gc.coz("Örnekkent", lok)
    assert sonuc is not None
    assert sonuc.ilce == "Denemedoğu"  # çakışmada parselin çözüldüğü ilçe seçildi
    assert sonuc.has_geometry
    assert 100 in fake.parsel_cagrilari and 200 in fake.parsel_cagrilari  # ikisi de denendi


def test_hicbir_adayda_cozulemezse_none() -> None:
    fake = FakeTkgm()
    gc = TkgmGeocoder(fake)  # type: ignore[arg-type]
    lok = YerLokator(mahalle="Kurgusalova", ada="88", parsel="1")  # hiçbir ilçede yok
    assert gc.coz("Örnekkent", lok) is None


def test_eksik_lokator_geocode_edilemez() -> None:
    fake = FakeTkgm()
    gc = TkgmGeocoder(fake)  # type: ignore[arg-type]
    assert gc.coz("Örnekkent", YerLokator(mahalle="Kurgusalova")) is None  # ada/parsel yok
    assert fake.parsel_cagrilari == []  # boşuna parsel çağrısı yok


def _http_status_error(kod: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://cbsapi.tkgm.gov.tr/")
    resp = httpx.Response(kod, request=req)
    return httpx.HTTPStatusError(f"{kod}", request=req, response=resp)


class WafTkgm(FakeTkgm):
    """parsel sorgusunda 403 (WAF/rate-limit) fırlatan sahte istemci."""

    def parsel(self, mahalle_id: int, ada: int | str, parsel: int | str) -> ParselSonuc:
        raise _http_status_error(403)


def test_rate_limit_yukseltilir() -> None:
    # 403/429 "parsel bulunamadı"dan farklı → TkgmRateLimitError olarak yüzeye çıkar (parti dursun).
    gc = TkgmGeocoder(WafTkgm())  # type: ignore[arg-type]
    lok = YerLokator(mahalle="Kurgusalova", ada="11", parsel="3")
    with pytest.raises(TkgmRateLimitError):
        gc.coz("Örnekkent", lok)


def test_bulunamadi_rate_limit_degil() -> None:
    # 404 → found=False → coz None döner (yükseltmez); akış sıradakine geçebilsin.
    gc = TkgmGeocoder(FakeTkgm())  # type: ignore[arg-type]
    assert gc.coz("Örnekkent", YerLokator(mahalle="Kurgusalova", ada="88", parsel="1")) is None


class FuzzyTkgm:
    """Olaydaki 'Kurgusaltepe Köyü', TKGM kaydında 'Kurgusaltepe Mahallesi' olarak geçer.

    Tam ad tutmaz (substring de değil), ama fuzzy aday üretimi + geometri doğrulaması ile çözülür.
    """

    def __init__(self) -> None:
        self.parsel_cagrilari: list[int] = []

    def il_liste(self) -> list[dict[str, object]]:
        return [{"id": 1, "text": "Örnekkent"}]

    def ilce_liste(self, il_id: int) -> list[dict[str, object]]:
        return [{"id": 7, "text": "Denemeilçe"}]

    def mahalle_liste(self, ilce_id: int) -> list[dict[str, object]]:
        return [{"id": 300, "text": "Kurgusaltepe Mahallesi"}]

    def find_id(self, items: list[dict[str, object]], name: str) -> int | None:
        for it in items:
            if _norm(str(it.get("text", ""))) == _norm(name):
                v = it.get("id")
                return int(v) if isinstance(v, int | str) else None
        return None

    def find_id_candidates(
        self, items: list[dict[str, object]], name: str, limit: int = 4
    ) -> list[int]:
        return aday_kimlikleri(items, name, limit=limit)

    def parsel(self, mahalle_id: int, ada: int | str, parsel: int | str) -> ParselSonuc:
        self.parsel_cagrilari.append(mahalle_id)
        if mahalle_id == 300 and str(ada) == "10" and str(parsel) == "2":
            return ParselSonuc(
                mahalle_id=300, ada="10", parsel_no="2", found=True,
                il="Örnekkent", ilce="Denemeilçe", geometry=_TRIANGLE,
            )
        return ParselSonuc(mahalle_id, str(ada), str(parsel), found=False)


def test_fuzzy_mahalle_koyu_mahallesi_uyusmazligi_cozulur() -> None:
    fake = FuzzyTkgm()
    gc = TkgmGeocoder(fake)  # type: ignore[arg-type]
    lok = YerLokator(ilce="Denemeilçe", mahalle="Kurgusaltepe Köyü", ada="10", parsel="2")
    sonuc = gc.coz("Örnekkent", lok)
    assert sonuc is not None and sonuc.has_geometry
    assert fake.parsel_cagrilari == [300]  # fuzzy aday (Mahallesi) denendi ve çözüldü
