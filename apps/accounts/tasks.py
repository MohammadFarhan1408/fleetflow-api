import smtplib
from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail

from apps.accounts.models import User


def set_password_link(user: User) -> str:
    """Link to the admin page that completes password set/reset for this user.

    The token is Django's PasswordResetTokenGenerator: time-limited by
    PASSWORD_RESET_TIMEOUT and invalidated as soon as the password or last login changes.
    """
    query = urlencode(
        {"uid": str(user.public_id), "token": default_token_generator.make_token(user)}
    )
    return f"{settings.PASSWORD_RESET_URL}?{query}"


@shared_task(
    autoretry_for=(smtplib.SMTPException, ConnectionError, TimeoutError),
    retry_backoff=True,
    max_retries=5,
)
def send_invite_email(user_id: int) -> None:
    """Email a newly created user the link to choose their password.

    Retries only transient delivery errors. A retry after a partial failure can send a
    second email; that is acceptable because both links are valid until one is used.
    """
    user = User.objects.select_related("company").filter(pk=user_id, is_active=True).first()
    if user is None:
        return
    send_mail(
        subject=f"You have been invited to FleetFlow ({user.company.name})",
        message=(
            f"Hello {user.full_name},\n\n"
            f"An account has been created for you at {user.company.name} on FleetFlow.\n"
            f"Choose your password here:\n\n{set_password_link(user)}\n\n"
            "If you did not expect this email, you can ignore it."
        ),
        from_email=None,
        recipient_list=[user.email],
    )
