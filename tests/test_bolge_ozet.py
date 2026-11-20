"""İl özet katmanı saf testleri (ağsız/DB'siz) + tohum centroid yüklemesi."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yatirimradar.features.bolge_ozet import il_centroidleri, il_ekle, il_ozet_features


def test_il_ekle_varyant_birlestirir_ve_features() -> None:
    sayimlar: dict[str, dict[str, Any]] = {}
    il_ekle(sayimlar, "ANKARA", "ced", 40)
    il_ekle(sayimlar, "Ankara", "kamulastirma", 10)  # kasa varyantı → aynı ile birleşir
    il_ekle(sayimlar, "İZMİR", "ced", 5)
    il_ekle(sayimlar, "BİLİNMEYENİL", "ced", 3)
    centroidler = {"ankara": (32.5, 39.7), "izmir": (27.1, 38.4)}
    feats = il_ozet_features(sayimlar, centroidler)
    assert len(feats) == 2  # centroid'i bilinmeyen il atlandı; ANKARA+Ankara birleşti
    a = feats[0]["properties"]
    assert a["il"] == "Ankara"  # karışık kasa gösterim adı tercih edildi
    assert a["toplam"] == 50 and a["ced"] == 40 and a["kamulastirma"] == 10
    assert feats[0]["geometry"]["type"] == "Point"
    assert feats[0]["geometry"]["coordinates"] == [32.5, 39.7]
    assert a["yaklasik"] is True


def test_il_centroidleri_seed_yuklenir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dosya = tmp_path / "il_centroidleri.json"
    dosya.write_text('{"ornek": [31.25, 38.75], "eksik": [12]}', encoding="utf-8")
    monkeypatch.setattr("yatirimradar.features.bolge_ozet._CENTROID_DOSYA", dosya)
    il_centroidleri.cache_clear()
    c = il_centroidleri()
    il_centroidleri.cache_clear()
    assert c == {"ornek": (31.25, 38.75)}
