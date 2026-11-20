"""Serbest yer metni ayrıştırıcı testleri (ağsız) — sentetik PROJE YERİ varyantları."""

from __future__ import annotations

from yatirimradar.geocode.yer import parse_yer


def test_tam_lokator() -> None:
    y = parse_yer("Örnekkent İli, Deneme İlçesi, Örnekova Mahallesi, 12 Ada, 1 Parsel")
    assert y.il == "Örnekkent"
    assert y.ilce == "Deneme"
    assert y.mahalle == "Örnekova"  # orijinal kasa korunur (fold konum-hizalı)
    assert y.ada == "12"
    assert y.parsel == "1"
    assert y.geocode_edilebilir


def test_mevkii_arasinda_ada_parsel() -> None:
    y = parse_yer("DENEME İLÇESİ, İKİNCİ MAHALLESİ, TEST MEVKİİ 13 ADA, 31 PARSEL")
    assert y.ilce == "DENEME"
    assert y.mahalle == "İKİNCİ"
    assert y.ada == "13"
    assert y.parsel == "31"
    assert y.geocode_edilebilir


def test_coklu_parsel_ilk_alinir() -> None:
    y = parse_yer("ÖRNEK İLÇESİ, ÜÇÜNCÜ MAHALLESİ (14 ADA, 2-5-8 PARSEL)")
    assert y.ilce == "ÖRNEK"
    assert y.mahalle == "ÜÇÜNCÜ"
    assert y.ada == "14"
    assert y.parsel == "2"  # çoklu parselde ilk temsilci


def test_nolu_parsel() -> None:
    y = parse_yer("GÜNEY İLÇESİ, DÖRDÜNCÜ MAHALLESİ (15 ADA 5 NOLU PARSEL)")
    assert y.ilce == "GÜNEY"
    assert y.mahalle == "DÖRDÜNCÜ"
    assert y.ada == "15"
    assert y.parsel == "5"


def test_coklu_ilce_mahalle_ilk() -> None:
    y = parse_yer("BATI VE DOĞU İLÇELERİ, BEŞİNCİ MAHALLESİ VE ALTINCI MAHALLESİ")
    assert y.ilce == "BATI"  # çok-ilçede ilk
    assert y.mahalle == "BEŞİNCİ"  # ilk mahalle
    assert y.ada is None and y.parsel is None
    assert not y.geocode_edilebilir


def test_cok_kelimeli_ilce() -> None:
    y = parse_yer("ÖRNEK KUZEY İLÇESİ, 16 ADA, 2 VE 3 PARSEL TOPLAM 1.000 M2")
    assert y.ilce == "ÖRNEK KUZEY"  # çok-kelimeli ilçe adı korunur
    assert y.mahalle is None
    assert y.ada == "16"
    assert not y.geocode_edilebilir  # mahalle yok


def test_ilcesi_oneki_virgulsuz_temizlenir() -> None:
    # Virgülsüz "X İLÇESİ Y MAHALLESİ" → mahalle Y (İlçesi öneki atılır; TKGM eşleşsin).
    y = parse_yer("KUZEY İLÇESİ YEDİNCİ MAHALLESİ 0 ADA 17 NOLU PARSEL")
    assert y.ilce == "KUZEY"
    assert y.mahalle == "YEDİNCİ"  # "KUZEY İLÇESİ YEDİNCİ" değil
    assert y.parsel == "17"
    y2 = parse_yer("GÜNEY İLÇESİ SEKİZİNCİ MAHALLESİ, 18 ADA, 51 PARSEL")
    assert y2.ilce == "GÜNEY" and y2.mahalle == "SEKİZİNCİ"
    assert y2.ada == "18" and y2.parsel == "51" and y2.geocode_edilebilir


def test_virgullu_ilce_mahalle_regresyon_yok() -> None:
    # Virgül zaten durdurduğu için temizleme bunu bozmamalı.
    y = parse_yer("Örnekkent İli, Deneme İlçesi, Örnekova Mahallesi, 12 Ada, 1 Parsel")
    assert y.ilce == "Deneme" and y.mahalle == "Örnekova"


def test_yalniz_ilce() -> None:
    y = parse_yer("DENEME İLÇESİ")
    assert y.ilce == "DENEME"
    assert y.mahalle is None and y.ada is None and y.parsel is None
    assert not y.geocode_edilebilir


def test_bos() -> None:
    assert parse_yer(None) == parse_yer("")
    assert not parse_yer("-").geocode_edilebilir
