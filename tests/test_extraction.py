"""Kamulaştırma parsel çıkarımı testleri (ağsız/DB'siz — sahte geocoder + monkeypatch)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import yatirimradar.extraction.kamulastirma as km
from yatirimradar.geocode.cozucu import TkgmRateLimitError
from yatirimradar.geocode.yer import YerLokator
from yatirimradar.ingestion.tkgm import ParselSonuc

_TRI: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[27.0, 38.0], [27.1, 38.0], [27.1, 38.1], [27.0, 38.0]]],
}


class FakeGeocoder:
    """Her (ada,parsel) için sabit geometrili parsel döndürür; ada=0 köy parselini de çözer."""

    def __init__(self) -> None:
        self.cagrilar: list[tuple[str | None, str | None]] = []

    def coz(self, il: str | None, lok: YerLokator) -> ParselSonuc | None:
        self.cagrilar.append((lok.ada, lok.parsel))
        return ParselSonuc(
            mahalle_id=1, ada=lok.ada or "0", parsel_no=lok.parsel or "",
            found=True, il=il, ilce=lok.ilce, geometry=_TRI,
        )


def test_seed_yukle_sentetik_tek_ve_cok_konum(tmp_path: Path) -> None:
    yol = tmp_path / "sentetik.json"
    veri = {
        "_not": "Kurgusal test kayıtları",
        "801": {"il": "Örnekkent", "ilce": "Kuzey", "mahalle": "Testköy",
                "parseller": [{"parsel": 1}, {"parsel": 2}]},
        "802": {"il": "Örnekkent", "gruplar": [
            {"ilce": "Güney", "mahalle": "Örnek bölge", "ada": "3", "parseller": ["1", "2"]},
            {"ilce": "Doğu", "mahalle": "Deneme bölgesi", "ada": "4", "parseller": ["1"]},
        ]},
    }
    yol.write_text(json.dumps(veri), encoding="utf-8")
    kayitlar = {k.karar_no: k for k in km.seed_yukle(yol)}
    assert set(kayitlar) == {"801", "802"}
    tek = kayitlar["801"].parseller
    assert [(r.ada, r.parsel, r.mahalle) for r in tek] == [
        (None, "1", "Testköy"), (None, "2", "Testköy")
    ]
    cok = kayitlar["802"].parseller
    assert [(r.ilce, r.ada, r.parsel) for r in cok] == [
        ("Güney", "3", "1"), ("Güney", "3", "2"), ("Doğu", "4", "1")
    ]
    assert all(r.il == "Örnekkent" for r in cok)


def test_uygula_geocode_union_ve_eslesme(monkeypatch: pytest.MonkeyPatch) -> None:
    yazilan: list[tuple[int, float | None]] = []

    def _sahte_guncelle(oid: int, geom: object, kesinlik: float | None = None) -> bool:
        yazilan.append((oid, kesinlik))
        return True

    monkeypatch.setattr(km, "kamulastirma_olaylari", lambda kn: [1] if kn == "802" else [])
    monkeypatch.setattr(km, "guncelle_katalizor_geom", _sahte_guncelle)
    kayitlar = [
        km.KararKaydi("802", [
            km.ParselRef("Örnekkent", "Kuzey", "Testköy", None, "3"),
            km.ParselRef("Örnekkent", "Kuzey", "Testköy", None, "5"),
        ]),
        km.KararKaydi("899", [km.ParselRef("X", "Y", "Z", "1", "1")]),  # olay yok
    ]
    fake = FakeGeocoder()
    rapor = km.uygula(kayitlar, fake, kaydet=True)

    assert rapor.parsel_cozulen == 3  # 2 + 1
    assert ("0", "3") in fake.cagrilar  # ada yok → "0" ile sorgulandı
    assert rapor.olay_eslesen == 1  # yalnız 802 eşleşti
    assert yazilan == [(1, km.GEOCODE_KESINLIK)]  # birleştirilmiş geom + kesinlik yazıldı
    assert "899" in rapor.eslenmeyen


class RateLimitGeocoder:
    """İlk parseli çözer, ikincide TKGM WAF (403) fırlatır (regresyon testi için)."""

    def __init__(self) -> None:
        self.cagri = 0

    def coz(self, il: str | None, lok: YerLokator) -> ParselSonuc | None:
        self.cagri += 1
        if self.cagri >= 2:
            raise TkgmRateLimitError("403")
        return ParselSonuc(
            mahalle_id=1, ada=lok.ada or "0", parsel_no=lok.parsel or "",
            found=True, il=il, ilce=lok.ilce, geometry=_TRI,
        )


def test_uygula_rate_limitte_zarif_durur(monkeypatch: pytest.MonkeyPatch) -> None:
    # TKGM 403 → uygula ÇÖKMEZ (eski davranış: coz None yutardı; yeni: yükselir, burada yakalanır).
    yazilan: list[int] = []

    def _sahte_guncelle(oid: int, geom: object, kesinlik: float | None = None) -> bool:
        yazilan.append(oid)
        return True

    monkeypatch.setattr(km, "kamulastirma_olaylari", lambda kn: [1] if kn == "801" else [])
    monkeypatch.setattr(km, "guncelle_katalizor_geom", _sahte_guncelle)
    kayitlar = [
        km.KararKaydi("801", [km.ParselRef("Örnekkent", "Deneme", "Testköy", "8", "7")]),
        km.KararKaydi("803", [km.ParselRef("Örnekkent", "Güney", "Testbölge", "6", "1")]),
    ]
    rapor = km.uygula(kayitlar, RateLimitGeocoder(), kaydet=True)

    assert rapor.rate_limit is True  # zarif durdu (çökmedi)
    assert rapor.parsel_cozulen == 1 and yazilan == [1]  # 1. karar yazıldı, 2.'de durdu
