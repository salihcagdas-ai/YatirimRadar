"""Rayiçlerin CSV ve bitemporal fiyat tablosundan harita yanıtına kadar doğrulanması."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from yatirimradar.api.main import app
from yatirimradar.features import rayic_ozet
from yatirimradar.ingestion.rayic import parse_rayic_csv
from yatirimradar.store.models import Fiyat

YOL = "/api/rayic-ozet.geojson"


@pytest.fixture(autouse=True)
def nokta_dosyasi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    klasor = tmp_path / "data" / "seed"
    klasor.mkdir(parents=True)
    veri = {
        "bilgi_zamani": "2030-05-01T00:00:00Z",
        "noktalar": [
            {
                "il": "Örnekkent",
                "ilce": "Deneme",
                "mahalle": f"Kurgusal {i}",
                "osm_node": i,
                "koordinat": [float(i), float(i)],
            }
            for i in range(1, 7)
        ],
    }
    (klasor / "mahalle_noktalari.json").write_text(json.dumps(veri), encoding="utf-8")
    monkeypatch.setattr(rayic_ozet, "PROJECT_ROOT", tmp_path)
    rayic_ozet._mahalle_noktalari.cache_clear()
    yield
    rayic_ozet._mahalle_noktalari.cache_clear()


def test_csv_alti_mahalle(fiyat_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rayic_ozet, "session_scope", sessionmaker(fiyat_session.get_bind()))
    csv = "il,ilce,mahalle,cadde_sokak,birim_fiyat_tl_m2,yil,kaynak,tip\n"
    for i in range(1, 7):
        csv += f"Örnekkent,Deneme,Kurgusal {i} MAH.,Test cadde,{i * 10},2030,test,rayic\n"
    csv += "Örnekkent,Deneme,Kurgusal 1 MAH.,Test yol,0,2030,test,rayic\n"
    kayitlar = [
        {
            "tip": k.tip,
            "il": k.il,
            "ilce": k.ilce,
            "mahalle": k.mahalle,
            "cadde_sokak": k.cadde_sokak,
            "birim_fiyat_tl_m2": k.birim_fiyat_tl_m2,
            "kaynak": k.kaynak,
            "valid_time": k.valid_time,
            "knowledge_time": datetime(2030, 6, 1, tzinfo=UTC),
        }
        for k in parse_rayic_csv(csv)
    ]
    fiyat_session.execute(insert(Fiyat), kayitlar)
    fiyat_session.commit()
    client = TestClient(app)
    params = {"yil": "2030", "as_of": "2030-12-01T00:00:00Z"}
    yanit = client.get(YOL, params=params)
    assert yanit.status_code == 200
    features = yanit.json()["features"]
    assert len(features) == 6
    assert sum(f["properties"]["adet"] for f in features) == 7
    assert sum(f["properties"]["pozitif_adet"] for f in features) == 6
    for f in features:
        assert f["geometry"]["type"] == "Point"
        assert f["properties"]["konum_url"].startswith("https://www.openstreetmap.org/node/")
        assert f["properties"]["min_tl_m2"] > 0
    assert client.get(YOL, params={**params, "yil": "2024"}).json()["features"] == []


def test_rayic_yil_surumu_kaynak_ve_konum(
    fiyat_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rayic_ozet, "session_scope", sessionmaker(fiyat_session.get_bind()))
    bilinen = datetime(2025, 6, 1, tzinfo=UTC)
    temel = {
        "tip": "rayic",
        "il": "Örnekkent",
        "ilce": "Deneme",
        "mahalle": "KURGUSAL 1 MAH.",
        "cadde_sokak": "1. Sokak",
        "birim_fiyat_tl_m2": 100,
        "kaynak": "sentetik-test",
        "valid_time": datetime(2025, 1, 1, tzinfo=UTC),
        "knowledge_time": bilinen,
    }
    fiyat_session.execute(
        insert(Fiyat),
        [
            temel,
            {**temel, "birim_fiyat_tl_m2": 150, "knowledge_time": datetime(2026, 2, 1, tzinfo=UTC)},
            {**temel, "tip": "ilan", "birim_fiyat_tl_m2": 5000},
            {**temel, "kaynak": "placeholder", "cadde_sokak": "Örnek"},
            {**temel, "valid_time": datetime(2024, 1, 1, tzinfo=UTC), "birim_fiyat_tl_m2": 75},
            {
                **temel,
                "mahalle": "Kurgusal 1 Mahallesi",
                "cadde_sokak": "2. Sokak",
                "birim_fiyat_tl_m2": 50,
            },
            {**temel, "ilce": "Denemedüzü", "birim_fiyat_tl_m2": 0},
        ],
    )
    fiyat_session.commit()
    client = TestClient(app)
    eski = client.get(YOL, params={"as_of": "2025-12-31T22:00:00Z", "yil": 2025}).json()
    mahalleler = {f["properties"]["ilce"]: f for f in eski["features"]}
    assert len(mahalleler) == 2
    assert mahalleler["Deneme"]["geometry"] is None  # Konum henüz projede bilinmiyordu.
    p = mahalleler["Deneme"]["properties"]
    assert (p["adet"], p["min_tl_m2"], p["ort_tl_m2"], p["max_tl_m2"]) == (2, 50, 75, 100)
    yeni = client.get(YOL, params={"as_of": "2030-12-01T00:00:00Z"}).json()
    assert len(yeni["features"]) == 3  # 2024 ayrı
    son = {f["properties"]["ilce"]: f for f in yeni["features"] if f["properties"]["yil"] == 2025}
    assert son["Deneme"]["properties"]["ort_tl_m2"] == 100
    assert (
        son["Denemedüzü"]["geometry"] is None
    )  # İki ilçedeki adaş mahallelere aynı nokta verilmez.
    assert son["Denemedüzü"]["properties"]["pozitif_adet"] == 0
    assert son["Denemedüzü"]["properties"]["min_tl_m2"] is None
    assert client.get(YOL, params={"yil": 0}).status_code == 422
    assert client.get(YOL, params={"as_of": "2026-12-01T00:00:00"}).status_code == 422
