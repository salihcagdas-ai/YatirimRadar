"""TKGM parse + soyağacı + geometri dönüşümü birim testleri (ağsız/DB'siz)."""

from __future__ import annotations

import json

from yatirimradar.ingestion.tkgm import aday_kimlikleri, parse_parsel_feature, parse_tr_number
from yatirimradar.store.repository import geojson_to_multipolygon, parse_lineage_event

_TRIANGLE: dict[str, object] = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
}

ACTIVE_FEATURE: dict[str, object] = {
    "type": "Feature",
    "geometry": _TRIANGLE,
    "properties": {
        "ilAd": "Örnekkent",
        "ilceAd": "Kuzey",
        "mahalleAd": "İlk Test Mahallesi",
        "adaNo": "4",
        "parselNo": "2",
        "durum": "1",
        "nitelik": "Arsa",
        "alan": "12.345,00",
        "mahalleId": 100,
    },
}

_ARDIL_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": _TRIANGLE,
            "properties": {
                "ilAd": "Örnekkent",
                "ilceAd": "Güney",
                "mahalleAd": "Test/Yeni",
                "mahalleId": 200,
                "adaNo": "12",
                "parselNo": "5",
                "durum": "1",
            },
        }
    ],
}

PASSIVE_FEATURE: dict[str, object] = {
    "type": "Feature",
    "geometry": None,
    "properties": {
        "ilAd": "Örnekkent",
        "ilceAd": "Kuzey",
        "mahalleAd": "İlk Test Mahallesi",
        "adaNo": "4",
        "parselNo": "2",
        "durum": "0",
        "gittigiParselSebep": "01/02/2030 Tarihli İfraz İşlemi (Tsm)",
        "gittigiParselListe": json.dumps(_ARDIL_FC),
        "mahalleId": 100,
    },
}


def test_parse_tr_number() -> None:
    assert parse_tr_number("12.345,00") == 12345.0
    assert parse_tr_number("6.610,00") == 6610.0
    assert parse_tr_number("") is None
    assert parse_tr_number(None) is None


def test_parse_tr_number_heuristik() -> None:
    # Yalnız virgül → ondalık; yalnız nokta → 3-hane grubu binlik, aksi ondalık.
    assert parse_tr_number("300,5") == 300.5
    assert parse_tr_number("300.5") == 300.5  # düz ondalık artık 3005 DEĞİL
    assert parse_tr_number("1.250") == 1250.0  # tek nokta + 3 hane → binlik
    assert parse_tr_number("1.500.000") == 1_500_000.0
    assert parse_tr_number("1.2345") == 1.2345
    assert parse_tr_number(41.877) == 41.877  # sayı tipi olduğu gibi
    assert parse_tr_number("abc") is None


def test_parse_active_feature() -> None:
    s = parse_parsel_feature(ACTIVE_FEATURE, 100, 4, 2)
    assert s.found and s.durum == "1"
    assert s.nitelik == "Arsa"
    assert s.alan_m2 == 12345.0
    assert s.geometry is not None
    assert s.has_geometry
    assert s.ardillar == []


def test_parse_passive_feature_with_lineage() -> None:
    s = parse_parsel_feature(PASSIVE_FEATURE, 100, 4, 2)
    assert s.geometry is None
    assert len(s.ardillar) == 1
    assert s.ardillar[0].ada == "12"
    assert s.ardillar[0].geometry is not None
    assert s.has_geometry  # soyağacı üzerinden geometri elde edilebilir
    assert s.lineage_sebep is not None


_DEEP_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": None,  # ara ardıl (8/3) pasif, geom yok
            "properties": {
                "adaNo": "8",
                "parselNo": "3",
                "durum": "0",
                "gittigiParselListe": json.dumps(_ARDIL_FC),  # derinde aktif geom
            },
        }
    ],
}

DEEP_PASSIVE_FEATURE: dict[str, object] = {
    "type": "Feature",
    "geometry": None,
    "properties": {
        "adaNo": "4",
        "parselNo": "2",
        "durum": "0",
        "gittigiParselSebep": "01/02/2030 Tarihli İfraz İşlemi",
        "gittigiParselListe": json.dumps(_DEEP_FC),
    },
}


def test_recursive_lineage_finds_deep_geometry() -> None:
    s = parse_parsel_feature(DEEP_PASSIVE_FEATURE, 100, 4, 2)
    assert s.geometry is None
    assert all(a.geometry is None for a in s.ardillar)  # doğrudan ardılda geom yok
    assert len(s.terminal_features) == 1  # özyineleme iki seviye derindeki aktif feature'ı buldu
    assert s.terminal_features[0]["geometry"] is not None
    assert s.has_geometry


def test_parse_lineage_event() -> None:
    olay, valid = parse_lineage_event("01/02/2030 Tarihli İfraz İşlemi (Tsm)")
    assert olay == "ifraz"
    assert valid is not None
    assert (valid.year, valid.month, valid.day) == (2030, 2, 1)

    olay2, _ = parse_lineage_event("02/03/2031 Tarihli Taşınmaz Nakli İşlemi")
    assert olay2 == "nakil"


def test_geojson_to_multipolygon() -> None:
    assert geojson_to_multipolygon(None) is None
    g = geojson_to_multipolygon(_TRIANGLE)
    assert g is not None  # Polygon → MultiPolygon sarmalandı


def test_aday_kimlikleri_tam_once_kismi_da_aday() -> None:
    items = [
        {"id": 1, "text": "Örnekova"},       # tam eşleşme
        {"id": 2, "text": "Örnekova Köyü"},  # kısmi (substring)
        {"id": 3, "text": "Başka Yer"},       # alakasız → elenmeli
    ]
    adaylar = aday_kimlikleri(items, "Örnekova", limit=4)
    assert adaylar[0] == 1  # tam eşleşme en önde
    assert 2 in adaylar  # kısmi de aday
    assert 3 not in adaylar


def test_aday_kimlikleri_koyu_mahallesi_fuzzy() -> None:
    # "X Köyü" ↔ "X Mahallesi": substring değil ama idari son ek sadeleşince aynı → fuzzy eşleşir.
    items: list[dict[str, object]] = [{"id": 9, "text": "Kurgusaltepe Mahallesi"}]
    assert aday_kimlikleri(items, "Kurgusaltepe Köyü") == [9]


def test_aday_kimlikleri_alakasiz_ve_bos() -> None:
    items: list[dict[str, object]] = [{"id": 1, "text": "Örnekkent"}]
    assert aday_kimlikleri(items, "Zonguldak") == []  # eşik altı → aday yok
    assert aday_kimlikleri(items, "") == []  # boş ad → boş liste
