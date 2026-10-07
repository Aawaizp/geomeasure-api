"""Processing jobs: claim a queued file, read it, measure it, save results."""
from __future__ import annotations

import logging
import tempfile
from datetime import timedelta
from pathlib import Path

import shapely
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction
from django.utils import timezone

from geofiles.geo.measure import measure
from geofiles.geo.readers import FileProcessingError, ReadResult, read_kml, read_zipped_shapefile
from geofiles.models import Feature, GeoFile

logger = logging.getLogger(__name__)

STALE_JOB_TIMEOUT = timedelta(minutes=15)


def claim_next_job() -> GeoFile | None:
    """Atomically move the oldest QUEUED file to PROCESSING and return it.

    SELECT ... FOR UPDATE SKIP LOCKED lets several workers run at once
    without ever picking up the same file twice.
    """
    with transaction.atomic():
        geofile = (
            GeoFile.objects.select_for_update(skip_locked=True)
            .filter(status=GeoFile.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if geofile is None:
            return None
        geofile.status = GeoFile.Status.PROCESSING
        geofile.started_at = timezone.now()
        geofile.save(update_fields=["status", "started_at"])
        return geofile


def requeue_stale_jobs() -> int:
    """Put back files stuck in PROCESSING (for example after a worker crash)."""
    cutoff = timezone.now() - STALE_JOB_TIMEOUT
    return GeoFile.objects.filter(
        status=GeoFile.Status.PROCESSING, started_at__lt=cutoff
    ).update(status=GeoFile.Status.QUEUED, started_at=None)


def process_geofile(geofile: GeoFile) -> GeoFile:
    """Read and measure a file, storing one Feature row per feature.

    Never raises: any problem ends with status FAILED and a readable message.
    """
    try:
        with tempfile.TemporaryDirectory() as workdir:
            result = _read(geofile, Path(workdir))
        features = _build_features(geofile, result)

        with transaction.atomic():
            geofile.features.all().delete()  # makes reprocessing safe
            Feature.objects.bulk_create(features, batch_size=1000)
            geofile.status = GeoFile.Status.COMPLETED
            geofile.crs = result.source_crs
            geofile.feature_count = len(features)
            geofile.warnings = result.warnings
            geofile.error_message = ""
            geofile.completed_at = timezone.now()
            geofile.save()
        logger.info("Processed %s: %d features", geofile.id, len(features))

    except FileProcessingError as exc:
        _mark_failed(geofile, str(exc))
    except Exception:
        logger.exception("Unexpected error processing %s", geofile.id)
        _mark_failed(geofile, "Unexpected error while processing the file.")
    return geofile


def _read(geofile: GeoFile, workdir: Path) -> ReadResult:
    path = Path(geofile.file.path)
    if geofile.file_type == GeoFile.FileType.KML:
        return read_kml(path)
    return read_zipped_shapefile(path, workdir)


def _build_features(geofile: GeoFile, result: ReadResult) -> list[Feature]:
    features = []
    for index, row in enumerate(result.features.itertuples(index=False)):
        geom = row.geometry
        m = measure(geom)
        features.append(
            Feature(
                geofile=geofile,
                index=index,
                layer=row.layer,
                geometry_type=geom.geom_type if geom is not None else "None",
                geometry=_to_geos(geom),
                properties=row.properties,
                measurement_status=m.status,
                area_m2=m.area_m2,
                length_m=m.length_m,
                perimeter_m=m.perimeter_m,
                geodesic_area_m2=m.geodesic_area_m2,
                geodesic_length_m=m.geodesic_length_m,
                projected_crs=m.projected_crs,
                notes=" ".join(m.notes),
            )
        )
    return features


def _to_geos(geom) -> GEOSGeometry | None:
    if geom is None or geom.is_empty:
        return None
    return GEOSGeometry(memoryview(shapely.to_wkb(geom)), srid=4326)


def _mark_failed(geofile: GeoFile, message: str) -> None:
    geofile.status = GeoFile.Status.FAILED
    geofile.error_message = message
    geofile.completed_at = timezone.now()
    geofile.save(update_fields=["status", "error_message", "completed_at"])
    logger.warning("Failed to process %s: %s", geofile.id, message)