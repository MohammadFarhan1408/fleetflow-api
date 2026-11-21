from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

# The error envelope (architecture §6), for the OpenAPI schema only. Declared inline
# because a field named `errors` would shadow `Serializer.errors` on a normal class.
ErrorSerializer = inline_serializer(
    name="Error",
    fields={
        "type": serializers.CharField(),
        "title": serializers.CharField(),
        "status": serializers.IntegerField(),
        "code": serializers.CharField(),
        "detail": serializers.CharField(),
        "errors": serializers.DictField(required=False),
        "request_id": serializers.CharField(allow_null=True),
    },
)
