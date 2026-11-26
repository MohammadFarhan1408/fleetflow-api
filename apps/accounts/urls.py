from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path(
        "auth/password-reset/",
        views.PasswordResetRequestView.as_view(),
        name="auth-password-reset",
    ),
    path(
        "auth/password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("me/", views.MeView.as_view(), name="me"),
    path("branches/", views.BranchListCreateView.as_view(), name="branch-list"),
    path("users/", views.UserListCreateView.as_view(), name="user-list"),
    path("users/<uuid:public_id>/", views.UserDetailView.as_view(), name="user-detail"),
]
