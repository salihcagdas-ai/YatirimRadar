"""Fresh-install behavior and exit codes for the pipeline and daily refresh."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from yatirimradar import cli
from yatirimradar.config import get_settings
from yatirimradar.durum import Durum
from yatirimradar.geocode.arkaplan import ArkaplanRaporu
from yatirimradar.ingestion.rayic import RayicRaporu


def test_pipeline_ve_gunluk_girisleri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "frontend").mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(get_settings(), "data_processed_dir", tmp_path / "data" / "processed")
    monkeypatch.setattr(get_settings(), "rayic_dizini", tmp_path / "yerel")
    monkeypatch.setattr("yatirimradar.scoring.sirala.firsatlari_sirala", lambda **kw: [])
    monkeypatch.setattr("yatirimradar.scoring.sirala.firsatlar_geojson", lambda **kw: {
        "type": "FeatureCollection", "features": [],
    })
    komut = CliRunner()
    sonuc = komut.invoke(cli.app, ["pipeline", "--no-fetch", "--province", "all"])
    assert sonuc.exit_code == 0, sonuc.exception
    assert "No local assessment records found" in sonuc.output
    assert (tmp_path / "frontend" / "firsatlar.geojson").is_file()

    gun = Mock(side_effect=OSError("test source offline"))
    rayic = Mock(return_value=RayicRaporu(toplam=4, yazilan=2, atlanan=2))
    monkeypatch.setattr("yatirimradar.ingestion.resmigazete.tara_gun", gun)
    monkeypatch.setattr("yatirimradar.ingestion.rayic.yukle_rayic_dosyalari", rayic)
    monkeypatch.setattr("yatirimradar.durum.durum_topla", lambda: Durum())
    monkeypatch.setattr(
        "yatirimradar.geocode.arkaplan.geocode_partisi", lambda **kw: ArkaplanRaporu()
    )
    yarim = komut.invoke(cli.app, ["daily", "--days", "1"])
    assert yarim.exit_code == 1 and "test source offline" in yarim.output
    assert "[daily] Coverage" in yarim.output
    assert "Assessments: 2 new, 2 already present" in yarim.output
    tam = komut.invoke(cli.app, ["gunluk", "--gun-geri", "0"])
    assert tam.exit_code == 0 and "[daily] Complete" in tam.output, tam.output
