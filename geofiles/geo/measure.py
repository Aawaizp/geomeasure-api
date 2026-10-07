"""Accurate area and length measurement for single geometries.

This module is pure Python + Shapely + pyproj: no Django, no files, no
database. That keeps the most important logic small and easy to test.

Strategy (geometry must already be in WGS84 / EPSG:4326, lon/lat order):

* Polygon area  -> projected into a Lambert Azimuthal Equal-Area (LAEA)
  projection centred on the feature itself. Equal-area projections preserve
  area by construction, and centring on the feature keeps shape distortion
  tiny, so the planar area is effectively exact.
* Line length and polygon perimeter -> projected into a Transverse Mercator
  projection centred on the feature (scale factor 1 at its centre). This is
  the same family as UTM, but a standard UTM zone is 6 degrees wide and its
  scale error grows to about 0.1 % at the zone edges; centring the
  projection on the feature removes that error.
* Cross-check   -> the same quantities computed geodesically on the WGS84
  ellipsoid with pyproj.Geod. These are never used as the primary value
  (the brief asks for a projected CRS) but are returned so anyone can verify
  the projected numbers, and large disagreements are flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
NO_MEASUREMENT_TYPES = {"Point", "MultiPoint"}

# Projected and geodesic values should agree closely for normal survey-sized
# features. A bigger gap usually means a huge or strange geometry.
CROSS_CHECK_TOLERANCE = 0.005  # 0.5 %


@dataclass
class Measurement:
    status: str  # MEASURED | NOT_APPLICABLE | UNSUPPORTED | ERROR
    area_m2: float | None = None
    length_m: float | None = None
    perimeter_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    projected_crs: str = ""
    notes: list[str] = field(default_factory=list)


def local_tm_crs_for(lon: float, lat: float) -> CRS:
    """A Transverse Mercator projection centred on the given point (k=1)."""
    return CRS.from_proj4(
        f"+proj=tmerc +lat_0={lat:.6f} +lon_0={lon:.6f} +k=1 +datum=WGS84 +units=m +no_defs"
    )


def laea_crs_for(lon: float, lat: float) -> CRS:
    """An equal-area projection centred on the given point."""
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat:.6f} +lon_0={lon:.6f} +datum=WGS84 +units=m +no_defs"
    )


@lru_cache(maxsize=256)
def _transformer(target: CRS) -> Transformer:
    return Transformer.from_crs(WGS84, target, always_xy=True)


def project(geom: BaseGeometry, target: CRS) -> BaseGeometry:
    """Reproject a WGS84 geometry to the target CRS."""
    transformer = _transformer(target)

    def _apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, _apply)


def _center(geom: BaseGeometry) -> tuple[float, float]:
    point = geom.centroid if not geom.centroid.is_empty else geom.representative_point()
    return point.x, point.y


def _flag_disagreement(result: Measurement, projected: float, geodesic: float, what: str) -> None:
    if geodesic > 0 and abs(projected - geodesic) / geodesic > CROSS_CHECK_TOLERANCE:
        result.notes.append(
            f"Projected {what} differs from the geodesic value by more than "
            f"{CROSS_CHECK_TOLERANCE:.1%}; the feature may be very large."
        )


def measure(geom: BaseGeometry | None) -> Measurement:
    """Measure one WGS84 geometry. Never raises for bad input."""
    if geom is None or geom.is_empty:
        return Measurement(status="ERROR", notes=["Feature has no geometry."])

    geom = shapely.force_2d(geom)
    geom_type = geom.geom_type

    if geom_type in NO_MEASUREMENT_TYPES:
        return Measurement(status="NOT_APPLICABLE")
    if geom_type not in AREA_TYPES | LENGTH_TYPES:
        return Measurement(
            status="UNSUPPORTED",
            notes=[f"Measurement is not supported for {geom_type} geometries."],
        )

    try:
        if geom_type in AREA_TYPES:
            return _measure_area(geom)
        return _measure_length(geom)
    except Exception as exc:  # keep one bad feature from failing the whole file
        return Measurement(status="ERROR", notes=[f"Measurement failed: {exc}"])


def _measure_area(geom: BaseGeometry) -> Measurement:
    notes = []
    if not geom.is_valid:
        reason = shapely.is_valid_reason(geom)
        geom = shapely.make_valid(geom)
        # make_valid can return a collection; keep only the polygon parts.
        polygons = shapely.get_parts(geom)
        polygons = [p for p in polygons if p.geom_type in AREA_TYPES]
        geom = shapely.union_all(polygons) if polygons else shapely.Polygon()
        notes.append(f"Invalid polygon was repaired before measuring ({reason}).")
        if geom.is_empty:
            return Measurement(status="ERROR", notes=notes + ["Nothing left after repair."])

    lon, lat = _center(geom)
    area = project(geom, laea_crs_for(lon, lat)).area
    perimeter = project(geom.boundary, local_tm_crs_for(lon, lat)).length

    # pyproj's geodesic area is signed by ring direction; orient first
    # (counter-clockwise exteriors, clockwise holes) and take the magnitude.
    oriented = shapely.orient_polygons(geom, exterior_cw=False)
    geodesic_area, _ = GEOD.geometry_area_perimeter(oriented)
    geodesic_area = abs(geodesic_area)

    result = Measurement(
        status="MEASURED",
        area_m2=area,
        perimeter_m=perimeter,
        geodesic_area_m2=geodesic_area,
        projected_crs=f"Local equal-area (LAEA) centred on {lat:.5f}, {lon:.5f}",
        notes=notes,
    )
    _flag_disagreement(result, area, geodesic_area, "area")
    return result


def _measure_length(geom: BaseGeometry) -> Measurement:
    lon, lat = _center(geom)
    length = project(geom, local_tm_crs_for(lon, lat)).length
    geodesic_length = GEOD.geometry_length(geom)

    result = Measurement(
        status="MEASURED",
        length_m=length,
        geodesic_length_m=geodesic_length,
        projected_crs=f"Local Transverse Mercator centred on {lat:.5f}, {lon:.5f}",
    )
    _flag_disagreement(result, length, geodesic_length, "length")
    return result