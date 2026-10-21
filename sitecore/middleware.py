"""Request-scoped operational context."""

from uuid import uuid4


class RequestCorrelationMiddleware:
    """Assign a server-generated identifier without trusting client-provided IDs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid4()
        response = self.get_response(request)
        response.setdefault("X-Request-ID", str(request.request_id))
        return response
