from django.urls import path

from apps.dispatch import views

urlpatterns = [
    path("routes/", views.RouteListCreateView.as_view(), name="route-list"),
    path("routes/<uuid:public_id>/", views.RouteDetailView.as_view(), name="route-detail"),
    path("routes/<uuid:public_id>/stops/", views.RouteStopsView.as_view(), name="route-stops"),
    path(
        "routes/<uuid:route_id>/stops/<uuid:public_id>/",
        views.RouteStopDetailView.as_view(),
        name="route-stop-detail",
    ),
    path(
        "routes/<uuid:public_id>/reorder/", views.RouteReorderView.as_view(), name="route-reorder"
    ),
    path("routes/<uuid:public_id>/assign/", views.RouteAssignView.as_view(), name="route-assign"),
    path(
        "routes/<uuid:public_id>/unassign/",
        views.RouteUnassignView.as_view(),
        name="route-unassign",
    ),
    path("routes/<uuid:public_id>/cancel/", views.RouteCancelView.as_view(), name="route-cancel"),
]
