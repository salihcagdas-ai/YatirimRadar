"""İleriye dönük izleme kayıtları aynı bilgi kesiti ve skorla yeniden okunabilmeli."""

import json
from datetime import UTC, datetime
from pathlib import Path

from yatirimradar.paper_trading.gunluk import tahminleri_kaydet
from yatirimradar.scoring.sirala import SiraliFirsat
from yatirimradar.scoring.skor import SkorBilesen


def test_tahmin_gunlugu_kesiti_ve_faktorleri_saklar(tmp_path: Path) -> None:
    kesim = datetime(2025, 5, 5, tzinfo=UTC)
    bilesen = SkorBilesen(0.9, 0.8, 0.7, 0.6)
    firsat = SiraliFirsat(1, "ced", "Örnek", None, None, 0.8, kesim, False, None, bilesen)
    yol = tahminleri_kaydet([firsat], kesim, "Örnek", tmp_path)
    tekrar = tahminleri_kaydet([firsat], kesim, "Örnek", tmp_path)
    bos = tahminleri_kaydet([], kesim, None, tmp_path)
    assert yol == tekrar and yol != bos
    veri = json.loads(yol.read_text("utf-8"))
    assert veri["knowledge_time"] == kesim.isoformat()
    assert veri["ufuk_ay"] == 12
    satir = veri["firsatlar"][0]
    assert satir["olay_id"] == satir["sira"] == 1
    assert satir["skor"] == firsat.skor and satir["bilesen"]["ucuzluk"] == 0.6
