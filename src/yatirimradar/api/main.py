"""Local map and API for public land-use decisions in Türkiye."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import AwareDatetime

from yatirimradar.config import PROJECT_ROOT, get_settings

app = FastAPI(
    title="YatırımRadar API",
    version="0.0.1",
    description=(
        "Explore environmental impact assessment (EIA) and expropriation decisions, "
        "look up cadastral parcels, and review local land assessments in Türkiye. "
        "The API uses Turkish field names to match the underlying records."
    ),
)


@app.get("/api/firsatlar.geojson", summary="List ranked events with parcel geometry")
def firsatlar(
    il: Annotated[str, Query(description="Province name, or 'all' for nationwide results.")] = "",
    limit: Annotated[int, Query(ge=0, description="Maximum number of features to return.")] = 300,
) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection ordered by score. The default is the pilot province."""
    from yatirimradar.scoring.sirala import firsatlar_geojson

    s = get_settings()
    il_filtre = None if il in ("tum", "all") else (il or s.pilot_il)
    return firsatlar_geojson(il=il_filtre, limit=limit)


@app.get("/api/bolge-ozet.geojson", summary="Count unmapped events by province")
def bolge_ozet() -> dict[str, Any]:
    """Group events whose parcel geometry is unknown by province.

    Each point represents a province. Its properties give the event counts and mark the
    location as approximate (`yaklasik=true`).
    """
    from yatirimradar.features.bolge_ozet import il_ozet_geojson

    return il_ozet_geojson()


@app.get("/api/rayic-ozet.geojson", summary="Summarize assessed land values by neighborhood")
def rayic_ozet(
    yil: Annotated[int | None, Query(ge=1, le=9999, description="Assessment year.")] = None,
    as_of: Annotated[
        AwareDatetime | None,
        Query(description="Time at which the records had to be known, including a UTC offset."),
    ] = None,
) -> dict[str, Any]:
    """Return counts and assessed values in TRY/m² for each neighborhood and assessment year.

    Minimum, mean and maximum values use positive observations. Neighborhoods with no
    known location have null geometry and remain in the results.
    """
    from yatirimradar.features.rayic_ozet import rayic_ozet_geojson

    return rayic_ozet_geojson(yil=yil, as_of=as_of)


@app.get("/api/bolgeler", summary="List provinces and districts with mapped events")
def bolgeler() -> dict[str, list[str]]:
    """Return a mapping from each province to the districts available in the event map."""
    from sqlalchemy import select

    from yatirimradar.store.db import session_scope
    from yatirimradar.store.models import KatalizorOlay
    from yatirimradar.store.olay import guncel_olay_idleri

    harita: dict[str, set[str]] = {}
    with session_scope() as ss:
        # Unmapped events can contain free-form source text in the district field.
        stmt = (
            select(KatalizorOlay.il, KatalizorOlay.ilce)
            .where(
                KatalizorOlay.id.in_(guncel_olay_idleri(datetime.now(UTC))),
                KatalizorOlay.il.isnot(None),
                KatalizorOlay.geom.isnot(None),
            )
            .group_by(KatalizorOlay.il, KatalizorOlay.ilce)
        )
        for il, ilce in ss.execute(stmt):
            harita.setdefault(str(il), set())
            if ilce:
                harita[str(il)].add(str(ilce))
    return {il: sorted(ilceler) for il, ilceler in sorted(harita.items())}


@app.get("/api/parsel", summary="Look up a cadastral parcel")
def parsel(
    il: Annotated[str, Query(description="Province name.")],
    ilce: Annotated[str, Query(description="District name, if known.")] = "",
    mahalle: Annotated[str, Query(description="Neighborhood name. Required for lookup.")] = "",
    ada: Annotated[str, Query(description="Cadastral block number. Required for lookup.")] = "",
    parsel: Annotated[str, Query(description="Cadastral parcel number. Required for lookup.")] = "",
) -> dict[str, Any]:
    """Query Türkiye's land registry (TKGM) for parcel geometry, area and land use.

    A match sets `bulundu` to true and includes a GeoJSON feature. A lookup failure
    returns an error message in `hata`. The `kota` flag indicates that TKGM limited the query.
    """
    from yatirimradar.geocode.cozucu import TkgmGeocoder, TkgmRateLimitError, temsili_geometri
    from yatirimradar.geocode.yer import YerLokator
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.tkgm import TkgmClient
    from yatirimradar.store.repository import geojson_to_multipolygon

    if not (il and mahalle and ada and parsel):
        return {"hata": "Enter a province, neighborhood, block number and parcel number."}

    lok = YerLokator(
        il=il.strip() or None,
        ilce=ilce.strip() or None,
        mahalle=mahalle.strip() or None,
        ada=ada.strip() or None,
        parsel=parsel.strip() or None,
    )
    if not lok.geocode_edilebilir:
        return {"hata": "Enter a neighborhood, block number and parcel number."}

    gc = TkgmGeocoder(TkgmClient(PoliteClient(delay_seconds=1.0)))
    try:
        sonuc = gc.coz(il, lok)
    except TkgmRateLimitError as exc:
        return {"hata": f"TKGM limited the lookup ({exc}).", "kota": True}
    except Exception as exc:  # noqa: BLE001 - isolate a failure in the external service
        return {"hata": f"The parcel lookup failed ({exc})."}
    finally:
        gc.close()

    if sonuc is None:
        return {"bulundu": False}

    geojson = temsili_geometri(sonuc)
    if geojson is None or geojson_to_multipolygon(geojson) is None:
        return {"bulundu": False}
    return {
        "bulundu": True,
        "feature": {
            "type": "Feature",
            "geometry": geojson,
            "properties": {
                "il": sonuc.il or il,
                "ilce": sonuc.ilce or ilce or None,
                "mahalle": sonuc.mahalle or mahalle,
                "ada": sonuc.ada,
                "parsel": sonuc.parsel_no,
                "alan_m2": round(sonuc.alan_m2, 1) if sonuc.alan_m2 else None,
                "nitelik": sonuc.nitelik,
                "mevki": sonuc.mevkii,
            },
        },
    }


@app.get("/", response_class=HTMLResponse, summary="Open the interactive map")
def index() -> str:
    """Display the MapLibre interface for event, assessment and parcel layers."""
    yol = PROJECT_ROOT / "frontend" / "index.html"
    if not yol.exists():
        return "<h1>Map unavailable</h1><p>The frontend/index.html file is missing.</p>"
    return yol.read_text(encoding="utf-8")
