"""TKGM parsel API istemcisi: il/ilçe/mahalle çöz + parsel→geometri + soyağacı.

Kamuya açık parsel sorgusu. Malik/PII dönmez; yalnız kadastro+geometri.
``durum=1`` aktif (geometri var), ``durum=0`` pasif (geometri null → ``gittigiParselListe``
ardıl parsellere işaret eder = ifraz/tevhit/nakil soyağacı).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import structlog

from yatirimradar.ingestion.http import PoliteClient
from yatirimradar.sayi import parse_tr_number

__all__ = [
    "ArdilRef",
    "ParselSonuc",
    "TkgmClient",
    "aday_kimlikleri",
    "parse_parsel_feature",
    "parse_tr_number",  # geriye-uyum: eski import noktası (asıl impl: yatirimradar.sayi)
]

log = structlog.get_logger(__name__)

CBS = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api"
IL_LISTE_URL = (
    "https://parselsorgu.tkgm.gov.tr/app/modules/administrativeQuery/data/ilListe.json"
)

# Fuzzy mahalle eşleşmesi: tam/kısmi tutmayınca yakın adayları da üret (Köyü/Mahallesi,
# kısaltma, birleşme). Nihai doğruluk geometri doğrulaması (ada/parsel o mahallede yoksa
# 404 → aday elenir), o yüzden eşik yüksek ama adaylar liberal.
_FUZZY_ESIK = 0.86
# İdari ad son ekleri — fuzzy karşılaştırmada gürültü ("X Köyü" ↔ "X").
_AD_SONEKLERI = frozenset(
    {"mah", "mahalle", "mahallesi", "koy", "koyu", "belde", "beldesi", "mevki", "mevkii", "osb"}
)


@dataclass(frozen=True)
class ArdilRef:
    """Soyağacı ardıl parsel referansı (gittigiParselListe içinden)."""

    il: str | None
    ilce: str | None
    mahalle: str | None
    mahalle_id: int | None
    ada: str | None
    parsel: str | None
    durum: str | None
    geometry: dict[str, object] | None


@dataclass
class ParselSonuc:
    """Bir ada/parsel sorgusunun sonucu (bulundu/bulunamadı + öznitelikler + soyağacı)."""

    mahalle_id: int
    ada: str
    parsel_no: str
    found: bool
    durum: str | None = None
    il: str | None = None
    ilce: str | None = None
    mahalle: str | None = None
    mevkii: str | None = None
    nitelik: str | None = None
    alan_m2: float | None = None
    geometry: dict[str, object] | None = None
    lineage_sebep: str | None = None
    ardillar: list[ArdilRef] = field(default_factory=list)
    terminal_features: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, object] | None = None

    @property
    def has_geometry(self) -> bool:
        """Doğrudan ya da (özyinelemeli) soyağacı üzerinden geometri elde edilebilir mi?"""
        return self.geometry is not None or bool(self.terminal_features)


def _features(gittigi: object) -> list[dict[str, Any]]:
    """``gittigiParselListe`` JSON-string → feature dict listesi (boş/bozuksa [])."""
    if not gittigi or not isinstance(gittigi, str):
        return []
    try:
        fc = json.loads(gittigi)
    except (json.JSONDecodeError, TypeError):
        return []
    feats = fc.get("features", []) if isinstance(fc, dict) else []
    return [f for f in feats if isinstance(f, dict)]


def _parse_ardillar(gittigi: object) -> list[ArdilRef]:
    """Doğrudan (bir seviye) ardıl parseller — soyağacı kenarları için."""
    out: list[ArdilRef] = []
    for feat in _features(gittigi):
        p: dict[str, Any] = feat.get("properties") or {}
        out.append(
            ArdilRef(
                il=p.get("ilAd"),
                ilce=p.get("ilceAd"),
                mahalle=p.get("mahalleAd"),
                mahalle_id=p.get("mahalleId"),
                ada=p.get("adaNo"),
                parsel=p.get("parselNo"),
                durum=p.get("durum"),
                geometry=feat.get("geometry"),
            )
        )
    return out


def _collect_terminal_features(gittigi: object) -> list[dict[str, Any]]:
    """Soyağacı ağacını **özyinelemeli** gez; terminal (geometrili, aktif) feature'ları topla.

    Aktif ardıllar derinlerde olabilir (ifraz→nakil zinciri); tüm ağaç tek yanıta gömülü.
    Bu feature'lar kendi lokatörleriyle (mahalle/ada/parsel) güncel parsel olarak yazılır.
    """
    feats: list[dict[str, Any]] = []
    for feat in _features(gittigi):
        if isinstance(feat.get("geometry"), dict):
            feats.append(feat)
        p: dict[str, Any] = feat.get("properties") or {}
        feats.extend(_collect_terminal_features(p.get("gittigiParselListe")))
    return feats


def parse_parsel_feature(
    feature: dict[str, Any], mahalle_id: int, ada: int | str, parsel: int | str
) -> ParselSonuc:
    """TKGM parsel Feature'ını ParselSonuc'a çevir."""
    props: dict[str, Any] = feature.get("properties") or {}
    geometry = feature.get("geometry")
    return ParselSonuc(
        mahalle_id=mahalle_id,
        ada=str(ada),
        parsel_no=str(parsel),
        found=True,
        durum=props.get("durum"),
        il=props.get("ilAd"),
        ilce=props.get("ilceAd"),
        mahalle=props.get("mahalleAd"),
        mevkii=props.get("mevkii"),
        nitelik=props.get("nitelik"),
        alan_m2=parse_tr_number(props.get("alan")),
        geometry=geometry if isinstance(geometry, dict) else None,
        lineage_sebep=props.get("gittigiParselSebep") or None,
        ardillar=_parse_ardillar(props.get("gittigiParselListe")),
        terminal_features=_collect_terminal_features(props.get("gittigiParselListe")),
        raw=feature,
    )


class TkgmClient:
    """TKGM idari yapı + parsel sorgu istemcisi."""

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._owns = client is None
        self._client = client or PoliteClient()

    # --- idari yapı ---
    def il_liste(self) -> list[dict[str, object]]:
        r = self._client.get(IL_LISTE_URL)
        r.raise_for_status()
        return _properties(r.json())

    def ilce_liste(self, il_id: int) -> list[dict[str, object]]:
        r = self._client.get(f"{CBS}/idariYapi/ilceListe/{il_id}")
        r.raise_for_status()
        return _properties(r.json())

    def mahalle_liste(self, ilce_id: int) -> list[dict[str, object]]:
        r = self._client.get(f"{CBS}/idariYapi/mahalleListe/{ilce_id}")
        r.raise_for_status()
        return _properties(r.json())

    def find_id(self, items: list[dict[str, object]], name: str) -> int | None:
        """``text`` alanında ada göre id bul (Türkçe normalize, önce tam sonra kısmi)."""
        target = _norm(name)
        exact = [it for it in items if _norm(str(it.get("text", ""))) == target]
        partial = [it for it in items if target in _norm(str(it.get("text", "")))]
        for it in exact or partial:
            return _as_int(it.get("id"))
        return None

    def find_id_candidates(
        self, items: list[dict[str, object]], name: str, limit: int = 4
    ) -> list[int]:
        """Ada göre **sıralı aday id** listesi: tam → kısmi → fuzzy (benzerlik ≥ eşik).

        ``find_id`` tek (ilk) id döndürür; bu, mahalle adı TKGM listesiyle tam tutmadığında
        (Köyü/Mahallesi, kısaltma, birleşme) yakın adayları da verir. Nihai doğruluk
        ``TkgmGeocoder.coz``'daki **geometri doğrulamasıdır**, o yüzden adaylar liberal.
        """
        return aday_kimlikleri(items, name, limit=limit)

    # --- parsel ---
    def parsel(self, mahalle_id: int, ada: int | str, parsel: int | str) -> ParselSonuc:
        r = self._client.get(f"{CBS}/parsel/{mahalle_id}/{ada}/{parsel}")
        if r.status_code == 404:
            return ParselSonuc(mahalle_id, str(ada), str(parsel), found=False)
        r.raise_for_status()
        return parse_parsel_feature(r.json(), mahalle_id, ada, parsel)

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> TkgmClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _properties(data: object) -> list[dict[str, object]]:
    """FeatureCollection ya da düz liste → properties listesi."""
    if isinstance(data, dict) and "features" in data:
        return [f.get("properties", f) for f in data["features"]]
    if isinstance(data, list):
        return [f.get("properties", f) if isinstance(f, dict) else f for f in data]
    return []


def _as_int(value: object) -> int | None:
    """int/str → int; sayısal olmayan string (beklenmedik API yanıtı) sessizce ``None``."""
    if not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _norm(s: str) -> str:
    table = str.maketrans("İıÇçĞğÖöŞşÜü", "iiccggoossuu")
    # Nokta/fazla boşluk temizliği: "O.S.B" ↔ "OSB", "19  Mayıs" ↔ "19 Mayıs" eşleşsin.
    return " ".join(s.translate(table).replace(".", "").lower().split())


def _ad_sadelestir(norm: str) -> str:
    """Normalize edilmiş addan idari son ek jetonlarını at ("x koyu" → "x")."""
    jetonlar = [t for t in norm.split() if t not in _AD_SONEKLERI]
    return " ".join(jetonlar) or norm


def _benzerlik(a: str, b: str) -> float:
    """İki normalize ad arası 0..1 benzerlik (idari son ekler sadeleştirilir)."""
    return SequenceMatcher(None, _ad_sadelestir(a), _ad_sadelestir(b)).ratio()


def aday_kimlikleri(items: list[dict[str, object]], name: str, limit: int = 4) -> list[int]:
    """(saf) ``text`` alanında ada göre sıralı aday id: tam(3) → kısmi(2+) → fuzzy(≥eşik).

    Puan azalan sırada en çok ``limit`` id. Aynı id birden çok kez eşleşirse en yüksek
    puanı alır. Fuzzy eşiği altındakiler elenir (0 puan).
    """
    target = _norm(name)
    if not target:
        return []
    puanli: dict[int, float] = {}
    for it in items:
        i = _as_int(it.get("id"))
        if i is None:
            continue
        t = _norm(str(it.get("text", "")))
        if not t:
            continue
        if t == target:
            puan = 3.0
        elif target in t or t in target:
            puan = 2.0 + _benzerlik(target, t)
        else:
            r = _benzerlik(target, t)
            puan = r if r >= _FUZZY_ESIK else 0.0
        if puan > puanli.get(i, 0.0):
            puanli[i] = puan
    sirali = sorted((i for i, p in puanli.items() if p > 0.0), key=lambda i: -puanli[i])
    return sirali[:limit]
