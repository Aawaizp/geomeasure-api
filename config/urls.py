from django.contrib import admin
from django.urls import include, path

from config.views import health
from geofiles.views import ViewerPage

urlpatterns = [
    path("", ViewerPage.as_view(), name="viewer"),
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/", include("geofiles.urls")),
]