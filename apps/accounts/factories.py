from typing import Any

import factory

from apps.accounts.models import Role, User
from apps.tenancy.factories import CompanyFactory

DEFAULT_TEST_PASSWORD = "correct-horse-battery-staple"  # noqa: S105  (test fixture value)


class UserFactory(factory.django.DjangoModelFactory[User]):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    company = factory.SubFactory(CompanyFactory)
    password = factory.django.Password(DEFAULT_TEST_PASSWORD)

    @factory.post_generation
    def roles(self, create: bool, extracted: Any, **kwargs: Any) -> None:
        """`UserFactory(roles=["owner"])` assigns seeded roles by code."""
        if create and extracted:
            self.roles.add(*Role.objects.filter(code__in=extracted))
