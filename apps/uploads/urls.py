from django.urls import path

from apps.uploads import views

urlpatterns = [
    path("uploads/", views.UploadCreateView.as_view(), name="upload-create"),
    path(
        "uploads/<uuid:public_id>/confirm/",
        views.UploadConfirmView.as_view(),
        name="upload-confirm",
    ),
]
