"""Rayiç, ilân ve mahalle bağlamının zaman uyumlu skor seçimi."""

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert
from sqlalchemy.orm import Session

from yatirimradar.scoring import sirala
from yatirimradar.store.models import Fiyat


def test_fiyat_secimi_as_of(fiyat_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def ekle(
        adres: str, fiyat: float | None, *, yil: int, ogrenim: int, mahalle: str = "ÖRNEK MAH.",
        tip: str = "rayic", kaynak: str = "sentetik-test",
    ) -> None:
        fiyat_session.execute(insert(Fiyat).values(
            tip=tip, il="Deneme", ilce="Testli", mahalle=mahalle, cadde_sokak=adres,
            birim_fiyat_tl_m2=fiyat, kaynak=kaynak,
            valid_time=datetime(yil, 1, 1), knowledge_time=datetime(ogrenim, 1, 1),
        ))

    ekle("A", 70, yil=2023, ogrenim=2023)
    ekle("A", 100, yil=2024, ogrenim=2024)
    ekle("A", 140, yil=2024, ogrenim=2025)
    ekle("A", 400, yil=2024, ogrenim=2026, kaynak="placeholder")
    ekle("B", 160, yil=2024, ogrenim=2024)
    ekle("Sıfır", 0, yil=2024, ogrenim=2024)
    ekle("İptal", 20, yil=2024, ogrenim=2024)
    ekle("İptal", None, yil=2024, ogrenim=2025)
    ekle("Gelecek", 800, yil=2028, ogrenim=2024)
    ekle("C", 300, yil=2024, ogrenim=2024, mahalle="Kuzey Mah.")
    ekle("D", 5000, yil=2026, ogrenim=2026, mahalle="Güney", tip="ilan")

    monkeypatch.setattr(sirala, "session_scope", lambda: nullcontext(fiyat_session))
    harita = sirala._fiyat_haritasi("Deneme", datetime(2026, 6, 1, tzinfo=UTC))
    bolge = sirala._anahtar("DENEME", "Testli")
    ilce = sirala.BolgeFiyati(200, "rayic", "ilce", 2024)
    mahalle = sirala.BolgeFiyati(150, "rayic", "mahalle", 2024)
    ozet = "Enerji @ TESTLİ İlçesi, Örnek Mah."
    assert sirala._olay_fiyati(harita, bolge, "ced", ozet) == mahalle
    assert sirala._olay_fiyati(harita, bolge, "ced", "Proje @ Örnek ve Kuzey Mahalleleri") == ilce
    assert sirala._olay_fiyati(harita, bolge, "ced", "Proje @ Güney Mahallesi") == ilce
    assert sirala._olay_fiyati(harita, bolge, "ced", "Örnek Mahallesi adına GES") == ilce
    assert sirala._olay_fiyati(harita, ("deneme", "baska"), "ced", None) is None
    erken = sirala._fiyat_haritasi(None, datetime(2024, 6, 1, tzinfo=UTC))
    assert erken[(*bolge, "ornek")].birim_fiyat == pytest.approx((100 + 160 + 20) / 3)

    fiyat_session.execute(insert(Fiyat).values(
        tip="ilan", il="Deneme", ilce="Yeni", mahalle="Örnek", birim_fiyat_tl_m2=900,
        kaynak="sentetik-test", knowledge_time=datetime(2024, 7, 1),
    ))
    sadece_ilan = sirala._fiyat_haritasi(None, datetime(2024, 8, 1, tzinfo=UTC))
    assert sadece_ilan["deneme", "yeni", ""].tip == "ilan"
