"""İleriye dönük izleme için skor anlık görüntülerini yerel diskte saklar."""

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from yatirimradar.scoring.sirala import SiraliFirsat


def tahminleri_kaydet(
    firsatlar: list[SiraliFirsat], as_of: datetime, il: str | None, dizin: Path
) -> Path:
    """Sıralama, karar tarihi ve skor bileşenlerini aynı bilgi kesitinde dondur."""
    if as_of.utcoffset() is None:
        raise ValueError("The observation timestamp must include a time zone.")
    kesim = as_of.astimezone(UTC)
    satirlar = []
    for sira, firsat in enumerate(firsatlar, 1):
        satirlar.append({
            **asdict(firsat),
            "sira": sira,
            "valid_time": firsat.valid_time.isoformat() if firsat.valid_time else None,
            "skor": firsat.skor,
        })
    govde = json.dumps(
        {"knowledge_time": kesim.isoformat(), "il": il, "ufuk_ay": 12, "firsatlar": satirlar},
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    dizin.mkdir(parents=True, exist_ok=True)
    imza = hashlib.sha256(govde).hexdigest()[:16]
    hedef = dizin / f"{kesim:%Y%m%dT%H%M%S%fZ}-{imza}.json"
    with NamedTemporaryFile(dir=dizin, suffix=".tmp", delete=False) as dosya:
        gecici = Path(dosya.name)
    try:
        gecici.write_bytes(govde)
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)
    return hedef
