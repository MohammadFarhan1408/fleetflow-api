from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core import views as core_views

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/live", core_views.health_live, name="health-live"),
    path("health/ready", core_views.health_ready, name="health-ready"),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.customers.urls")),
    path("api/v1/", include("apps.uploads.urls")),
    path("api/v1/", include("apps.shipments.urls")),
]

if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
