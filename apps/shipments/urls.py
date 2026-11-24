from django.urls import path

from apps.shipments import views

urlpatterns = [
    path("shipments/", views.ShipmentListCreateView.as_view(), name="shipment-list"),
    path("shipments/<uuid:public_id>/", views.ShipmentDetailView.as_view(), name="shipment-detail"),
    path(
        "shipments/<uuid:public_id>/tracking-token/",
        views.ShipmentTrackingTokenView.as_view(),
        name="shipment-tracking-token",
    ),
    path(
        "shipments/<uuid:public_id>/transitions/",
        views.ShipmentTransitionView.as_view(),
        name="shipment-transition",
    ),
    path(
        "shipments/<uuid:public_id>/corrections/",
        views.ShipmentCorrectionView.as_view(),
        name="shipment-correction",
    ),
    path(
        "shipments/<uuid:public_id>/events/",
        views.ShipmentTimelineView.as_view(),
        name="shipment-events",
    ),
    path("track/<str:token>/", views.PublicTrackingView.as_view(), name="public-tracking"),
]
