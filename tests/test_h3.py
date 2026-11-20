"""H3 yoğunluk öznitelik testleri (saf/DB'siz)."""

from __future__ import annotations

from yatirimradar.features.h3_ozellik import hucrelere_cevir, noktalari_h3_say


def test_ayni_konum_ayni_hucre() -> None:
    # Aynı/çok yakın noktalar tek hücrede toplanır; uzak nokta ayrı hücrede.
    noktalar = [(32.10, 39.30), (32.101, 39.301), (28.00, 38.50)]
    sayim = noktalari_h3_say(noktalar, cozunurluk=7)
    assert sum(sayim.values()) == 3
    assert max(sayim.values()) == 2  # iki yakın nokta aynı hücrede
    assert len(sayim) == 2  # iki ayrı hücre


def test_hucrelere_cevir_siralama() -> None:
    sayim = noktalari_h3_say([(32.1, 39.3), (32.1, 39.3), (28.0, 38.5)], cozunurluk=7)
    hucreler = hucrelere_cevir(sayim)
    assert hucreler[0].sayi == 2  # en yoğun başta
    assert hucreler[-1].sayi == 1
    # merkez koordinat makul (lon/lat aralığı)
    assert all(25 < h.lon < 45 and 35 < h.lat < 43 for h in hucreler)
