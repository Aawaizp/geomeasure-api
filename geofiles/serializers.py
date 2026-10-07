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
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields