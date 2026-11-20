"""Arka plan geocode — lokatör çıkarma + parti davranışı testleri (saf/DB'siz)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from yatirimradar.geocode import arkaplan
from yatirimradar.geocode.arkaplan import geocode_partisi, lokator_ozetten
from yatirimradar.geocode.cozucu import TkgmRateLimitError
from yatirimradar.geocode.yer import YerLokator
from yatirimradar.ingestion.tkgm import ParselSonuc

_UCGEN: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[32.0, 39.0], [32.1, 39.0], [32.1, 39.1], [32.0, 39.0]]],
}


def _olay(oid: int, ada: int, *, yer: bool = True) -> tuple[int, str, str, str]:
    """Geocode edilebilir (ada/parsel'li) ya da (yer=False) ada/parsel'siz ÇED ozet satırı."""
    son = f"{ada} Ada, 1 Parsel " if yer else ""
    ozet = f"Enerji — GES @ Deneme İlçesi, Örnekova Mahallesi, {son}[OLUMLU]"
    return (oid, "Örnekkent", "Deneme", ozet)


def test_ozetten_lokator_cikar() -> None:
    # ÇED ozet biçimi: "SEKTÖR — PROJE (SAHİBİ) @ PROJE YERİ [SONUÇ]"
    ozet = (
        "Enerji — Örnekova GES (Test Kuruluşu) @ Deneme İlçesi, Örnekova Mahallesi, "
        "23 Ada, 1 Parsel [OLUMLU]"
    )
    lok = lokator_ozetten(ozet)
    assert lok is not None
    assert lok.ilce == "Deneme" and lok.mahalle == "Örnekova"
    assert lok.ada == "23" and lok.parsel == "1"
    assert lok.geocode_edilebilir


def test_ozette_yer_yoksa_kayitli_ilce() -> None:
    # @ kısmı yoksa lokatör çıkmaz (geocode edilemez).
    assert lokator_ozetten("Sanayi — Fabrika (X A.Ş.) [OLUMLU]") is None
    assert lokator_ozetten(None) is None


def test_ozette_ilce_yoksa_kayitli_ilce_kullanilir() -> None:
    # Yer'de ilçe yok ama mahalle+ada+parsel var → kayıtlı ilçe doldurulur.
    ozet = "Enerji — GES @ Testalan Mahallesi, 24 Ada, 1 Parsel [OLUMLU]"
    lok = lokator_ozetten(ozet, ilce_kayit="Deneme")
    assert lok is not None
    assert lok.ilce == "Deneme" and lok.mahalle == "Testalan"
    assert lok.geocode_edilebilir


class _SahteGeocoder:
    """Çağrı sırasına göre 'found' / 'none' / 'ratelimit' davranışı üreten sahte geocoder."""

    def __init__(self, davranis: Callable[[int], str]) -> None:
        self.davranis = davranis
        self.cagri = 0

    def coz(self, il: str | None, lokator: YerLokator) -> ParselSonuc | None:
        self.cagri += 1
        d = self.davranis(self.cagri)
        if d == "ratelimit":
            raise TkgmRateLimitError("403")
        if d == "found":
            return ParselSonuc(
                mahalle_id=200, ada=lokator.ada or "1", parsel_no=lokator.parsel or "1",
                found=True, il="Örnekkent", ilce="Deneme", geometry=_UCGEN,
            )
        return None


def test_parti_bulunamadi_devam_eder(monkeypatch: pytest.MonkeyPatch) -> None:
    # "Bulunamadı" (None) artık erken durdurmaz → tüm adaylar tek geçişte denenir.
    bes = [_olay(i, i) for i in range(1, 6)]
    monkeypatch.setattr(arkaplan, "_bekleyenler", lambda il, haric=None: bes)
    monkeypatch.setattr(arkaplan, "guncelle_katalizor_geom", lambda *a, **k: True)
    r = geocode_partisi(adet=10, geocoder=_SahteGeocoder(lambda c: "none"), denenen_atla=False)
    assert r.geocode_denenen == 5 and r.cozulen == 0 and not r.erken_durdu


def test_parti_rate_limitte_erken_durur(monkeypatch: pytest.MonkeyPatch) -> None:
    # 3. çağrıda WAF/403 → parti durur; ilk 2 çözüm korunur (kaldığı yerden devam edilebilir).
    bes = [_olay(i, i) for i in range(1, 6)]
    monkeypatch.setattr(arkaplan, "_bekleyenler", lambda il, haric=None: bes)
    cozulenler: list[int] = []

    def _kaydet(oid: int, *a: object, **k: object) -> bool:
        cozulenler.append(oid)
        return True

    monkeypatch.setattr(arkaplan, "guncelle_katalizor_geom", _kaydet)
    gc = _SahteGeocoder(lambda c: "ratelimit" if c >= 3 else "found")
    r = geocode_partisi(adet=10, geocoder=gc, denenen_atla=False)
    assert r.erken_durdu and r.cozulen == 2 and cozulenler == [1, 2] and r.geocode_denenen == 2


def test_parti_butce_denemeyle_sinirli_atlanan_yemez(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2 ada/parsel'siz (atlanan, bütçe yemez) + 4 edilebilir; adet=3 → 3 deneme, 3 çözüm.
    rows = [_olay(1, 0, yer=False), _olay(2, 0, yer=False)] + [_olay(i, i) for i in range(3, 7)]
    monkeypatch.setattr(arkaplan, "_bekleyenler", lambda il, haric=None: rows)
    monkeypatch.setattr(arkaplan, "guncelle_katalizor_geom", lambda *a, **k: True)
    r = geocode_partisi(adet=3, geocoder=_SahteGeocoder(lambda c: "found"), denenen_atla=False)
    assert r.atlanan == 2 and r.geocode_denenen == 3 and r.cozulen == 3


def test_parti_cozulemeyeni_kalici_isaretler(monkeypatch: pytest.MonkeyPatch) -> None:
    # Çözülemeyen olay kalıcı kümeye eklenir → sonraki turlar atlar (işçi ilerler).
    monkeypatch.setattr(arkaplan, "_bekleyenler", lambda il, haric=None: [_olay(1, 1), _olay(2, 2)])
    monkeypatch.setattr(arkaplan, "guncelle_katalizor_geom", lambda *a, **k: True)
    kaydedilen: dict[str, set[int]] = {}
    monkeypatch.setattr(arkaplan, "_basarisiz_yukle", lambda: set())
    monkeypatch.setattr(arkaplan, "_basarisiz_kaydet", lambda s: kaydedilen.__setitem__("s", s))
    gc = _SahteGeocoder(lambda c: "found" if c == 1 else "none")  # 1 çözülür, 2 çözülemez
    r = geocode_partisi(adet=10, geocoder=gc, denenen_atla=True)
    assert r.cozulen == 1
    assert kaydedilen["s"] == {2}  # yalnız çözülemeyen id kalıcılaştırıldı
