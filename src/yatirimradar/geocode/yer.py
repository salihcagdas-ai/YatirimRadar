"""Serbest metin yer/lokatör ayrıştırıcı (deterministik).

ÇED ``PROJE YERİ`` (ve benzeri) serbest metninden **temsili tek**
``(il, ilçe, mahalle, ada, parsel)`` çıkarır → TKGM ile geometriye çözülür (geocode).

Yer metni birden çok ilçe, mahalle veya parsel içerebilir. Ayrıştırıcı tireli
parsel listelerini, "NOLU/NUMARALI PARSEL" ve mevki gibi ifadeleri tanır.
Amaç mükemmel ayrıştırma değil; **çözülebilir olanı** (mahalle+ada+parsel) yakalamak;
gerisi geometрisiz ("izleme") kalır.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass

# Pozisyon-koruyan Türkçe→ascii-küçük fold (translate 1:1 → folded span == original span).
_FOLD = {
    ord(u): ord(low)
    for u, low in zip(string.ascii_uppercase, string.ascii_lowercase, strict=True)
}
_FOLD.update(
    {ord(a): ord(b) for a, b in zip("İIıŞşĞğÇçÖöÜü", "iiissggccoouu", strict=True)}
)


def _fold(s: str) -> str:
    """Türkçe harfleri ascii-küçüğe çevir (uzunluk korunur, konumlar hizalı kalır)."""
    return s.translate(_FOLD)


@dataclass(frozen=True)
class YerLokator:
    """Serbest metinden çıkarılmış temsili kadastro lokatörü."""

    il: str | None = None
    ilce: str | None = None
    mahalle: str | None = None
    ada: str | None = None
    parsel: str | None = None

    @property
    def geocode_edilebilir(self) -> bool:
        """Çözmeye yeter mi? (mahalle+ada+parsel; ilçe yoksa il içinde aranır)."""
        return bool(self.mahalle and self.ada and self.parsel)


def _ad_oncesi(folded: str, original: str, kw: str) -> str | None:
    """``<ad> <keyword>`` deseninde keyword'den önceki adı (orijinal kasayla) döndür."""
    m = re.search(rf"([^,()]+?)\s+{kw}", folded)
    if not m:
        return None
    ad = original[m.start(1) : m.end(1)].strip(" .,-")
    return ad or None


def _ilk_ad(ad: str | None) -> str | None:
    """Çok-isimli grubun ilkini al ("X VE Y" / "X, Y" → "X")."""
    if not ad:
        return None
    parcalar = re.split(r"\s+[Vv][Ee]\s+|,|/", ad)
    for p in parcalar:
        t = p.strip(" .,-")
        if t:
            return t
    return None


# Folded "ilçesi"/"ilçeleri" belirteci — mahalle adının başındaki "X İlçesi" önekini atmak için.
_ILCE_BELIRTEC_RE = re.compile(r"ilce(?:si|leri)\b")


def _mahalle_temizle(ad: str | None) -> str | None:
    """Virgülle ayrılmamış ilçe önekini mahalle adından kaldırır.

    Sık desen: "{İl} {İlçe} İlçesi {Mahalle} Mahallesi" virgülsüz yazıldığında ``_ad_oncesi``
    "Mahallesi" öncesini olduğu gibi alır → mahalle "İlçesi" önekiyle kirlenir, TKGM bulamaz.
    Son "ilçesi/ilçeleri" belirtecinden sonrası gerçek mahalledir.
    """
    if not ad:
        return None
    eslesmeler = list(_ILCE_BELIRTEC_RE.finditer(_fold(ad)))
    if eslesmeler:
        ad = ad[eslesmeler[-1].end() :].strip(" .,-/")
    return ad or None


def parse_yer(text: str | None) -> YerLokator:
    """Serbest yer metni → ``YerLokator`` (temsili tek lokatör)."""
    if not text or not text.strip():
        return YerLokator()
    folded = _fold(text)

    il = _ilk_ad(_ad_oncesi(folded, text, r"il(?:i)\b"))
    ilce = _ilk_ad(_ad_oncesi(folded, text, r"ilce(?:si|leri)\b"))
    mahalle = _ilk_ad(_mahalle_temizle(_ad_oncesi(folded, text, r"mahalle(?:si|leri)\b")))

    ada_m = re.search(r"(\d+)\s*ada\b", folded)
    ada = ada_m.group(1) if ada_m else None

    # Tekil, numaralı ve tireli parsel biçimlerinde ilk parsel numarasını al.
    parsel = None
    par_m = re.search(r"(\d[\d\s-]*?)\s*(?:nolu\s+|numarali\s+)?parsel", folded)
    if par_m:
        ilk = re.search(r"\d+", par_m.group(1))
        parsel = ilk.group() if ilk else None

    return YerLokator(il=il, ilce=ilce, mahalle=mahalle, ada=ada, parsel=parsel)
