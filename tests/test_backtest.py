"""Backtest takvim agregasyonu testleri (saf/DB'siz)."""

from __future__ import annotations

from yatirimradar.paper_trading.backtest import takvim_topla


def test_takvim_topla_kumulatif() -> None:
    kayitlar = [
        (2024, 0.5), (2024, 0.3), (2025, 0.4), (2026, 0.8), (None, 0.9),  # None atlanır
    ]
    takvim = takvim_topla(kayitlar)
    assert [t.yil for t in takvim] == [2024, 2025, 2026]  # sıralı
    assert [t.yeni for t in takvim] == [2, 1, 1]
    assert [t.kumulatif for t in takvim] == [2, 3, 4]  # kümülatif birikim
    assert takvim[0].ort_skor == 0.4  # (0.5+0.3)/2
    assert takvim[2].ort_skor == 0.8


def test_takvim_topla_bos() -> None:
    assert takvim_topla([]) == []
    assert takvim_topla([(None, 0.5)]) == []
