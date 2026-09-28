"""Internal readiness check for the application container."""

import os
import tempfile

from django.conf import settings
from django.db import connection
from django.http import JsonResponse


def health_view(request):
    if request.method != "GET":
        result = JsonResponse({"ready": False}, status=405)
    else:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                healthy_database = cursor.fetchone() == (1,)
            # An actual fsync catches read-only/full mounts that os.access misses.
            with tempfile.TemporaryFile(dir=settings.MEDIA_ROOT) as probe:
                probe.write(b"1")
                probe.flush()
                os.fsync(probe.fileno())
            healthy_storage = True
            result = JsonResponse({"ready": healthy_database and healthy_storage},
                                  status=200 if healthy_database and healthy_storage else 503)
        except Exception:
            result = JsonResponse({"ready": False}, status=503)
    result["Cache-Control"] = "no-store"
    return result
