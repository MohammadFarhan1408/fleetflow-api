"""Seed `dispatch.override_capacity` (memory D-API-31). Values are written out here, not
imported, so the migration keeps working if the application constants change later."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

CODENAME = "dispatch.override_capacity"
ROLES = ["owner", "admin", "ops_manager", "dispatcher"]


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    permission, _ = Permission.objects.get_or_create(codename=CODENAME)
    permission.roles.add(*Role.objects.filter(code__in=ROLES))


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.filter(codename=CODENAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dispatch", "0003_route_assignment"),
        ("accounts", "0002_seed_roles_permissions"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
