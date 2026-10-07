"""Accuracy tests for the measurement core.

Test shapes are built in a projection centred on the shape, so their true
size is known exactly, then converted to WGS84 like a real uploaded file.
"""
import shapely
from django.test import SimpleTestCase
from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)

from geofiles.geo.measure import WGS84, laea_crs_for, measure, project

BENGALURU = (77.5946, 12.9716)


def square(lon: float, lat: float, side_m: float) -> Polygon:
    """A square of known size (side_m x side_m metres) centred on lon/lat."""
    to_wgs84 = Transformer.from_crs(laea_crs_for(lon, lat), WGS84, always_xy=True)
    h = side_m / 2
    corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
    return Polygon([to_wgs84.transform(x, y) for x, y in corners])


class PolygonAreaTests(SimpleTestCase):
    def test_one_square_kilometre_in_bengaluru(self):
        result = measure(square(*BENGALURU, 1000))

        self.assertEqual(result.status, "MEASURED")
        self.assertAlmostEqual(result.area_m2, 1_000_000, delta=1)
        self.assertAlmostEqual(result.perimeter_m, 4000, delta=4)

    def test_accurate_at_high_latitude(self):
        # Plain lat/lon or Web Mercator maths fails badly this far north.
        result = measure(square(10.75, 59.91, 1000))  # Oslo

        self.assertAlmostEqual(result.area_m2, 1_000_000, delta=1)

    def test_accurate_in_southern_hemisphere(self):
        result = measure(square(151.21, -33.87, 500))  # Sydney

        self.assertAlmostEqual(result.area_m2, 250_000, delta=1)

    def test_projected_area_agrees_with_geodesic_cross_check(self):
        result = measure(square(*BENGALURU, 2000))

        self.assertAlmostEqual(result.area_m2, result.geodesic_area_m2, delta=1)
        self.assertEqual(result.notes, [])

    def test_hole_is_subtracted_even_with_same_ring_direction(self):
        outer = square(*BENGALURU, 1000)
        hole = square(*BENGALURU, 500)
        # Same winding direction for both rings, as some files contain.
        polygon = Polygon(outer.exterior.coords, [hole.exterior.coords])

        result = measure(polygon)

        self.assertAlmostEqual(result.area_m2, 750_000, delta=1)
        self.assertAlmostEqual(result.geodesic_area_m2, 750_000, delta=1)

    def test_multipolygon_parts_are_summed(self):
        parts = MultiPolygon([square(*BENGALURU, 100), square(80.27, 13.08, 100)])

        result = measure(parts)

        self.assertAlmostEqual(result.area_m2, 20_000, delta=0.1)

    def test_self_intersecting_polygon_is_repaired_not_zero(self):
        centre = project(Point(*BENGALURU), laea_crs_for(*BENGALURU))
        to_wgs84 = Transformer.from_crs(laea_crs_for(*BENGALURU), WGS84, always_xy=True)
        x, y = centre.x, centre.y
        bowtie = Polygon(
            [to_wgs84.transform(*p) for p in [(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100)]]
        )
        self.assertFalse(bowtie.is_valid)

        result = measure(bowtie)

        self.assertEqual(result.status, "MEASURED")
        self.assertAlmostEqual(result.area_m2, 5_000, delta=1)  # two triangles of 2,500 m2 each
        self.assertIn("repaired", result.notes[0])


class LineLengthTests(SimpleTestCase):
    def test_line_length_matches_geodesic(self):
        line = LineString([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98)])

        result = measure(line)

        self.assertEqual(result.status, "MEASURED")
        self.assertAlmostEqual(result.length_m, result.geodesic_length_m, delta=0.01)
        self.assertAlmostEqual(result.length_m, 2191, delta=2)

    def test_altitude_is_ignored(self):
        flat = measure(LineString([(77.59, 12.97), (77.60, 12.97)]))
        with_z = measure(LineString([(77.59, 12.97, 900), (77.60, 12.97, 950)]))

        self.assertAlmostEqual(flat.length_m, with_z.length_m, places=6)


class GracefulHandlingTests(SimpleTestCase):
    def test_point_needs_no_measurement(self):
        result = measure(Point(*BENGALURU))

        self.assertEqual(result.status, "NOT_APPLICABLE")
        self.assertIsNone(result.area_m2)

    def test_geometry_collection_is_unsupported_not_an_error(self):
        result = measure(GeometryCollection([Point(*BENGALURU)]))

        self.assertEqual(result.status, "UNSUPPORTED")
        self.assertIn("GeometryCollection", result.notes[0])

    def test_missing_geometry(self):
        self.assertEqual(measure(None).status, "ERROR")
        self.assertEqual(measure(shapely.Polygon()).status, "ERROR")