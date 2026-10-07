from rest_framework import generics, status
from rest_framework.response import Response

from geofiles.models import GeoFile
from geofiles.serializers import GeoFileSerializer, GeoFileUploadSerializer


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