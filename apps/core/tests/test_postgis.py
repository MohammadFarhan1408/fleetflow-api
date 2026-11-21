import pytest
from django.db import connection


@pytest.mark.django_db
def test_postgis_extension_is_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT postgis_version()")
        row = cursor.fetchone()

    assert row is not None
    assert row[0]
