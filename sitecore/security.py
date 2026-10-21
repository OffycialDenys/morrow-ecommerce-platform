class SecurityHeadersMiddleware:
    """Add a restrictive public-page CSP and baseline browser protections."""

    public_policy = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' https: data:; font-src 'self' data:; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    admin_policy = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            self.admin_policy if request.path.startswith("/admin/") else self.public_policy,
        )
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response
