"""Rayiç CSV parser birim testleri (ağsız/DB'siz)."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from yatirimradar.cli import app
from yatirimradar.config import get_settings
from yatirimradar.ingestion.rayic import parse_rayic_csv, yukle_rayic_dosyalari
from yatirimradar.store import repository
from yatirimradar.store.models import Fiyat

CSV = """# yorum satiri atlanir
il,ilce,mahalle,cadde_sokak,birim_fiyat_tl_m2,yil,kaynak
Örnekil,Batı,,,200,2026,placeholder
Örnekil,Batı,Örnek,,"1.250,00",2026,sentetik-test
Örnekil,Doğu,,Deneme Sokağı,3500,2022,sentetik-test
"""


def test_parse_rayic_csv() -> None:
    kayitlar = parse_rayic_csv(CSV)
    assert len(kayitlar) == 3

    ilce_geneli = kayitlar[0]
    assert ilce_geneli.il == "Örnekil" and ilce_geneli.ilce == "Batı"
    assert ilce_geneli.mahalle is None and ilce_geneli.cadde_sokak is None
    assert ilce_geneli.birim_fiyat_tl_m2 == 200.0
    assert ilce_geneli.kesinlik == 0.5  # yalnız ilçe
    assert ilce_geneli.valid_time == datetime(2026, 1, 1, tzinfo=UTC)

    mahalle = kayitlar[1]
    assert mahalle.mahalle == "Örnek"
    assert mahalle.birim_fiyat_tl_m2 == 1250.0  # TR sayı "1.250,00"
    assert mahalle.kesinlik == 0.7  # mahalle

    cadde = kayitlar[2]
    assert cadde.cadde_sokak == "Deneme Sokağı"
    assert cadde.kesinlik == 0.9  # cadde
    assert cadde.yil == 2022
    assert all(k.tip == "rayic" for k in kayitlar)  # tip kolonu yoksa varsayılan rayic


def test_parse_rayic_csv_tip_kolonu() -> None:
    # tip kolonu: ilan (piyasa proxy) ya da rayic; geçersiz → rayic'e düşer.
    csv = (
        "il,ilce,birim_fiyat_tl_m2,tip\n"
        "Örnekil,Merkez,8000,ilan\n"
        "Örnekil,Merkez,3000,RAYIC\n"
        "Örnekil,Merkez,9999,sacma\n"
    )
    k = parse_rayic_csv(csv)
    assert [x.tip for x in k] == ["ilan", "rayic", "rayic"]


def test_parse_rayic_csv_bos() -> None:
    assert parse_rayic_csv("") == []
    assert parse_rayic_csv("# yalniz yorum\n") == []


def test_yerel_cetveller_ve_yeniden_yukleme(
    fiyat_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dizin = tmp_path / "cetveller"
    monkeypatch.setattr(get_settings(), "rayic_dizini", dizin)
    monkeypatch.setattr(repository, "session_scope", lambda: nullcontext(fiyat_session))
    conn = fiyat_session.connection().connection.driver_connection
    assert conn is not None
    conn.create_function("pg_advisory_xact_lock", 1, lambda _kilit: None)
    conn.create_function("GeomFromEWKT", 1, lambda g: g)
    komut = CliRunner()
    bos = komut.invoke(app, ["import-assessments"])
    assert bos.exit_code == 0 and "No assessment records found" in bos.output

    baslik = "il,ilce,mahalle,cadde_sokak,birim_fiyat_tl_m2,yil,kaynak\n"
    adres = "Deneme,Testli,Mahalle,Sokak"
    dizin.mkdir()
    dosya = dizin / "bir.csv"
    dosya.write_text(baslik + f"{adres},200,2024,test\n", encoding="utf-8-sig")
    (dizin / "iki.csv").write_text(baslik + f"{adres} 2,400,2024,test\n", encoding="utf8")
    sonuc = komut.invoke(app, ["rayic-yukle"])
    assert sonuc.exit_code == 0, sonuc.exception
    assert yukle_rayic_dosyalari().atlanan == 2
    # Açıkça verilen düzeltilmiş CSV ek sürüm oluşturur; eski cetvel sonraki
    # hasatta bu düzeltmeyi geri almamalı.
    duzeltme = tmp_path / "duzeltme.csv"
    duzeltme.write_text(baslik + f"{adres},300,2024,test\n", encoding="utf8")
    assert komut.invoke(app, ["rayic-yukle", "--csv", str(duzeltme)]).exit_code == 0
    assert yukle_rayic_dosyalari().yazilan == 0
    assert list(fiyat_session.scalars(select(Fiyat.birim_fiyat_tl_m2).order_by(Fiyat.id))) == [
        200.0, 400.0, 300.0,
    ]
