"""Checks run on an uploaded file before it is accepted.

These are cheap, structural checks done during the request. Reading the
geospatial content itself happens later, during processing.
"""
import hashlib
import zipfile
from pathlib import PurePosixPath

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from rest_framework.exceptions import ValidationError

from geofiles.models import GeoFile

EXTENSION_TO_TYPE = {
    ".zip": GeoFile.FileType.SHAPEFILE,
    ".kml": GeoFile.FileType.KML,
}


def detect_file_type(upload: UploadedFile) -> str:
    """Return the GeoFile.FileType for an upload, or raise ValidationError."""
    suffix = PurePosixPath(upload.name).suffix.lower()
    if suffix not in EXTENSION_TO_TYPE:
        allowed = ", ".join(sorted(EXTENSION_TO_TYPE))
        raise ValidationError(f"Unsupported file extension '{suffix or '(none)'}'. Allowed: {allowed}.")
    return EXTENSION_TO_TYPE[suffix]


def validate_size(upload: UploadedFile) -> None:
    if upload.size == 0:
        raise ValidationError("The uploaded file is empty.")
    if upload.size > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_mb = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(f"File is too large. The limit is {limit_mb} MB.")


def validate_content_signature(upload: UploadedFile, file_type: str) -> None:
    """Make sure the file content matches its extension (not just the name)."""
    upload.seek(0)
    if file_type == GeoFile.FileType.SHAPEFILE:
        if not zipfile.is_zipfile(upload):
            raise ValidationError("The .zip file is not a valid ZIP archive.")
    elif file_type == GeoFile.FileType.KML:
        head = upload.read(2048).lstrip(b"\xef\xbb\xbf").lstrip()
        if not (head.startswith(b"<?xml") or head.startswith(b"<kml")):
            raise ValidationError("The .kml file does not look like KML (XML) content.")
    upload.seek(0)


def sha256_of(upload: UploadedFile) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()