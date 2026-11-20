"""TR sayı ayrıştırma (saf, paylaşımlı).

Kaynaklar karışık biçim kullanır: TKGM "14.979,00" (nokta binlik + virgül ondalık),
rayiç CSV düz "300.5" (nokta ondalık) ya da "1.250" (binlik), ÇED özeti "41,877 MW"
(virgül ondalık). Heuristik:

- Hem ``.`` hem ``,`` → TR biçimi kesin: nokta binlik, virgül ondalık.
- Yalnız ``,``       → ondalık virgül.
- Yalnız ``.``       → birden çok nokta ya da tek nokta + **tam 3 hane** → binlik
  ("1.500.000", "1.250"); aksi halde ondalık nokta ("300.5", "1.2345").
"""

from __future__ import annotations


def parse_tr_number(value: object) -> float | None:
    """Karışık TR/düz sayı biçimini float'a çevir; çevrilemezse ``None``."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if "." in text and "," in text:
            return float(text.replace(".", "").replace(",", "."))
        if "," in text:
            return float(text.replace(",", "."))
        if "." in text:
            parcalar = text.split(".")
            # "1.500.000" ya da "1.250" → binlik; "300.5" / "1.2345" → ondalık.
            if len(parcalar) > 2 or len(parcalar[1]) == 3:
                return float(text.replace(".", ""))
            return float(text)
        return float(text)
    except ValueError:
        return None
