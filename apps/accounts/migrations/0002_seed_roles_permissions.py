"""Seed the built-in roles and the Phase 1 permissions (architecture §8).

Values are written out here rather than imported, so this migration keeps working if the
application constants change later. Later phases add their permissions in their own data
migrations.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ROLES = [
    "owner",
    "admin",
    "ops_manager",
    "dispatcher",
    "fleet_manager",
    "warehouse_staff",
    "finance",
    "customer_service",
    "driver",
    "viewer",
]

ROLE_PERMISSIONS = {
    "users.view": ["owner", "admin", "ops_manager"],
    "users.manage": ["owner", "admin"],
}


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("accounts", "Permission")
    roles = {code: Role.objects.get_or_create(code=code)[0] for code in ROLES}
    for codename, role_codes in ROLE_PERMISSIONS.items():
        permission, _ = Permission.objects.get_or_create(codename=codename)
        permission.roles.add(*(roles[code] for code in role_codes))


def unseed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.filter(codename__in=ROLE_PERMISSIONS).delete()
    # Roles stay: users may reference them.


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
