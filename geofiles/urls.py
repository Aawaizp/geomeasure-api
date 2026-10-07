from django.urls import path

from geofiles.views import GeoFileDetailView, GeoFileListCreateView, GeoFileMeasurementsView

urlpatterns = [
    path("files/", GeoFileListCreateView.as_view(), name="geofile-list"),
    path("files/<uuid:pk>/", GeoFileDetailView.as_view(), name="geofile-detail"),
    path(
        "files/<uuid:pk>/measurements/",
        GeoFileMeasurementsView.as_view(),
        name="geofile-measurements",
    ),
]