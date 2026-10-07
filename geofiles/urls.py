from django.urls import path

from geofiles.views import GeoFileDetailView, GeoFileListCreateView

urlpatterns = [
    path("files/", GeoFileListCreateView.as_view(), name="geofile-list"),
    path("files/<uuid:pk>/", GeoFileDetailView.as_view(), name="geofile-detail"),
]