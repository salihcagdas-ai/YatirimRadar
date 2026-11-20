"""Rayiç (resmi asgari m² birim değer) yükleyici — CSV → ``fiyat`` tablosu.

Rayiç, arsa veya arazinin asgari m² birim değeridir. Yerel CSV cetvellerini
veritabanına aktaran bu modül, her fiyatın kaynağını ve geçerlilik yılını saklar.

CSV kolonları: ``il, ilce, mahalle, cadde_sokak, birim_fiyat_tl_m2, yil, kaynak, tip``
(mahalle/cadde boş olabilir → ilçe geneli). ``birim_fiyat_tl_m2`` TR sayı ("1.250,00") ya da düz.
``tip`` (ops.): ``rayic`` (resmi taban, vars.) | ``ilan`` (piyasa/asking proxy). Kaynaktan
bağımsız: GİB/belediye rayiç, lisanslı sağlayıcı (Endeksa/REIDIN) ortalama m² ya da
elle toplanan ilan değerleri aynı yükleyiciyle ``fiyat`` tablosuna akar.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from yatirimradar.config import get_settings
from yatirimradar.ingestion.tkgm import parse_tr_number
from yatirimradar.store.repository import kaydet_fiyat


@dataclass(frozen=True)
class RayicKaydi:
    """Tek rayiç gözlemi (asgari m² birim değeri)."""

    il: str | None
    ilce: str | None
    mahalle: str | None
    cadde_sokak: str | None
    birim_fiyat_tl_m2: float | None
    yil: int | None
    kaynak: str
    tip: str = "rayic"  # rayic (resmi taban) | ilan (piyasa/asking proxy)

    @property
    def kesinlik(self) -> float:
        """Granülerlik güveni: cadde→0.9, mahalle→0.7, ilçe→0.5."""
        if self.cadde_sokak:
            return 0.9
        if self.mahalle:
            return 0.7
        return 0.5

    @property
    def valid_time(self) -> datetime | None:
        """Takdir yılı başı (UTC)."""
        return datetime(self.yil, 1, 1, tzinfo=UTC) if self.yil else None


def _bos_none(v: str | None) -> str | None:
    t = (v or "").strip()
    return t or None


def parse_rayic_csv(text: str) -> list[RayicKaydi]:
    """Rayiç CSV metni → ``RayicKaydi`` listesi ('#' ile başlayan satırlar atlanır)."""
    satirlar = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not satirlar:
        return []
    reader = csv.DictReader(satirlar)
    out: list[RayicKaydi] = []
    for row in reader:
        norm = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        yil_str = norm.get("yil", "").strip()
        tip = (_bos_none(norm.get("tip")) or "rayic").lower()
        out.append(
            RayicKaydi(
                il=_bos_none(norm.get("il")),
                ilce=_bos_none(norm.get("ilce")),
                mahalle=_bos_none(norm.get("mahalle")),
                cadde_sokak=_bos_none(norm.get("cadde_sokak")),
                birim_fiyat_tl_m2=parse_tr_number(norm.get("birim_fiyat_tl_m2")),
                yil=int(yil_str) if yil_str.isdigit() else None,
                kaynak=_bos_none(norm.get("kaynak")) or "rayic-csv",
                tip=tip if tip in ("rayic", "ilan") else "rayic",
            )
        )
    return out


@dataclass
class RayicRaporu:
    """Rayiç yükleme özeti."""

    toplam: int = 0
    yazilan: int = 0
    atlanan: int = 0


def yukle_rayic(
    kayitlar: Iterable[RayicKaydi], kaydet: bool = True, *, yalniz_eksik: bool = False
) -> RayicRaporu:
    """Fiyat kayıtlarını ``fiyat``'a yaz (tip kayıttan: rayic|ilan; bitemporal, idempotent)."""
    rapor = RayicRaporu()
    for k in kayitlar:
        rapor.toplam += 1
        if not kaydet:
            continue
        _, eklendi = kaydet_fiyat(
            tip=k.tip,
            il=k.il,
            ilce=k.ilce,
            mahalle=k.mahalle,
            cadde_sokak=k.cadde_sokak,
            birim_fiyat_tl_m2=k.birim_fiyat_tl_m2,
            kaynak=k.kaynak,
            kesinlik=k.kesinlik,
            valid_time=k.valid_time,
            yalniz_eksik=yalniz_eksik,
        )
        if eklendi:
            rapor.yazilan += 1
        else:
            rapor.atlanan += 1
    return rapor


def yukle_rayic_csv(content: bytes | str, kaydet: bool = True) -> RayicRaporu:
    """CSV (byte/metin) → parse → ``fiyat``'a yükle."""
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    return yukle_rayic(parse_rayic_csv(text), kaydet=kaydet)


def yukle_rayic_dosyalari(csv_yol: Path | None = None, kaydet: bool = True) -> RayicRaporu:
    """CSV verilmezse yerel rayiç dizininden yalnız eksik gözlemleri tamamla."""
    dizin = get_settings().rayic_dizini
    yollar = [csv_yol] if csv_yol is not None else sorted(dizin.glob("*.csv"))
    kayitlar = [
        kayit for yol in yollar for kayit in parse_rayic_csv(yol.read_text("utf-8-sig"))
    ]
    return yukle_rayic(kayitlar, kaydet=kaydet, yalniz_eksik=csv_yol is None)
