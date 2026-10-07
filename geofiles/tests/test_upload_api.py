import io
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from geofiles.models import GeoFile

TEMP_MEDIA = tempfile.mkdtemp()

KML_BYTES = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Gate</name><Point><coordinates>77.59,12.97</coordinates></Point></Placemark>
</Document></kml>"""


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class UploadApiTests(APITestCase):
    url = reverse("geofile-list")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def upload(self, name: str, content: bytes):
        return self.client.post(
            self.url, {"file": SimpleUploadedFile(name, content)}, format="multipart"
        )

    def test_kml_upload_is_accepted_and_queued(self):
        response = self.upload("site.kml", KML_BYTES)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["filename"], "site.kml")
        self.assertEqual(response.data["file_type"], "KML")
        self.assertEqual(response.data["status"], "QUEUED")
        self.assertEqual(GeoFile.objects.count(), 1)

    def test_zip_upload_is_detected_as_shapefile(self):
        response = self.upload("parcels.zip", make_zip({"parcels.shp": b"stub"}))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["file_type"], "SHAPEFILE")

    def test_unsupported_extension_is_rejected(self):
        response = self.upload("notes.txt", b"hello")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Unsupported file extension", str(response.data["file"]))
        self.assertEqual(GeoFile.objects.count(), 0)

    def test_fake_zip_is_rejected(self):
        response = self.upload("parcels.zip", b"this is not a zip")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not a valid ZIP", str(response.data["file"]))

    def test_fake_kml_is_rejected(self):
        response = self.upload("site.kml", b"\x89PNG not xml")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_file_is_rejected(self):
        response = self.upload("site.kml", b"")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(MAX_UPLOAD_SIZE_BYTES=100)
    def test_oversized_file_is_rejected(self):
        response = self.upload("site.kml", KML_BYTES)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("too large", str(response.data["file"]))

    def test_missing_file_field_is_rejected(self):
        response = self.client.post(self.url, {}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class DetailApiTests(APITestCase):
    def test_returns_file_information(self):
        upload = self.client.post(
            reverse("geofile-list"),
            {"file": SimpleUploadedFile("site.kml", KML_BYTES)},
            format="multipart",
        )
        url = reverse("geofile-detail", args=[upload.data["id"]])

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], upload.data["id"])
        self.assertEqual(response.data["status"], "QUEUED")

    def test_unknown_id_returns_404(self):
        url = reverse("geofile-detail", args=["00000000-0000-0000-0000-000000000000"])

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)