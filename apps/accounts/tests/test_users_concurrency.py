import threading

import pytest
from django.db import connection
from django.test import RequestFactory

from apps.accounts import services
from apps.accounts.factories import UserFactory
from apps.accounts.models import RoleCode, User
from apps.core.errors import DomainError
from apps.tenancy.factories import CompanyFactory


# serialized_rollback restores the migration-seeded roles after the table flush.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_owners_demoting_each_other_at_once_leave_one_owner() -> None:
    company = CompanyFactory.create()
    first = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    second = UserFactory.create(company=company, roles=[RoleCode.OWNER])
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def demote(actor: User, target: User) -> None:
        barrier.wait()
        try:
            services.update_user(
                actor=actor,
                target=target,
                changes=services.UserChanges(roles=[RoleCode.ADMIN]),
                request=RequestFactory().patch("/"),
            )
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(str(exc.error_code))
        finally:
            connection.close()

    threads = [
        threading.Thread(target=demote, args=(first, second)),
        threading.Thread(target=demote, args=(second, first)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # The company lock serializes the two edits: the loser is no longer an owner.
    assert sorted(outcomes) == ["ok", "user.owner_required"]
    owners = User.objects.filter(company=company, roles__code=RoleCode.OWNER, is_active=True)
    assert owners.count() == 1
