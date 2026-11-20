"""FastAPI harita uçlarının sentetik verilerle regresyon testleri."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yatirimradar.api.main import app


def test_index_route_maplibre_dondurur() -> None:
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "maplibre" in r.text.lower()
    assert "firsatlar.geojson" in r.text


def test_geojson_route_kayitli() -> None:
    # Rota tanımlı mı (DB'ye gitmeden yalnız varlık kontrolü).
    yollar = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/api/firsatlar.geojson" in yollar


def test_firsat_limiti_negatif_olamaz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yatirimradar.scoring.sirala.firsatlar_geojson", lambda **kw: {"limit": 0})
    client = TestClient(app)
    assert client.get("/api/firsatlar.geojson?limit=-1").status_code == 422
    assert client.get("/api/firsatlar.geojson?limit=0").status_code == 200


def test_bolgeler_ve_il_ozeti_son_surumden_gelir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'olaylar.db'}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """CREATE TABLE katalizor_olay (
                    id INTEGER PRIMARY KEY, tur TEXT, il TEXT, ilce TEXT, geom BLOB,
                    dedup_anahtar TEXT, knowledge_time DATETIME
                )"""
            )
            conn.exec_driver_sql(
                """INSERT INTO katalizor_olay VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (1, "ced", "Örnek", "Eski", None, "a", "2000-01-01"),
                    (2, "ced", "Örnek", "Yeni", b"nokta", "a", "2001-01-01"),
                    (3, "ced", "Örnek", "Silinen", b"nokta", "b", "2000-01-01"),
                    (4, "ced", "Örnek", "Kuzey", None, "b", "2001-01-01"),
                    (5, "ced", "Örnek", "A", None, None, "2000-01-01"),
                    (6, "ced", "Örnek", "B", None, None, "2000-01-01"),
                    (7, "ced", "Örnek", "Gelecek", b"nokta", "a", "2999-01-01"),
                    (8, "ced", "Örnek", "C", None, "5", "2001-01-01"),
                ],
            )
        sessions = sessionmaker(engine)
        monkeypatch.setattr("yatirimradar.features.bolge_ozet.session_scope", sessions)
        monkeypatch.setattr("yatirimradar.store.db.session_scope", sessions)
        monkeypatch.setattr(
            "yatirimradar.features.bolge_ozet.il_centroidleri", lambda: {"ornek": (31, 39)}
        )
        client = TestClient(app)
        assert client.get("/api/bolgeler").json() == {"Örnek": ["Yeni"]}
        (ozet,) = client.get("/api/bolge-ozet.geojson").json()["features"]
        assert ozet["properties"]["ced"] == ozet["properties"]["toplam"] == 4
    finally:
        engine.dispose()
