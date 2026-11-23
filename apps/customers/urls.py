from django.urls import path

from apps.customers import views

urlpatterns = [
    path("customers/", views.CustomerListCreateView.as_view(), name="customer-list"),
    path("customers/<uuid:public_id>/", views.CustomerDetailView.as_view(), name="customer-detail"),
    path(
        "customers/<uuid:public_id>/archive/",
        views.CustomerArchiveView.as_view(),
        name="customer-archive",
    ),
    path(
        "customers/<uuid:customer_id>/addresses/",
        views.AddressListCreateView.as_view(),
        name="customer-address-list",
    ),
    path(
        "customers/<uuid:customer_id>/addresses/<uuid:public_id>/",
        views.AddressDetailView.as_view(),
        name="customer-address-detail",
    ),
    path(
        "customers/<uuid:customer_id>/addresses/<uuid:public_id>/archive/",
        views.AddressArchiveView.as_view(),
        name="customer-address-archive",
    ),
]
