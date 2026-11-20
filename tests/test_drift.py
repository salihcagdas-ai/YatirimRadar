"""Şema/encoding drift korumaları — belgesiz kaynaklar (TKGM/ÇED/R.Gazete) değişirse yakala.

Bu testler 'kaynak şeması beklediğimiz gibi' varsayımını sabitler; kaynak kayarsa kırılır.
"""

from __future__ import annotations

from datetime import date

from yatirimradar.ingestion.ced import parse_ced_rows
from yatirimradar.ingestion.resmigazete import parse_baslik, parse_fihrist
from yatirimradar.ingestion.tkgm import parse_parsel_feature


def test_ced_bilinmeyen_kolon_ve_kasa_bozmaz() -> None:
    # Küçük/karışık kasa + fazladan bilinmeyen kolon → İLİ+SONUÇ ile yine ayrışır.
    rows = [
        ("karar no", "Karar Tarihi", "SEKTÖR", "Projenin Adı", "İLİ",
         "PROJE YERİ", "Karar Sonucu", "YENİ BİLİNMEYEN KOLON"),
        ("5", "2024-01-01", "Enerji", "RES", "Kurgukent", "Deneme", "OLUMLU", "çöp"),
    ]
    (k,) = parse_ced_rows(rows)
    assert k.il == "Kurgukent" and k.olumlu and k.proje_yeri == "Deneme"


def test_ced_iki_sema_da_taninir() -> None:
    # Arşiv (KARAR NO'lu) ve eced 2025+ (KARAR NO'suz, İLÇESİ'li) başlıkları.
    arsiv = [("KARAR NO", "KARAR TARİHİ", "İLİ", "PROJE YERİ", "KARAR SONUCU"),
             ("1", "2024-01-01", "Kurgukent", "Deneme", "OLUMLU")]
    eced = [("KARAR TARİHİ", "İLİ", "İLÇESİ", "PROJE YERİ", "KARAR SONUCU"),
            ("2024-01-01", "Kurgukent", "Deneme", "Merkez Mah", "OLUMLU")]
    assert parse_ced_rows(arsiv)[0].il == "Kurgukent"
    assert parse_ced_rows(eced)[0].ilce == "Deneme"


def test_rgazete_il_ilinde_varyanti() -> None:
    # "X İlinde" (İli değil) varyantı da il vermeli.
    il, _, karar = parse_baslik(
        "Bazı Taşınmazların Örnekkent İlinde ... Acele Kamulaştırılması Hakkında "
        "Karar (Karar Sayısı: 801)"
    )
    assert il == "Örnekkent" and karar == "801"


def test_rgazete_fihrist_pdf_disi_link_atlanir() -> None:
    # Acele kamulaştırma metni olsa da .pdf olmayan link katalizör sayılmaz (drift guard).
    html = (
        b'<a href="x.htm">Acele Kamulastirilmasi Hakkinda Karar</a>'
        b'<a href="test-9.pdf">Deneme Acele Kamulastirilmasi Hakkinda Karar '
        b'(Karar Sayisi: 801)</a>'
    )
    kalemler = parse_fihrist(html, "https://arsiv.example/x.htm", date(2030, 5, 6))
    assert len(kalemler) == 1 and kalemler[0].pdf_url.endswith(".pdf")


def test_tkgm_feature_beklenen_alanlar() -> None:
    # TKGM Feature şeması: durum/adaNo/parselNo/alan/geometry beklenir (drift sabitleme).
    feat = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[32.0, 39.0], [32.1, 39.0],
                     [32.1, 39.1], [32.0, 39.0]]]},
        "properties": {"durum": "1", "adaNo": "4", "parselNo": "2", "alan": "1.000,00",
                       "ilAd": "Kurgukent", "ilceAd": "Deneme", "mahalleAd": "Örnekova"},
    }
    s = parse_parsel_feature(feat, 100, 4, 2)
    assert s.found and s.durum == "1" and s.alan_m2 == 1000.0 and s.has_geometry
