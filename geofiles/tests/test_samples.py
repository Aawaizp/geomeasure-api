"""The sample files in samples/ are real inputs: keep them working."""
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from geofiles.geo.measure import measure
from geofiles.geo.readers import read_kml, read_zipped_shapefile

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


class MineSiteSampleTests(SimpleTestCase):
    def setUp(self):
        self.features = read_kml(SAMPLES / "mine_site.kml").features

    def by_name(self, name):
        return self.features[self.features.properties.map(lambda p: p.get("Name")) == name].iloc[0]

    def test_every_folder_is_read(self):
        self.assertEqual(len(self.features), 10)
        self.assertEqual(
            sorted(set(self.features.layer)),
            ["Haul roads", "Lease and pit", "Stockpiles and dumps", "Survey control points"],
        )

    def test_unmined_island_is_subtracted_from_pit_area(self):
        pit = self.by_name("Main pit")
        outer_only = measure(pit.geometry.__class__(pit.geometry.exterior)).area_m2

        pit_area = measure(pit.geometry).area_m2

        self.assertEqual(len(pit.geometry.interiors), 1)
        self.assertLess(pit_area, outer_only)

    def test_kml_extended_data_becomes_properties(self):
        road = self.by_name("Haul road: pit to crusher")

        self.assertEqual(road.properties["width_m"], "25")
        self.assertEqual(road.properties["surface"], "Gravel")

    def test_control_point_altitude_is_dropped(self):
        gcp = self.by_name("GCP-01")

        self.assertFalse(gcp.geometry.has_z)
        self.assertEqual(measure(gcp.geometry).status, "NOT_APPLICABLE")


class VillageParcelsSampleTests(SimpleTestCase):
    def test_projected_shapefile_is_measured_on_the_ground(self):
        result = read_zipped_shapefile(SAMPLES / "village_parcels_utm43n.zip", Path(tempfile.mkdtemp()))

        self.assertEqual(result.source_crs, "EPSG:32643")
        self.assertEqual(len(result.features), 5)
        first = result.features.iloc[0]
        self.assertEqual(first.properties["parcel_id"], "KA-TMK-0101")
        # 768.37 m2 in UTM grid units, 768.0 m2 on the ground.
        self.assertAlmostEqual(measure(first.geometry).area_m2, 768.0, delta=0.5)
