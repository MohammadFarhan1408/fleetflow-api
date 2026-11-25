"""Seed the fleet permissions with the role table approved in memory D-API-25.

Values are written out here rather than imported, so this migration keeps working if the
application constants change later.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ROLE_PERMISSIONS = {
    "fleet.view": [
        "owner",
        "admin",
        "ops_manager",
        "dispatcher",
        "fleet_manager",
        "finance",
        "viewer",
    ],
    "fleet.manage": ["owner", "admin", "ops_manager", "fleet_manager"],
    "fleet.all_branches": ["owner", "admin", "ops_manager"],
}


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    for codename, role_codes in ROLE_PERMISSIONS.items():
        permission, _ = Permission.objects.get_or_create(codename=codename)
        permission.roles.add(*Role.objects.filter(code__in=role_codes))


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.filter(codename__in=ROLE_PERMISSIONS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("fleet", "0001_initial"),
        ("accounts", "0002_seed_roles_permissions"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
