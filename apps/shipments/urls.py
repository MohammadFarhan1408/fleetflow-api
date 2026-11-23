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
]
