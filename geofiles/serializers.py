import json

from rest_framework import serializers

from geofiles import validators
from geofiles.models import GeoFile


class GeoFileUploadSerializer(serializers.Serializer):
    """Validates an upload and creates a queued GeoFile."""

    file = serializers.FileField()

    def validate_file(self, upload):
        file_type = validators.detect_file_type(upload)
        validators.validate_size(upload)
        validators.validate_content_signature(upload, file_type)
        self.context["file_type"] = file_type
        return upload

    def create(self, validated_data):
        upload = validated_data["file"]
        return GeoFile.objects.create(
            original_filename=upload.name,
            file=upload,
            file_type=self.context["file_type"],
            size_bytes=upload.size,
            sha256=validators.sha256_of(upload),
        )


class GeoFileSerializer(serializers.ModelSerializer):
    """Public representation of an uploaded file and its processing status."""

    filename = serializers.CharField(source="original_filename")

    class Meta:
        model = GeoFile
        fields = [
            "id",
            "filename",
            "file_type",
            "size_bytes",
            "sha256",
            "status",
            "crs",
            "feature_count",
            "error_message",
            "warnings",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields


SQ_M_PER_HECTARE = 10_000
SQ_M_PER_ACRE = 4_046.8564224


def _rounded(value, digits=3):
    return round(value, digits) if value is not None else None


class FeatureMeasurementSerializer(serializers.Serializer):
    """One feature with its geometry, attributes and measurements."""

    def to_representation(self, feature):
        data = {
            "index": feature.index,
            "layer": feature.layer,
            "geometry_type": feature.geometry_type,
            "crs": "EPSG:4326",
            "properties": feature.properties,
            "measurement_status": feature.measurement_status,
            "measurements": self._measurements(feature),
            "notes": feature.notes,
        }
        if self.context.get("include_geometry", True):
            data["geometry"] = json.loads(feature.geometry.geojson) if feature.geometry else None
        return data

    def _measurements(self, feature):
        if feature.area_m2 is not None:
            return {
                "area_m2": _rounded(feature.area_m2),
                "area_hectares": _rounded(feature.area_m2 / SQ_M_PER_HECTARE, 6),
                "area_acres": _rounded(feature.area_m2 / SQ_M_PER_ACRE, 6),
                "perimeter_m": _rounded(feature.perimeter_m),
                "method": feature.projected_crs,
                "cross_check": _cross_check(feature.area_m2, feature.geodesic_area_m2),
            }
        if feature.length_m is not None:
            return {
                "length_m": _rounded(feature.length_m),
                "length_km": _rounded(feature.length_m / 1000, 6),
                "method": feature.projected_crs,
                "cross_check": _cross_check(feature.length_m, feature.geodesic_length_m),
            }
        return None


def _cross_check(projected, geodesic):
    if geodesic is None:
        return None
    difference = abs(projected - geodesic) / geodesic * 100 if geodesic else 0.0
    return {
        "geodesic_value": _rounded(geodesic),
        "difference_percent": round(difference, 6),
    }