"""Fırsat skoru saf fonksiyon testleri (ağsız/DB'siz)."""

from __future__ import annotations

from datetime import UTC, datetime

from yatirimradar.scoring.skor import (
    alan_olcek,
    buyukluk,
    kapasite_olcek,
    olcek_belirle,
    skorla,
    tazelik,
    tur_agirlik,
    ucuzluk,
    yogunluk,
)

SIMDI = datetime(2026, 6, 27, tzinfo=UTC)


def test_tur_agirlik() -> None:
    assert tur_agirlik("kamulastirma") == 1.0
    assert tur_agirlik("ced") == 0.7
    assert tur_agirlik("bilinmeyen") == 0.5
    assert tur_agirlik(None) == 0.5


def test_tazelik_ustel_azalim() -> None:
    # Üstel azalım: bugün 1.0, eskidikçe yumuşak söner, çok eski → tabana (0.1) yaklaşır.
    assert tazelik(SIMDI, SIMDI) == 1.0  # bugün → tam taze
    assert tazelik(datetime(2028, 1, 1, tzinfo=UTC), SIMDI) == 1.0  # gelecek tarih → tam taze
    assert tazelik(None, SIMDI) == 0.5  # tarih yok → nötr
    assert round(tazelik(datetime(2022, 6, 27, tzinfo=UTC), SIMDI), 3) == 0.562  # ~4 yıl
    assert round(tazelik(datetime(2016, 1, 1, tzinfo=UTC), SIMDI), 3) == 0.257  # ~10.5 yıl
    # Çok eski (≈31 yıl) — sert kesimde 0 olurdu; üstel + taban sayesinde > 0.1 kalır (sıfırlanmaz).
    cok_eski = tazelik(datetime(1995, 1, 1, tzinfo=UTC), SIMDI)
    assert 0.1 < cok_eski < 0.12
    # Monotonluk: yeni olay her zaman eskiden taze.
    assert tazelik(datetime(2024, 1, 1, tzinfo=UTC), SIMDI) > tazelik(
        datetime(2018, 1, 1, tzinfo=UTC), SIMDI
    )


def test_ucuzluk() -> None:
    assert ucuzluk(150) == 1.0  # REF (300) altı → tam ucuz
    assert ucuzluk(300) == 1.0
    assert ucuzluk(600) == 0.5  # 300/600
    assert round(ucuzluk(3000), 3) == 0.1
    assert ucuzluk(None) == 0.5  # veri yok → nötr


def test_yogunluk() -> None:
    assert yogunluk(1) == 0.65  # taban 0.6 + 0.4×(1/8)
    assert yogunluk(8) == 1.0  # ref ve üstü → tam
    assert yogunluk(20) == 1.0  # capped
    assert yogunluk(0) == 0.6  # taban


def test_kapasite_olcek_parse() -> None:
    # Sentetik girdideki en güçlü sinyal seçilir (güç veya alan).
    assert kapasite_olcek("Test tesisi (50 MWe/50 MWm), örnek alan 18,5 Ha") == 10.0
    # Boşluksuz "21,555MWm" (TR ondalık virgül) da yakalanır → 21.555/5.
    assert round(kapasite_olcek("(21,555MWm / 15MWe; 10,2 Ha)") or 0, 3) == 4.311
    # ton/yıl kapasitesi → 1.500.000/50.000 = 30.
    assert kapasite_olcek("Test tesisi 1.500.000 ton/yıl") == 30.0
    # ton/gün yıllığa çevrilir: 900 × 365 / 50.000 = 6.57 (biyogaz tesisi).
    assert round(kapasite_olcek("Test tesisi 900 ton/gün") or 0, 2) == 6.57
    # Hayvancılık sayısı: 120.000 adet/dönem / 20.000 = 6 ; "baş" da yakalanır.
    assert kapasite_olcek("Test tesisi (120.000 ADET/DÖNEM)") == 6.0
    assert kapasite_olcek("Büyükbaş Hayvancılık 50.000 baş") == 2.5
    # alan m² ve dönüm → m² referansına (100.000) bölünür.
    assert kapasite_olcek("Sanayi tesisi 50.000 m² alan") == 0.5
    assert kapasite_olcek("Tarım projesi 100 dönüm") == 1.0
    # Ölçek sinyali olmayan özet (taş ocağı, sektör adı) → None (nötr büyüklük).
    assert kapasite_olcek("Örnek maden işletmesi (Test Kuruluşu)") is None
    assert kapasite_olcek(None) is None
    assert kapasite_olcek("") is None


