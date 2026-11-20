"""Seçilen mahallede ada/parsel aralığını tarayarak TKGM çözüm oranını ölçer.

Çözüm oranı = (doğrudan geom + soyağacı üzerinden geom) / (var olan parseller).
404 'bulunamadı' = o ada/parsel yok → paydaya katılmaz.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import structlog

from yatirimradar.ingestion.http import save_raw
from yatirimradar.ingestion.tkgm import TkgmClient
from yatirimradar.store.repository import (
    kaydet_parsel,
    kaydet_soyagaci,
    kaydet_terminal_parseller,
)

log = structlog.get_logger(__name__)


@dataclass
class OrneklemRaporu:
    mahalle_id: int
    mahalle_adi: str
    denenen: int = 0
    bulunamadi: int = 0  # 404
    aktif_geom: int = 0  # durum=1, doğrudan geometri
    soyagaci_geom: int = 0  # durum=0 ama ardılda geometri var
    pasif_geomsuz: int = 0  # durum=0 ve hiç geometri yok
    hata: int = 0
    kaydedilen_parsel: int = 0
    kaydedilen_soyagaci: int = 0
    sure_s: float = 0.0

    @property
    def var_olan(self) -> int:
        return self.aktif_geom + self.soyagaci_geom + self.pasif_geomsuz

    @property
    def cozum_orani(self) -> float:
        return (self.aktif_geom + self.soyagaci_geom) / self.var_olan if self.var_olan else 0.0


def ornekle_mahalle(
    client: TkgmClient,
    mahalle_id: int,
    mahalle_adi: str,
    ada_max: int,
    parsel_max: int,
    kaydet: bool = True,
) -> OrneklemRaporu:
    """``ada in [1, ada_max]`` × ``parsel in [1, parsel_max]`` grid'ini tara."""
    rapor = OrneklemRaporu(mahalle_id=mahalle_id, mahalle_adi=mahalle_adi)
    t0 = time.monotonic()
    for ada in range(1, ada_max + 1):
        for parsel in range(1, parsel_max + 1):
            rapor.denenen += 1
            try:
                sonuc = client.parsel(mahalle_id, ada, parsel)
            except Exception as exc:  # noqa: BLE001 - tek sorgu hatası taramayı durdurmasın
                rapor.hata += 1
                log.warning("parcel_lookup_failed", block=ada, parcel=parsel, error=str(exc))
                continue

            if not sonuc.found:
                rapor.bulunamadi += 1
                continue

            raw_ref = None
            if sonuc.raw is not None:
                raw_ref = str(
                    save_raw(
                        json.dumps(sonuc.raw, ensure_ascii=False),
                        source="tkgm",
                        name=f"parsel_{mahalle_id}_{ada}_{parsel}.json",
                    )
                )

            if sonuc.geometry is not None:
                rapor.aktif_geom += 1
                if kaydet and kaydet_parsel(sonuc, raw_ref=raw_ref) is not None:
                    rapor.kaydedilen_parsel += 1
            elif sonuc.terminal_features:  # özyinelemeli soyağacında terminal (aktif) geom var
                rapor.soyagaci_geom += 1
                if kaydet:
                    rapor.kaydedilen_soyagaci += kaydet_soyagaci(sonuc)
                    rapor.kaydedilen_parsel += kaydet_terminal_parseller(sonuc)
            else:
                rapor.pasif_geomsuz += 1
                if kaydet and sonuc.ardillar:
                    rapor.kaydedilen_soyagaci += kaydet_soyagaci(sonuc)

    rapor.sure_s = round(time.monotonic() - t0, 1)
    return rapor
