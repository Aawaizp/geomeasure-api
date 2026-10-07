from collections import defaultdict

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from geofiles.models import GeoFile
from geofiles.serializers import (
    FeatureMeasurementSerializer,
    GeoFileSerializer,
    GeoFileUploadSerializer,
)


class GeoFileListCreateView(generics.ListAPIView):
    """GET lists uploaded files; POST uploads a new one.

    POST returns 202 Accepted: the file is stored and queued, and processing
    happens outside the request so large files don't block the client.
    """

    queryset = GeoFile.objects.all()
    serializer_class = GeoFileSerializer

    def post(self, request, *args, **kwargs):
        upload = GeoFileUploadSerializer(data=request.data, context={})
        upload.is_valid(raise_exception=True)
        geofile = upload.save()
        return Response(GeoFileSerializer(geofile).data, status=status.HTTP_202_ACCEPTED)


class GeoFileDetailView(generics.RetrieveAPIView):
    """GET information about one uploaded file."""

    queryset = GeoFile.objects.all()
    serializer_class = GeoFileSerializer


class GeoFileMeasurementsView(APIView):
    """GET measurements for every feature in a processed file.

    Query parameters:
      geometry=false     leave out geometries (much smaller response)
      status=MEASURED    only features with this measurement_status
    """

    def get(self, request, pk):
        geofile = get_object_or_404(GeoFile, pk=pk)

        if geofile.status in (GeoFile.Status.QUEUED, GeoFile.Status.PROCESSING):
            return Response(
                {"id": geofile.id, "status": geofile.status, "detail": "Measurements are not ready yet."},
                status=status.HTTP_202_ACCEPTED,
            )
        if geofile.status == GeoFile.Status.FAILED:
            return Response(
                {"id": geofile.id, "status": geofile.status, "detail": geofile.error_message},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        features = geofile.features.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            features = features.filter(measurement_status=status_filter.upper())
        include_geometry = request.query_params.get("geometry", "true").lower() != "false"

        return Response(
            {
                "id": geofile.id,
                "filename": geofile.original_filename,
                "status": geofile.status,
                "source_crs": geofile.crs,
                "warnings": geofile.warnings,
                "summary": _summary(geofile),
                "features": FeatureMeasurementSerializer(
                    features, many=True, context={"include_geometry": include_geometry}
                ).data,
            }
        )


def _summary(geofile: GeoFile) -> dict:
    """Totals per geometry type and per measurement status, computed in SQL."""
    rows = geofile.features.values("geometry_type", "measurement_status").annotate(
        count=Count("id"), area=Sum("area_m2"), length=Sum("length_m")
    )
    by_type = defaultdict(lambda: {"count": 0})
    by_status = defaultdict(int)
    total_area = total_length = 0.0
    for row in rows:
        entry = by_type[row["geometry_type"]]
        entry["count"] += row["count"]
        by_status[row["measurement_status"]] += row["count"]
        if row["area"] is not None:
            entry["total_area_m2"] = round(entry.get("total_area_m2", 0) + row["area"], 3)
            total_area += row["area"]
        if row["length"] is not None:
            entry["total_length_m"] = round(entry.get("total_length_m", 0) + row["length"], 3)
            total_length += row["length"]

    return {
        "feature_count": geofile.feature_count,
        "by_geometry_type": dict(by_type),
        "by_measurement_status": dict(by_status),
        "total_area_m2": round(total_area, 3),
        "total_area_hectares": round(total_area / 10_000, 6),
        "total_length_m": round(total_length, 3),
    }