import uuid

from django.contrib.gis.db import models


class GeoFile(models.Model):
    """An uploaded geospatial file and the state of its processing job."""

    class FileType(models.TextChoices):
        SHAPEFILE = "SHAPEFILE", "Zipped Shapefile"
        KML = "KML", "KML"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    file_type = models.CharField(max_length=16, choices=FileType.choices)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    crs = models.CharField(max_length=64, blank=True)
    feature_count = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    warnings = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status})"


class Feature(models.Model):
    """One feature read from a GeoFile, with its measurements.

    Geometry is stored in WGS84 (EPSG:4326) so PostGIS can index and query it;
    the file's original CRS is kept on the parent GeoFile.
    """

    class MeasurementStatus(models.TextChoices):
        MEASURED = "MEASURED", "Measured"
        NOT_APPLICABLE = "NOT_APPLICABLE", "No measurement for this geometry type"
        UNSUPPORTED = "UNSUPPORTED", "Geometry type not supported"
        ERROR = "ERROR", "Measurement failed"

    geofile = models.ForeignKey(GeoFile, on_delete=models.CASCADE, related_name="features")
    index = models.PositiveIntegerField()
    layer = models.CharField(max_length=255, blank=True)
    geometry_type = models.CharField(max_length=32)
    geometry = models.GeometryField(srid=4326, null=True, blank=True)
    properties = models.JSONField(default=dict, blank=True)

    measurement_status = models.CharField(max_length=16, choices=MeasurementStatus.choices)
    area_m2 = models.FloatField(null=True, blank=True)
    length_m = models.FloatField(null=True, blank=True)
    perimeter_m = models.FloatField(null=True, blank=True)
    geodesic_area_m2 = models.FloatField(null=True, blank=True)
    geodesic_length_m = models.FloatField(null=True, blank=True)
    projected_crs = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["geofile", "index"]
        constraints = [
            models.UniqueConstraint(fields=["geofile", "index"], name="unique_feature_index_per_file")
        ]

    def __str__(self) -> str:
        return f"Feature {self.index} ({self.geometry_type}) of {self.geofile_id}"