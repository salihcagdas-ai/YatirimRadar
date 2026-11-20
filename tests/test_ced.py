"""ÇED XLSX satır-parser birim testleri (ağsız/DB'siz, saf tuple).

Başlık satırı gerçek dosyadaki tuhaflıkları taşır: " PROJE SAHİBİ" (baştaki boşluk),
"KARAR     SONUCU" (çoklu boşluk) — _norm bunları sadeleştirip eşler.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from yatirimradar.geocode.cozucu import TkgmRateLimitError
from yatirimradar.geocode.yer import YerLokator
from yatirimradar.ingestion import ced as ced_mod
from yatirimradar.ingestion.ced import CedKarar, _to_date, parse_ced_rows
from yatirimradar.ingestion.tkgm import ParselSonuc

# Arşivdeki sütun düzeni ve boşluklar için sentetik satırlar.
TITLE_ROW = ("T.C. ... ÇED KARARLARI LİSTESİ", None, None, None, None, None, None, None, None)
HEADER_ROW = (
    "KARAR NO", "KARAR TARİHİ", "SEKTÖR", "PROJENİN ADI", " PROJE SAHİBİ",
    "İLİ", "PROJE YERİ", "RAPORU HAZIRLAYAN KURUM/KURULUŞ", "KARAR     SONUCU",
)
ROWS = [
    TITLE_ROW,
    HEADER_ROW,
    (101, datetime(2024, 5, 6), "ENERJİ", "RES Projesi", "Test Kuruluşu",
     "Ankara", "Polatlı", "Örnek Birim", "OLUMLU"),
    (102, datetime(2023, 2, 1), "MADEN", "Taş Ocağı", "Örnek Kuruluş",
     "Muğla", "Yatağan", "Y Üni.", "ÇED GEREKLİ DEĞİLDİR"),
    (103, datetime(2022, 9, 9), "SANAYİ", "Fabrika", "Z A.Ş.",
     "Ankara", None, "W Müh.", "OLUMSUZ"),
    (None, None, None, None, None, None, None, None, None),  # boş/dipnot → atlanır
]


def test_to_date() -> None:
    assert _to_date(datetime(2024, 5, 6, 12, 0)) == date(2024, 5, 6)
    assert _to_date(date(2024, 5, 6)) == date(2024, 5, 6)
    assert _to_date("06.05.2024") == date(2024, 5, 6)
    assert _to_date("6/5/2024") == date(2024, 5, 6)
    assert _to_date("2024-05-06") == date(2024, 5, 6)  # ISO (yyyy-aa-gg) da tanınır
    assert _to_date("06.05.24") is None  # 2 haneli yıl → güvenilmez, atılır
    assert _to_date("") is None
    assert _to_date(None) is None
    assert _to_date("saçma") is None


def test_parse_ced_rows_temel() -> None:
    kararlar = parse_ced_rows(ROWS)
    assert len(kararlar) == 3  # boş satır atlandı

    k = kararlar[0]
    assert k.karar_no == "101"
    assert k.tarih == date(2024, 5, 6)
    assert k.il == "Ankara"
    assert k.proje_yeri == "Polatlı"
    assert k.olumlu is True
    assert k.kesinlik == 0.4  # il + yer
    assert "ENERJİ" in k.ozet and "RES Projesi" in k.ozet
    assert "Test Kuruluşu" in k.ozet


def test_olumlu_ayrimi_ve_kesinlik() -> None:
    kararlar = parse_ced_rows(ROWS)
    by_no = {k.karar_no: k for k in kararlar}
    assert by_no["102"].olumlu is False  # "ÇED GEREKLİ DEĞİLDİR"
    assert by_no["103"].olumlu is False  # "OLUMSUZ"
    # 103: il var, yer yok → 0.3
    assert by_no["103"].proje_yeri is None
    assert by_no["103"].kesinlik == 0.3
    # olumlu kararın aşaması "karar"
    assert by_no["101"].asama == "karar"


def test_kesinlik_il_yok() -> None:
    assert CedKarar(karar_no="1").kesinlik == 0.15
    assert CedKarar(karar_no="1", il="Ankara").kesinlik == 0.3
    assert CedKarar(karar_no="1", il="Ankara", proje_yeri="Polatlı").kesinlik == 0.4


def test_yer_placeholder_ve_il_token() -> None:
    """'-' yer → None (kesinlik düşer); çok-il listesi token'lara ayrılır + eşleşir."""
    rows = [
        HEADER_ROW,
        (None, datetime(2024, 1, 1), "ENH", "Hat", "A.Ş.",
         "ANKARA, BOLU, SAKARYA", "-", "M.", "OLUMLU"),
    ]
    (k,) = parse_ced_rows(rows)
    assert k.proje_yeri is None  # "-" placeholder temizlendi
    assert k.kesinlik == 0.3  # il var, yer yok
    assert k.il_tokenlar == ["ANKARA", "BOLU", "SAKARYA"]
    assert k.il_eslesir("ankara") and k.il_eslesir("bolu")
    assert not k.il_eslesir("izmir")


