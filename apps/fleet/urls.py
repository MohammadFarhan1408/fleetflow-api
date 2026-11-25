from django.urls import path

from apps.fleet import views

urlpatterns = [
    path("drivers/", views.DriverListCreateView.as_view(), name="driver-list"),
    path("drivers/<uuid:public_id>/", views.DriverDetailView.as_view(), name="driver-detail"),
    path(
        "drivers/<uuid:public_id>/status/", views.DriverStatusView.as_view(), name="driver-status"
    ),
    path(
        "drivers/<uuid:public_id>/archive/",
        views.DriverArchiveView.as_view(),
        name="driver-archive",
    ),
    path(
        "drivers/<uuid:driver_id>/leave/",
        views.DriverLeaveListCreateView.as_view(),
        name="driver-leave-list",
    ),
    path(
        "drivers/<uuid:driver_id>/leave/<uuid:public_id>/",
        views.DriverLeaveDetailView.as_view(),
        name="driver-leave-detail",
    ),
    path("vehicles/", views.VehicleListCreateView.as_view(), name="vehicle-list"),
    path("vehicles/<uuid:public_id>/", views.VehicleDetailView.as_view(), name="vehicle-detail"),
    path(
        "vehicles/<uuid:public_id>/status/",
        views.VehicleStatusView.as_view(),
        name="vehicle-status",
    ),
    path(
        "vehicles/<uuid:public_id>/archive/",
        views.VehicleArchiveView.as_view(),
        name="vehicle-archive",
    ),
]
