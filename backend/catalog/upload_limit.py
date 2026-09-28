"""Stop oversized asset streams while Django parses multipart requests."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class AssetUploadLimitHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.PRODUCT_VIDEO_MAX_BYTES:
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None


class AssetUploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path == "/api/v1/admin/assets":
            request.upload_handlers.insert(0, AssetUploadLimitHandler(request))
        return self.get_response(request)
