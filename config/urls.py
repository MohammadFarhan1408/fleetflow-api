from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core import views as core_views

urlpatterns = [
    path("health/live", core_views.health_live, name="health-live"),
    path("health/ready", core_views.health_ready, name="health-ready"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
