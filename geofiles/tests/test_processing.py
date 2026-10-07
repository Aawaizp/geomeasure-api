"""End-to-end: upload -> worker processes -> measurements endpoint."""
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from geofiles.models import GeoFile
from geofiles.services import claim_next_job

TEMP_MEDIA = tempfile.mkdtemp()
SAMPLE_KML = Path(__file__).resolve().parents[2] / "samples" / "bengaluru_site.kml"


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ProcessingFlowTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def upload(self, name: str, content: bytes) -> str:
        response = self.client.post(
            reverse("geofile-list"),
            {"file": SimpleUploadedFile(name, content)},
            format="multipart",
        )
        return response.data["id"]

    def measurements(self, file_id: str, query: str = ""):
        return self.client.get(reverse("geofile-measurements", args=[file_id]) + query)

    def test_measurements_are_pending_until_processed(self):
        file_id = self.upload("site.kml", SAMPLE_KML.read_bytes())

        response = self.measurements(file_id)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "QUEUED")

    def test_full_flow_measures_every_geometry_type(self):
        file_id = self.upload("site.kml", SAMPLE_KML.read_bytes())

        call_command("process_files", "--once", stdout=StringIO())

        detail = self.client.get(reverse("geofile-detail", args=[file_id])).data
        self.assertEqual(detail["status"], "COMPLETED")
        self.assertEqual(detail["feature_count"], 3)
        self.assertEqual(detail["crs"], "EPSG:4326")

        data = self.measurements(file_id).data
        polygon, line, point = data["features"]

        self.assertEqual(polygon["measurement_status"], "MEASURED")
        self.assertAlmostEqual(polygon["measurements"]["area_m2"], 300_075, delta=5)
        self.assertLess(polygon["measurements"]["cross_check"]["difference_percent"], 0.01)
        self.assertEqual(polygon["geometry"]["type"], "Polygon")

        # Two segments: 243.57 m + 387.38 m (checked independently with pyproj.Geod.inv).
        self.assertAlmostEqual(line["measurements"]["length_m"], 630.95, delta=0.1)
        self.assertEqual(point["measurement_status"], "NOT_APPLICABLE")
        self.assertIsNone(point["measurements"])

        self.assertEqual(data["summary"]["by_geometry_type"]["Point"]["count"], 1)

    def test_geometry_can_be_left_out(self):
        file_id = self.upload("site.kml", SAMPLE_KML.read_bytes())
        call_command("process_files", "--once", stdout=StringIO())

        data = self.measurements(file_id, "?geometry=false").data

        self.assertNotIn("geometry", data["features"][0])

    def test_bad_file_fails_with_message_instead_of_crashing(self):
        file_id = self.upload("broken.kml", b"<?xml version='1.0'?><kml><Document><Placemark>")

        call_command("process_files", "--once", stdout=StringIO())

        response = self.measurements(file_id)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data["status"], "FAILED")
        self.assertTrue(response.data["detail"])

    def test_a_job_is_claimed_only_once(self):
        self.upload("site.kml", SAMPLE_KML.read_bytes())

        first = claim_next_job()
        second = claim_next_job()

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(GeoFile.objects.get().status, "PROCESSING")