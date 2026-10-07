import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from django.test import SimpleTestCase
from shapely.geometry import Polygon

from geofiles.geo.measure import measure
from geofiles.geo.readers import FileProcessingError, read_kml, read_zipped_shapefile

SAMPLE_KML = Path(__file__).resolve().parents[2] / "samples" / "bengaluru_site.kml"

MULTI_FOLDER_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Folder><name>Boundaries</name>
    <Placemark><name>Pit</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      77.59,12.97 77.60,12.97 77.60,12.98 77.59,12.97
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder>
  <Folder><name>Roads</name>
    <Placemark><name>Haul road</name><LineString><coordinates>
      77.59,12.97 77.60,12.98
    </coordinates></LineString></Placemark>
  </Folder>
</Document></kml>"""


def utm_parcels(folder: Path) -> Path:
    """Write a Shapefile in UTM zone 43N (metres), like a real survey export."""
    gdf = gpd.GeoDataFrame(
        {"name": ["Plot A"], "owner": ["Survey dept"]},
        geometry=[
            Polygon([(600000, 1400000), (600100, 1400000), (600100, 1400100), (600000, 1400100)])
        ],
        crs="EPSG:32643",
    )
    path = folder / "parcels.shp"
    gdf.to_file(path)
    return path


def zip_files(entries: dict[str, bytes]) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    path = Path(tempfile.mkdtemp()) / "upload.zip"
    path.write_bytes(buffer.getvalue())
    return path


def shapefile_entries(prefix: str = "", skip: tuple[str, ...] = ()) -> dict[str, bytes]:
    shp = utm_parcels(Path(tempfile.mkdtemp()))
    return {
        f"{prefix}{p.name}": p.read_bytes()
        for p in shp.parent.glob("parcels.*")
        if p.suffix not in skip
    }


class KmlReaderTests(SimpleTestCase):
    def test_reads_all_geometry_types_from_sample(self):
        result = read_kml(SAMPLE_KML)

        types = list(result.features.geometry.geom_type)
        self.assertEqual(types, ["Polygon", "LineString", "Point"])
        self.assertEqual(result.source_crs, "EPSG:4326")
        self.assertEqual(result.features.properties[0], {"Name": "Site boundary"})

    def test_reads_every_folder_not_just_the_first(self):
        path = Path(tempfile.mkdtemp()) / "site.kml"
        path.write_bytes(MULTI_FOLDER_KML)

        result = read_kml(path)

        self.assertEqual(len(result.features), 2)
        self.assertEqual(sorted(result.features.layer), ["Boundaries", "Roads"])

    def test_broken_kml_gives_clear_error(self):
        path = Path(tempfile.mkdtemp()) / "broken.kml"
        path.write_bytes(b"<?xml version='1.0'?><kml><Document><Placemark>")

        with self.assertRaises(FileProcessingError):
            read_kml(path)


class ShapefileReaderTests(SimpleTestCase):
    def read(self, entries):
        return read_zipped_shapefile(zip_files(entries), Path(tempfile.mkdtemp()))

    def test_projected_shapefile_is_converted_and_measured_on_the_ground(self):
        result = self.read(shapefile_entries())

        self.assertEqual(result.source_crs, "EPSG:32643")
        # 100 m x 100 m in UTM grid units is slightly more on the ground,
        # because UTM shrinks distances 100 km from the central meridian.
        area = measure(result.features.geometry[0]).area_m2
        self.assertAlmostEqual(area, 10_005.5, delta=1)

    def test_shapefile_inside_a_subfolder_is_found(self):
        result = self.read(shapefile_entries(prefix="export/data/"))

        self.assertEqual(len(result.features), 1)
        self.assertEqual(result.features.properties[0]["name"], "Plot A")

    def test_mac_metadata_files_are_ignored(self):
        entries = shapefile_entries()
        entries["__MACOSX/._parcels.shp"] = b"junk"

        result = self.read(entries)

        self.assertEqual(len(result.features), 1)

    def test_missing_shx_is_reported_clearly(self):
        with self.assertRaisesRegex(FileProcessingError, r"missing required parts: \.shx"):
            self.read(shapefile_entries(skip=(".shx",)))

    def test_missing_prj_with_projected_coordinates_is_rejected(self):
        with self.assertRaisesRegex(FileProcessingError, "no .prj file"):
            self.read(shapefile_entries(skip=(".prj",)))

    def test_zip_without_shapefile_is_rejected(self):
        with self.assertRaisesRegex(FileProcessingError, "No .shp file"):
            self.read({"readme.txt": b"hello"})

    def test_path_traversal_is_blocked(self):
        entries = shapefile_entries()
        entries["../../evil.txt"] = b"pwned"

        with self.assertRaisesRegex(FileProcessingError, "Unsafe path"):
            self.read(entries)

    def test_zip_bomb_is_blocked(self):
        with self.assertRaisesRegex(FileProcessingError, "unsafe size"):
            self.read({"huge.shp": b"\0" * 20_000_000})