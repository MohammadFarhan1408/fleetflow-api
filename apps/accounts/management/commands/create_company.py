from argparse import ArgumentParser
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.services import create_company_with_owner
from apps.core.errors import DomainError


class Command(BaseCommand):
    help = "Create a company (tenant) and invite its first owner by email."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--name", required=True, help="Company name")
        parser.add_argument("--currency", required=True, help="ISO 4217 code, e.g. EUR")
        parser.add_argument("--timezone", required=True, help="IANA timezone, e.g. Europe/Berlin")
        parser.add_argument("--owner-email", required=True, help="Owner's login email")
        parser.add_argument("--owner-name", required=True, help="Owner's full name")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            company, owner = create_company_with_owner(
                company_name=options["name"],
                currency=options["currency"],
                timezone=options["timezone"],
                owner_email=options["owner_email"],
                owner_name=options["owner_name"],
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        except DomainError as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Created company {company.public_id} and invited owner {owner.email}."
            )
        )
