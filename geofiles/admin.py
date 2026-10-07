from django.contrib import admin

from geofiles.models import Feature, GeoFile


@admin.register(GeoFile)
class GeoFileAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "file_type", "status", "feature_count", "created_at")
    list_filter = ("status", "file_type")
    search_fields = ("original_filename", "sha256")


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("geofile", "index", "geometry_type", "measurement_status", "area_m2", "length_m")
    list_filter = ("geometry_type", "measurement_status")