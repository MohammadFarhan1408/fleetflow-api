from typing import Any

from django.core.management.base import BaseCommand

from apps.core import storage


class Command(BaseCommand):
    help = "Create the private upload bucket if it does not exist (local development and CI)."

    def handle(self, *args: Any, **options: Any) -> None:
        created = storage.ensure_bucket()
        self.stdout.write("Created bucket." if created else "Bucket already exists.")
