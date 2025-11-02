# logs/serializers.py
from django.utils import timezone
from rest_framework import serializers

from .models import LearningRecord


class AwareDateTimeField(serializers.DateTimeField):
    """
    Datetime field that ensures tz-aware UTC datetimes.
    - Accepts ISO strings with/without timezone; if naive → assume UTC.
    - Always outputs ISO in UTC (Z).
    """
    def to_internal_value(self, value):
        dt = super().to_internal_value(value)
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt.astimezone(timezone.utc)

    def to_representation(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.utc)
        return super().to_representation(value.astimezone(timezone.utc))


class LearningRecordCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating a LearningRecord.
    Notes:
      - idempotency_key can be passed via Header (handled in the view); not required in the body.
      - start_at / end_at are optional; if both provided, must satisfy start_at <= end_at.
      - word_count must be >= 0.
    """
    # Allow idempotency_key in the body; if not provided, the view will read from the Header.
    idempotency_key = serializers.CharField(
        required=False, allow_blank=False, max_length=64
    )
    start_at = AwareDateTimeField(required=False, allow_null=True)
    end_at = AwareDateTimeField(required=False, allow_null=True)

    class Meta:
        model = LearningRecord
        fields = (
            "user_id",
            "idempotency_key",
            "word_count",
            "start_at",
            "end_at",
        )

    def validate_word_count(self, v: int):
        if v is None:
            raise serializers.ValidationError("word_count is required.")
        if v < 0:
            raise serializers.ValidationError("word_count must be >= 0.")
        return v

    def validate(self, attrs):
        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")
        if start_at is not None and end_at is not None and start_at > end_at:
            raise serializers.ValidationError("start_at must be <= end_at.")
        return attrs


class LearningRecordSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for returning a LearningRecord snapshot
    together with a derived `study_minutes` (for response echo).
    Note: the "cross-day -> 0 minutes" aggregation rule only affects
    GET /summary and does not affect the POST echo here.
    """
    start_at = AwareDateTimeField(required=False, allow_null=True)
    end_at = AwareDateTimeField(required=False, allow_null=True)
    created_at = AwareDateTimeField(read_only=True)
    study_minutes = serializers.SerializerMethodField()

    class Meta:
        model = LearningRecord
        fields = (
            "id",
            "user_id",
            "idempotency_key",
            "word_count",
            "start_at",
            "end_at",
            "created_at",
            "study_minutes",
        )
        read_only_fields = ("id", "created_at")

    def get_study_minutes(self, obj) -> int:
        if obj.start_at and obj.end_at:
            seconds = max(0, int((obj.end_at - obj.start_at).total_seconds()))
            return seconds // 60
        return 0
