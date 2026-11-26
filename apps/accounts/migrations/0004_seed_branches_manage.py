"""Seed `branches.manage` for owner and admin (they already hold `users.manage`).

Values are written out here rather than imported, so this migration keeps working if the
application constants change later.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

CODENAME = "branches.manage"
ROLE_CODES = ["owner", "admin"]


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    permission, _ = Permission.objects.get_or_create(codename=CODENAME)
    permission.roles.add(*Role.objects.filter(code__in=ROLE_CODES))


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model("accounts", "Permission").objects.filter(codename=CODENAME).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_token_sessions")]

    operations = [migrations.RunPython(seed, unseed)]
