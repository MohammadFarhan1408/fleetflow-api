from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.factories import UserFactory
from apps.accounts.models import RefreshTokenRecord, TokenFamily
from apps.accounts.tasks import purge_expired_refresh_tokens
from apps.accounts.tokens import issue_token_pair

pytestmark = pytest.mark.django_db


def test_purge_removes_expired_tokens_and_empty_sessions() -> None:
    user = UserFactory.create()
    stale = TokenFamily.objects.create(user=user)
    issue_token_pair(user, stale)
    RefreshTokenRecord.objects.filter(family=stale).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    live = TokenFamily.objects.create(user=user)
    issue_token_pair(user, live)

    purge_expired_refresh_tokens()

    assert list(TokenFamily.objects.all()) == [live]
    assert RefreshTokenRecord.objects.filter(family=live).count() == 1
