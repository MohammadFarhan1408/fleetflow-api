"""Backfill the Phase 2 company and branch fields, and seed default service types.

Existing companies get USD and UTC. Only development data existed when this was written
(hosting is deferred to Phase 11, memory D-API-6); a real database must set the right
values before migrating. Existing branches get the code B<id>, unique within a company.
Values are written out here rather than imported, so this migration keeps working if the
application constants change later.
"""

from decimal import Decimal

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

SERVICE_TYPES = [
    ("standard", "Standard", Decimal(30), Decimal(120), Decimal(80), Decimal(80)),
    ("express", "Express", Decimal(30), Decimal(120), Decimal(80), Decimal(80)),
    ("freight", "Freight", Decimal(1000), Decimal(240), Decimal(120), Decimal(220)),
]


def backfill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Company = apps.get_model("tenancy", "Company")
    Branch = apps.get_model("tenancy", "Branch")
    ServiceType = apps.get_model("tenancy", "ServiceType")

    Company.objects.filter(currency__isnull=True).update(currency="USD")
    Company.objects.filter(timezone__isnull=True).update(timezone="UTC")
    for branch in Branch.objects.filter(code__isnull=True):
        branch.code = f"B{branch.pk}"
        branch.save(update_fields=["code"])

    for company in Company.objects.all():
        for code, name, weight, length, width, height in SERVICE_TYPES:
            ServiceType.objects.get_or_create(
                company=company,
                code=code,
                defaults={
                    "name": name,
                    "max_weight_kg": weight,
                    "max_length_cm": length,
                    "max_width_cm": width,
                    "max_height_cm": height,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0002_company_locale_branch_code_service_type")]

    # Reversing leaves the data: the previous schema migration drops these columns anyway.
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
