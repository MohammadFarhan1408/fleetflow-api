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
    path(
        "routes/<uuid:public_id>/suggest-order/",
        views.RouteSuggestOrderView.as_view(),
        name="route-suggest-order",
    ),
    path("routes/<uuid:public_id>/assign/", views.RouteAssignView.as_view(), name="route-assign"),
    path(
        "routes/<uuid:public_id>/unassign/",
        views.RouteUnassignView.as_view(),
        name="route-unassign",
    ),
    path(
        "routes/<uuid:public_id>/dispatch/",
        views.RouteDispatchView.as_view(),
        name="route-dispatch",
    ),
    path(
        "routes/<uuid:public_id>/reassign/",
        views.RouteReassignView.as_view(),
        name="route-reassign",
    ),
    path("me/routes/", views.MyRouteListView.as_view(), name="me-route-list"),
    path("me/routes/<uuid:public_id>/", views.MyRouteDetailView.as_view(), name="me-route-detail"),
    path(
        "me/routes/<uuid:public_id>/start/",
        views.MyRouteStartView.as_view(),
        name="me-route-start",
    ),
    path("routes/<uuid:public_id>/cancel/", views.RouteCancelView.as_view(), name="route-cancel"),
]