def test_alan_olcek() -> None:
    assert alan_olcek(100_000.0) == 1.0  # referans alan (10 ha)
    assert alan_olcek(1_000_000.0) == 10.0
    assert alan_olcek(None) is None
    assert alan_olcek(0) is None


def test_olcek_belirle_kaynak() -> None:
    # ÇED → özet kapasitesinden (200 MW / 5 = 40).
    assert olcek_belirle("GES (200 MWe)", "ced", None) == 40.0
    # Kamulaştırma özeti ölçek taşımaz → parsel alanından (345000/100000).
    enh = olcek_belirle("154 kV Enerji Nakil Hattı", "kamulastirma", 345000.0)
    assert round(enh or 0, 3) == 3.45
    # Kamulaştırma + ne kapasite ne alan → None.
    assert olcek_belirle("154 kV Enerji Nakil Hattı", "kamulastirma", None) is None
    # ÇED, alanı olsa bile (geocode parseli) özette kapasite yoksa alana DÜŞMEZ → None.
    assert olcek_belirle("Bazı taşınmazlar", "ced", 345000.0) is None


def test_buyukluk_kademeleri() -> None:
    assert buyukluk(None) == 1.0  # veri yok → nötr (cezalandırma yok)
    assert buyukluk(1.0) == 0.6  # referans ölçek → taban
    assert buyukluk(0.01) == 0.6  # referans altı → taban (alt sınır)
    assert round(buyukluk(10.0), 3) == 0.8  # 10× → orta (log10(10)/log10(100)=0.5)
    assert buyukluk(100.0) == 1.0  # tavan
    assert buyukluk(1000.0) == 1.0  # tavan üstü → capped


def test_buyukluk_skoru_buyuteni_one_cikarir() -> None:
    # Aynı tür/kesinlik/tazelik/rayiç/yoğunluk; yalnız büyüklük ölçeği farklı.
    vt = datetime(2026, 1, 1, tzinfo=UTC)
    buyuk = skorla("ced", 0.4, vt, 200, SIMDI, buyukluk_olcek=100.0)  # ör. 500 MW GES
    kucuk = skorla("ced", 0.4, vt, 200, SIMDI, buyukluk_olcek=1.0)  # ör. 5 MW küçük tesis
    bilinmeyen = skorla("ced", 0.4, vt, 200, SIMDI, buyukluk_olcek=None)  # ölçek yok → nötr
    assert buyuk.buyukluk == 1.0 and kucuk.buyukluk == 0.6 and bilinmeyen.buyukluk == 1.0
    assert buyuk.skor > kucuk.skor  # büyük proje, küçükten yüksek skor


def test_skorla_kompozisyon_ve_siralama() -> None:
    # Taze kamulaştırma, ucuz ilçe (rayiç 200), yoğun ilçe (10 katalizör) → yüksek skor.
    yuksek = skorla(
        "kamulastirma", 0.75, datetime(2026, 1, 1, tzinfo=UTC), 200, SIMDI,
        ilce_katalizor_sayisi=10,
    )
    # Eski ÇED, pahalı ilçe (rayiç 5200), tek katalizör → düşük skor.
    dusuk = skorla("ced", 0.3, datetime(2020, 1, 1, tzinfo=UTC), 5200, SIMDI)
    assert yuksek.skor > dusuk.skor
    assert 0.0 <= dusuk.skor <= yuksek.skor <= 1.0
    # bileşenler ayrı ayrı erişilebilir (açıklanabilirlik)
    assert yuksek.tur_agirlik == 1.0 and yuksek.ucuzluk == 1.0
    assert yuksek.yogunluk == 1.0  # 10 ≥ ref(8)
    # yoğun ilçe, tek olaydan yüksek skorlu
    tek = skorla("kamulastirma", 0.75, datetime(2026, 1, 1, tzinfo=UTC), 200, SIMDI,
                 ilce_katalizor_sayisi=1)
    assert yuksek.skor > tek.skor
