import uuid

import factory

from apps.accounts.factories import UserFactory
from apps.tenancy.factories import CompanyFactory
from apps.uploads.models import Upload, UploadContentType, UploadPurpose


class UploadFactory(factory.django.DjangoModelFactory[Upload]):
    """A pending upload whose object does not exist in the bucket unless a test puts it."""

    class Meta:
        model = Upload

    company = factory.SubFactory(CompanyFactory)
    uploaded_by = factory.SubFactory(UserFactory, company=factory.SelfAttribute("..company"))
    purpose = UploadPurpose.FLEET_DOCUMENT
    content_type = UploadContentType.JPEG
    size = 1000
    object_key = factory.LazyFunction(lambda: f"test/{uuid.uuid4().hex}.jpg")
