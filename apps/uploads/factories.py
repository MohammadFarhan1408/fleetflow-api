import uuid

import factory
from django.utils import timezone

from apps.accounts.factories import UserFactory
from apps.tenancy.factories import CompanyFactory
from apps.uploads.models import Upload, UploadContentType, UploadPurpose, UploadStatus


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


class AttachedUploadFactory(UploadFactory):
    """A confirmed, attached upload (what a stored document points at). Still no object in
    the bucket unless a test puts one at `object_key`."""

    status = UploadStatus.CONFIRMED
    etag = "test-etag"
    confirmed_at = factory.LazyFunction(timezone.now)
    attached_at = factory.LazyFunction(timezone.now)
