from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    """Report whether the API and its PostGIS database are reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT postgis_lib_version()")
            postgis = cursor.fetchone()[0]
        return Response({"status": "ok", "database": "ok", "postgis": postgis})
    except Exception as exc:  # report, don't crash, when the DB is down
        return Response({"status": "degraded", "database": str(exc)}, status=503)