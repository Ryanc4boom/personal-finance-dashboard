"""Standard security response headers.

This is a JSON API, not a site that serves documents, so most of these are
defence in depth rather than load-bearing — the browser is not rendering these
responses as HTML. They are cheap, and the one case where they stop mattering
in theory and start mattering in practice is an error page or a content-type
confusion bug, which is exactly when nobody is thinking about headers.

The CSP is deliberately the *API's* policy and says "this response can load and
execute nothing". It is not the frontend's policy: Next.js serves its own
documents from its own origin and needs a policy that permits its scripts and
styles, which belongs in next.config.ts, not here. Applying an API-shaped CSP to
the frontend would blank the app.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

# `default-src 'none'` because a JSON response legitimately needs no resources at
# all. `frame-ancestors 'none'` is the modern X-Frame-Options and covers the
# clickjacking case for browsers that honour CSP.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    # Stops a browser second-guessing a declared content type. The attack it
    # blocks is a JSON response that happens to contain markup being sniffed as
    # HTML and executed — which is squarely a risk for an API returning
    # user-supplied transaction descriptions.
    "X-Content-Type-Options": "nosniff",
    # Redundant with frame-ancestors above, kept for browsers that support one
    # and not the other. Neither costs anything.
    "X-Frame-Options": "DENY",
    # Financial data has no business appearing in a Referer header sent to a
    # third party, and account ids travel in these paths.
    "Referrer-Policy": "no-referrer",
    # This app has no use for any of them, and an explicit denial is worth more
    # than silence if a dependency ever tries.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}

# HSTS is applied separately: it is meaningless over http and actively harmful
# to send from a localhost dev server, because the browser caches the pin per
# host and will then refuse plain http to `localhost` for every *other* project
# on the machine until the pin expires. Only sent when the request actually
# arrived over https.
HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            # setdefault semantics: never clobber a header a handler set
            # deliberately.
            if name not in response.headers:
                response.headers[name] = value

        if request.url.scheme == "https" and "Strict-Transport-Security" not in response.headers:
            response.headers["Strict-Transport-Security"] = HSTS

        return response
