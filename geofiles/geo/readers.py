"""Read features from a KML file or a zipped Shapefile.

Output is always the same shape no matter the input format: a GeoDataFrame
in WGS84 (EPSG:4326) with columns ``layer``, ``properties`` and ``geometry``,
plus the original CRS and any warnings. Measurement code never needs to know
which format a feature came from.
"""
from __future__ import annotations

import json
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS

WGS84 = "EPSG:4326"

# Limits that protect the server from malicious or broken ZIP files.
MAX_ZIP_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

SHAPEFILE_REQUIRED = (".shp", ".shx", ".dbf")

# KML styling fields that GDAL exposes as columns but that aren't attributes.
KML_DISPLAY_COLUMNS = ("tessellate", "extrude", "visibility", "drawOrder", "altitudeMode", "icon")


class FileProcessingError(Exception):
    """The file can't be processed. The message is safe to show to users."""


@dataclass
class ReadResult:
    features: gpd.GeoDataFrame
    source_crs: str
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def read_kml(path: Path) -> ReadResult:
    """Read every layer (KML folder) of a KML file."""
    try:
        layers = [name for name, _ in pyogrio.list_layers(path)]
    except Exception as exc:
        raise FileProcessingError(f"Could not read KML: {exc}") from exc

    frames = [_read_layer(path, layer) for layer in layers]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise FileProcessingError("The KML file contains no features.")

    # KML coordinates are always WGS84 longitude/latitude by specification.
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop(columns=[c for c in KML_DISPLAY_COLUMNS if c in combined.columns])
    gdf = gpd.GeoDataFrame(combined, geometry="geometry", crs=WGS84)
    return ReadResult(features=_normalise(gdf), source_crs=WGS84)


def read_zipped_shapefile(zip_path: Path, workdir: Path) -> ReadResult:
    """Safely extract a ZIP and read every Shapefile inside it."""
    extracted = safe_extract_zip(zip_path, workdir)
    shapefiles = find_shapefiles(extracted)

    frames, crs_values, warnings = [], set(), []
    for shp in shapefiles:
        gdf = _read_layer(shp, layer=None)
        gdf["layer"] = shp.stem
        if gdf.empty:
            continue
        gdf, crs_label, crs_warning = _ensure_wgs84(gdf, shp.name)
        crs_values.add(crs_label)
        if crs_warning:
            warnings.append(crs_warning)
        frames.append(gdf)

    if not frames:
        raise FileProcessingError("The Shapefile contains no features.")

    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=WGS84)
    source_crs = crs_values.pop() if len(crs_values) == 1 else "MIXED"
    return ReadResult(features=_normalise(combined), source_crs=source_crs, warnings=warnings)


# --------------------------------------------------------------------------- #
# ZIP handling
# --------------------------------------------------------------------------- #
def safe_extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract a ZIP after checking it for path traversal, symlinks and ZIP bombs."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileProcessingError("The file is not a valid ZIP archive.") from exc

    with archive:
        members = [m for m in archive.infolist() if not _is_junk(m.filename)]
        if len(members) > MAX_ZIP_ENTRIES:
            raise FileProcessingError(f"The ZIP has too many files (limit {MAX_ZIP_ENTRIES}).")

        total = sum(m.file_size for m in members)
        compressed = sum(m.compress_size for m in members) or 1
        if total > MAX_UNCOMPRESSED_BYTES or total / compressed > MAX_COMPRESSION_RATIO:
            raise FileProcessingError("The ZIP expands to an unsafe size and was rejected.")

        dest = dest.resolve()
        for member in members:
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts:
                raise FileProcessingError(f"Unsafe path in ZIP: {member.filename}")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise FileProcessingError(f"Symbolic links are not allowed in ZIP: {member.filename}")
            if member.flag_bits & 0x1:
                raise FileProcessingError("Encrypted ZIP files are not supported.")
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):
                raise FileProcessingError(f"Unsafe path in ZIP: {member.filename}")
            archive.extract(member, dest)
    return dest


def find_shapefiles(folder: Path) -> list[Path]:
    """Find .shp files (at any depth) and check each has its required parts."""
    shapefiles = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".shp")
    if not shapefiles:
        raise FileProcessingError("No .shp file was found inside the ZIP.")

    for shp in shapefiles:
        siblings = {p.suffix.lower() for p in shp.parent.glob(f"{shp.stem}.*")}
        missing = [ext for ext in SHAPEFILE_REQUIRED if ext not in siblings]
        if missing:
            raise FileProcessingError(
                f"Shapefile '{shp.name}' is missing required parts: {', '.join(missing)}."
            )
    return shapefiles


def _is_junk(filename: str) -> bool:
    parts = PurePosixPath(filename).parts
    return filename.endswith("/") or "__MACOSX" in parts or parts[-1].startswith("._")


# --------------------------------------------------------------------------- #
# CRS and normalisation
# --------------------------------------------------------------------------- #
def _read_layer(path: Path, layer: str | None) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(path, layer=layer)
    except Exception as exc:
        raise FileProcessingError(f"Could not read '{path.name}': {exc}") from exc
    gdf["layer"] = layer or ""
    return gdf


def describe_crs(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def _ensure_wgs84(gdf: gpd.GeoDataFrame, source_name: str) -> tuple[gpd.GeoDataFrame, str, str]:
    """Return the data in WGS84, its original CRS label, and an optional warning."""
    if gdf.crs is None:
        minx, miny, maxx, maxy = gdf.total_bounds
        if -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90:
            warning = (
                f"'{source_name}' has no .prj file; coordinates look like longitude/latitude, "
                "so EPSG:4326 was assumed."
            )
            return gdf.set_crs(WGS84), "UNKNOWN (assumed EPSG:4326)", warning
        raise FileProcessingError(
            f"'{source_name}' has no .prj file and its coordinates are not longitude/latitude, "
            "so its coordinate system can't be determined. Include the .prj file."
        )
    return gdf.to_crs(WGS84), describe_crs(gdf.crs), ""


def _normalise(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reduce to layer / properties / geometry, with 2D geometries."""
    attribute_columns = [c for c in gdf.columns if c not in ("geometry", "layer")]
    records = json.loads(
        pd.DataFrame(gdf[attribute_columns]).to_json(orient="records", date_format="iso")
    )
    properties = [{k: v for k, v in row.items() if v not in (None, "")} for row in records]

    geometries = gdf.geometry.array
    geometries = [shapely.force_2d(g) if g is not None else None for g in geometries]

    return gpd.GeoDataFrame(
        {"layer": gdf["layer"].fillna("").tolist(), "properties": properties},
        geometry=gpd.GeoSeries(geometries, crs=WGS84),
    )