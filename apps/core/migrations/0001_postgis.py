from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    # Enabled before any geometry field exists so every database (including the
    # pytest-created test DB) has PostGIS. The GeoDjango engine switch is Phase 2.
    operations = [CreateExtension("postgis")]
