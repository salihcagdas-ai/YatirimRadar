"""İl (province) bazında özet katmanı — kaba ulusal kapsama.

Olayların %94'ü ÇED metninde ada/parsel taşımaz → TKGM ile parsele oturmaz; bunları
**il merkezine yaklaşık** (sayımlı) işaretleriz. Hassas parsel katmanından ayrı tutulur
(``yaklasik=true``, düşük görsel ağırlık). İl merkezleri ``data/seed/il_centroidleri.json``
(ücretsiz TKGM il sınırlarından bir kez türetildi; kotasız).

Not: geom'suz olayların ``ilce`` alanı çoğu ham "PROJE YERİ" metni taşır (kirli) →
güvenilir alan **il**'dir; bu yüzden il granülünde toplanır.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select

from yatirimradar.config import PROJECT_ROOT
from yatirimradar.geocode.yer import _fold
from yatirimradar.store.db import session_scope
from yatirimradar.store.models import KatalizorOlay
from yatirimradar.store.olay import guncel_olay_idleri

_CENTROID_DOSYA = PROJECT_ROOT / "data" / "seed" / "il_centroidleri.json"


@lru_cache(maxsize=1)
def il_centroidleri() -> dict[str, tuple[float, float]]:
    """Folded il adı → (lon, lat). Tohum dosyasından (yoksa boş)."""
    try:
        ham = json.loads(_CENTROID_DOSYA.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        k: (float(v[0]), float(v[1]))
        for k, v in ham.items()
        if isinstance(v, list) and len(v) == 2
    }


def il_ekle(sayimlar: dict[str, dict[str, Any]], il: str | None, tur: str | None, n: int) -> None:
    """``sayimlar``'a (folded il anahtarlı) bir grubu ekle — kasa/biçim varyantları birleşir."""
    if not il:
        return
    anahtar = _fold(il)
    d = sayimlar.setdefault(anahtar, {"ad": il, "toplam": 0, "kamulastirma": 0, "ced": 0})
    if il and not il.isupper():  # daha okunaklı (karışık kasa) gösterim adını tercih et
        d["ad"] = il
    d["toplam"] += n
    if tur in ("kamulastirma", "ced"):
        d[tur] += n


def il_ozet_features(
    sayimlar: dict[str, dict[str, Any]], centroidler: dict[str, tuple[float, float]]
) -> list[dict[str, Any]]:
    """(saf) **folded-il anahtarlı** sayımlar + centroid → il merkezinde Point feature listesi.

    Centroid'i bilinmeyen il atlanır. Özellikler: il (ad), toplam, kamulastirma, ced, yaklasik.
    """
    feats: list[dict[str, Any]] = []
    for anahtar, c in sorted(sayimlar.items(), key=lambda kv: -kv[1]["toplam"]):
        nokta = centroidler.get(anahtar)
        if nokta is None:
            continue
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [nokta[0], nokta[1]]},
                "properties": {
                    "il": c["ad"],
                    "yaklasik": True,
                    "toplam": c["toplam"],
                    "kamulastirma": c["kamulastirma"],
                    "ced": c["ced"],
                },
            }
        )
    return feats


def il_ozet_geojson() -> dict[str, Any]:
    """Geometrisi çözülmemiş olayların il bazında sayımı → yaklaşık Point FeatureCollection."""
    sayimlar: dict[str, dict[str, Any]] = {}
    with session_scope() as s:
        stmt = (
            select(KatalizorOlay.il, KatalizorOlay.tur, func.count())
            .where(
                KatalizorOlay.id.in_(guncel_olay_idleri(datetime.now(UTC))),
                KatalizorOlay.geom.is_(None),
                KatalizorOlay.il.isnot(None),
            )
            .group_by(KatalizorOlay.il, KatalizorOlay.tur)
        )
        for il, tur, n in s.execute(stmt):
            il_ekle(sayimlar, il, tur, int(n))
    return {"type": "FeatureCollection", "features": il_ozet_features(sayimlar, il_centroidleri())}
