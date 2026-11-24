from rest_framework import serializers

from apps.uploads.models import Upload, UploadContentType, UploadPurpose


class UploadCreateSerializer(serializers.Serializer[dict[str, object]]):
    purpose = serializers.ChoiceField(choices=UploadPurpose.choices)
    content_type = serializers.ChoiceField(choices=UploadContentType.choices)
    size = serializers.IntegerField(min_value=1, help_text="File size in bytes")


class UploadSerializer(serializers.ModelSerializer[Upload]):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Upload
        fields = ["id", "purpose", "content_type", "size", "status", "confirmed_at"]
        read_only_fields = fields


class UploadTicketSerializer(serializers.Serializer[dict[str, object]]):
    """Response of `POST /uploads/`: send the file with `method` to `upload_url` and exactly
    these `headers`, before `expires_at`, then confirm."""

    id = serializers.UUIDField()
    upload_url = serializers.URLField()
    method = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())
    expires_at = serializers.DateTimeField()
