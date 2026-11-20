"""H3 mekânsal öznitelik: katalizör yoğunluğu (kümelenme sinyali).

Geocoded katalizör olayları H3 altıgen hücrelerine atanır; hücre başına olay sayısı
**kümelenme** sinyalidir (aynı hücrede çok katalizör = daha güçlü bölgesel dönüşüm
beklentisi). İleride skorlamaya ``yogunluk`` çarpanı olarak girebilir.

H3 res 7 ≈ 5.7 km² (ilçe-altı kümeleme için uygun).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import h3

VARSAYILAN_COZUNURLUK = 7


def noktalari_h3_say(
    noktalar: list[tuple[float, float]], cozunurluk: int = VARSAYILAN_COZUNURLUK
) -> dict[str, int]:
    """``(lon, lat)`` noktalarını H3 hücrelerine ata, hücre başına say (saf — DB yok).

    Girdi GeoJSON düzeninde (lon, lat); H3 (lat, lng) ister → çevrilir.
    """
    sayac: Counter[str] = Counter()
    for lon, lat in noktalar:
        sayac[h3.latlng_to_cell(lat, lon, cozunurluk)] += 1
    return dict(sayac)


@dataclass(frozen=True)
class H3Hucre:
    """Bir H3 hücresi: kimlik + katalizör sayısı + merkez (lat, lon)."""

    hucre: str
    sayi: int
    lat: float
    lon: float


def hucrelere_cevir(
    sayim: dict[str, int],
) -> list[H3Hucre]:
    """Hücre→sayı haritasını merkez koordinatlı ``H3Hucre`` listesine çevir (sayıya göre azalan)."""
    out: list[H3Hucre] = []
    for hucre, sayi in sayim.items():
        lat, lon = h3.cell_to_latlng(hucre)
        out.append(H3Hucre(hucre=hucre, sayi=sayi, lat=round(lat, 5), lon=round(lon, 5)))
    out.sort(key=lambda h: h.sayi, reverse=True)
    return out


def katalizor_yogunlugu(
    il: str | None = None, cozunurluk: int = VARSAYILAN_COZUNURLUK
) -> list[H3Hucre]:
    """Geocoded katalizör olaylarının H3 yoğunluğu (DB; merkez noktaları kullanılır)."""
    from sqlalchemy import func, select

    from yatirimradar.store.db import session_scope
    from yatirimradar.store.models import KatalizorOlay

    stmt = select(
        func.ST_X(func.ST_Centroid(KatalizorOlay.geom)),
        func.ST_Y(func.ST_Centroid(KatalizorOlay.geom)),
    ).where(KatalizorOlay.geom.isnot(None))
    if il:
        stmt = stmt.where(KatalizorOlay.il == il)
    with session_scope() as s:
        noktalar = [(float(x), float(y)) for x, y in s.execute(stmt) if x is not None]
    return hucrelere_cevir(noktalari_h3_say(noktalar, cozunurluk))