def test_kayit_anahtari() -> None:
    """KARAR NO varsa onu; yoksa içerikten kararlı hash (aynı içerik → aynı anahtar)."""
    a = CedKarar(karar_no="101")
    assert a.kayit_anahtari == "no=101"
    b1 = CedKarar(tarih=date(2024, 5, 6), il="Ankara", proje_yeri="Polatlı", proje_adi="RES")
    b2 = CedKarar(tarih=date(2024, 5, 6), il="Ankara", proje_yeri="Polatlı", proje_adi="RES")
    assert b1.kayit_anahtari.startswith("h=") and b1.kayit_anahtari == b2.kayit_anahtari
    c = CedKarar(tarih=date(2024, 5, 7), il="Ankara", proje_yeri="Polatlı", proje_adi="RES")
    assert c.kayit_anahtari != b1.kayit_anahtari  # farklı tarih → farklı anahtar


def test_eced_2025_semasi_karar_no_yok_ilce_var() -> None:
    """eced 2025+ şeması: KARAR NO yok, ayrı İLÇESİ kolonu var (başlık il+sonuç ile bulunur)."""
    eced_header = (
        "KARAR TARİHİ", "SEKTÖR", "PROJENİN ADI", "PROJE SAHİBİ",
        "İLİ", "İLÇESİ", "PROJE YERİ", "RAPORU HAZIRLAYAN", "KARAR SONUCU",
    )
    rows = [
        eced_header,
        (datetime(2026, 6, 26), "Hayvancılık", "Örnek Tesis", "Test Kuruluşu",
         "ANKARA ", "POLATLI ", "Kurgusal Mahalle", "Test Birimi", "OLUMLU"),
    ]
    (k,) = parse_ced_rows(rows)
    assert k.karar_no is None
    assert k.il == "ANKARA"
    assert k.ilce == "POLATLI"  # ayrı kolondan (trailing space temizlendi)
    assert k.proje_yeri == "Kurgusal Mahalle"
    assert k.olumlu and k.kayit_anahtari.startswith("h=")  # karar_no yok → hash


def test_kolon_sirasi_bagimsiz() -> None:
    """Kolon sırası değişse de başlıktan eşleme doğru çalışır."""
    # Kolonları ters çevir (başlık + veri birlikte).
    header = tuple(reversed(HEADER_ROW))
    veri = tuple(reversed(ROWS[2]))  # 101 Ankara/Polatlı satırı
    kararlar = parse_ced_rows([header, veri])
    assert len(kararlar) == 1
    k = kararlar[0]
    assert k.karar_no == "101"
    assert k.il == "Ankara"
    assert k.proje_yeri == "Polatlı"
    assert k.sonuc == "OLUMLU"


class _FakeCedClient:
    def indir(self, url: str) -> bytes:
        return b"xlsx"


class _RateLimitGeocoder:
    def coz(self, il: str | None, lok: YerLokator) -> ParselSonuc | None:
        raise TkgmRateLimitError("403")


def test_yukle_geocode_rate_limitte_cokmez(monkeypatch: pytest.MonkeyPatch) -> None:
    """ÇED --geocode sırasında TKGM 403 → çökme yok; geocode kapanır, çekim sürer (regresyon)."""
    kararlar = [
        CedKarar(karar_no="1", tarih=date(2024, 1, 1), il="Ankara",
                 proje_yeri="Örnekova Mahallesi, 23 Ada, 1 Parsel", sonuc="OLUMLU"),
        CedKarar(karar_no="2", tarih=date(2024, 1, 2), il="Ankara",
                 proje_yeri="Testalan Mahallesi, 24 Ada, 1 Parsel", sonuc="OLUMLU"),
    ]
    monkeypatch.setattr(ced_mod, "parse_ced_xlsx", lambda content: kararlar)
    monkeypatch.setattr(ced_mod, "save_raw", lambda *a, **k: "rawpath")
    rapor = ced_mod.yukle(
        "http://x/y.xlsx",
        client=_FakeCedClient(),  # type: ignore[arg-type]
        il_filtre="Ankara",
        geocode=True,
        geocoder=_RateLimitGeocoder(),  # type: ignore[arg-type]
        kaydet=False,
    )
    assert rapor.geocode_durdu is True  # zarif durdu (çökmedi)
    assert rapor.geocoded == 0 and rapor.filtre_sonrasi == 2  # çekim iki olayı da gördü
