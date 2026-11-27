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
        "drivers/<uuid:public_id>/unarchive/",
        views.DriverUnarchiveView.as_view(),
        name="driver-unarchive",
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
    path(
        "drivers/<uuid:driver_id>/documents/",
        views.DriverDocumentListCreateView.as_view(),
        name="driver-document-list",
    ),
    path(
        "drivers/<uuid:driver_id>/documents/<uuid:public_id>/",
        views.DriverDocumentDetailView.as_view(),
        name="driver-document-detail",
    ),
    path(
        "drivers/<uuid:driver_id>/documents/<uuid:public_id>/archive/",
        views.DriverDocumentArchiveView.as_view(),
        name="driver-document-archive",
    ),
    path(
        "drivers/<uuid:driver_id>/documents/<uuid:public_id>/download/",
        views.DriverDocumentDownloadView.as_view(),
        name="driver-document-download",
    ),
    path(
        "vehicles/<uuid:vehicle_id>/documents/",
        views.VehicleDocumentListCreateView.as_view(),
        name="vehicle-document-list",
    ),
    path(
        "vehicles/<uuid:vehicle_id>/documents/<uuid:public_id>/",
        views.VehicleDocumentDetailView.as_view(),
        name="vehicle-document-detail",
    ),
    path(
        "vehicles/<uuid:vehicle_id>/documents/<uuid:public_id>/archive/",
        views.VehicleDocumentArchiveView.as_view(),
        name="vehicle-document-archive",
    ),
    path(
        "vehicles/<uuid:vehicle_id>/documents/<uuid:public_id>/download/",
        views.VehicleDocumentDownloadView.as_view(),
        name="vehicle-document-download",
    ),
    path("me/driver/", views.MyDriverView.as_view(), name="me-driver"),
    path("me/shift/start/", views.MyShiftStartView.as_view(), name="me-shift-start"),
    path("me/shift/end/", views.MyShiftEndView.as_view(), name="me-shift-end"),
]
