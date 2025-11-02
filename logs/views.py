# logs/views.py
from __future__ import annotations

import datetime as dt
import pytz

from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LearningRecord
from .services import summarize_with_sma


def _to_aware(dt_str: str | None) -> dt.datetime | None:
    """Parse an ISO string into a tz-aware (UTC) datetime; allow None."""
    if not dt_str:
        return None
    d = parse_datetime(dt_str)
    if d is None:
        raise ValueError("invalid datetime format")
    if timezone.is_naive(d):
        d = timezone.make_aware(d, dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def _fmt_small_values(val_wc, val_mins):
    """Small-value replacements (used for totals and averages)."""
    def wc(v):
        if v is None:
            return None
        v = float(v)
        return 'word count less than 1' if v < 1.0 else v

    def mins(v):
        if v is None:
            return None
        v = float(v)
        return 'study minutes less than a minute' if v < 1.0 else v

    return wc(val_wc), mins(val_mins)


class RecordCreateView(APIView):
    """POST /api/records (supports idempotency conflict 409)."""
    def post(self, request):
        body = request.data or {}
        user_id = body.get('user_id')
        word_count = body.get('word_count')
        idem = request.headers.get('Idempotency-Key') or body.get('idempotency_key')
        start_at_raw = body.get('start_at')
        end_at_raw = body.get('end_at')

        if not user_id:
            return Response({'detail': 'user_id is required.'}, status=400)
        try:
            word_count = int(word_count)
        except (TypeError, ValueError):
            return Response({'detail': 'word_count must be an integer.'}, status=400)
        if word_count < 0:
            return Response({'detail': 'word_count must be >= 0.'}, status=400)
        if not idem:
            return Response({'detail': 'Idempotency-Key (header or body) is required.'}, status=400)

        try:
            start_at = _to_aware(start_at_raw)
            end_at = _to_aware(end_at_raw)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)

        # If both start_at and end_at are missing, default end_at to now (server time).
        if start_at is None and end_at is None:
            end_at = timezone.now()
            if timezone.is_naive(end_at):
                end_at = timezone.make_aware(end_at, dt.timezone.utc)

        if start_at is not None and end_at is not None and start_at > end_at:
            return Response({'detail': 'start_at must be <= end_at.'}, status=400)

        try:
            with transaction.atomic():
                obj, created = LearningRecord.objects.get_or_create(
                    user_id=user_id,
                    idempotency_key=idem,
                    defaults={
                        'word_count': word_count,
                        'start_at': start_at,
                        'end_at': end_at,
                    },
                )
                if created:
                    status_code = status.HTTP_201_CREATED
                else:
                    # Same idempotency key: accept only if payload is identical; otherwise 409.
                    same_payload = (
                        obj.word_count == word_count and
                        ((obj.start_at == start_at) or (obj.start_at is None and start_at is None)) and
                        ((obj.end_at == end_at) or (obj.end_at is None and end_at is None))
                    )
                    if not same_payload:
                        return Response(
                            {'detail': 'Idempotency-Key reused with different payload.'},
                            status=status.HTTP_409_CONFLICT
                        )
                    status_code = status.HTTP_200_OK
        except IntegrityError:
            # Handle race: unique constraint hit, re-read and compare payload.
            obj = LearningRecord.objects.get(user_id=user_id, idempotency_key=idem)
            same_payload = (
                obj.word_count == word_count and
                ((obj.start_at == start_at) or (obj.start_at is None and start_at is None)) and
                ((obj.end_at == end_at) or (obj.end_at is None and end_at is None))
            )
            if not same_payload:
                return Response(
                    {'detail': 'Idempotency-Key reused with different payload.'},
                    status=status.HTTP_409_CONFLICT
                )
            status_code = status.HTTP_200_OK

        # Derived study_minutes returned in POST echo (independent of cross-day bucketing rules).
        if obj.start_at and obj.end_at:
            seconds = max(0, int((obj.end_at - obj.start_at).total_seconds()))
        else:
            seconds = 0
        study_minutes = seconds // 60

        return Response({
            'id': obj.id,
            'user_id': obj.user_id,
            'idempotency_key': idem,
            'word_count': obj.word_count,
            'start_at': obj.start_at,
            'end_at': obj.end_at,
            'created_at': obj.created_at,
            'study_minutes': study_minutes,
        }, status=status_code)


class UserSummaryView(APIView):
    """
    GET /api/users/{user_id}/summary
      ?from=ISO
      &to=ISO
      &granularity=hour|day|month
      &tz=Asia/Tokyo
      &include_empty=true|false
    Returns only totals and averages_per_bucket (no buckets in the response).
    """
    def get(self, request, user_id: str):
        dt_from = request.query_params.get('from')
        dt_to = request.query_params.get('to')
        gran = request.query_params.get('granularity', 'day')
        tzname = request.query_params.get('tz', 'UTC')
        include_empty = request.query_params.get('include_empty', 'true').lower() == 'true'

        if not dt_from or not dt_to:
            return Response({'detail': 'from and to are required (ISO-8601).'}, status=400)

        # Tolerate a space where '+00:00' should be (e.g., when URL query wasn't URL-encoded in tests).
        if ' ' in dt_from and ('+00:00' not in dt_from) and ('Z' not in dt_from):
            dt_from = dt_from.replace(' ', '+', 1)
        if ' ' in dt_to and ('+00:00' not in dt_to) and ('Z' not in dt_to):
            dt_to = dt_to.replace(' ', '+', 1)

        if gran not in ('hour', 'day', 'month'):
            return Response({'detail': 'granularity must be hour|day|month.'}, status=400)
        try:
            pytz.timezone(tzname)
        except Exception:
            return Response({'detail': 'invalid tz.'}, status=400)

        # Still compute per-bucket internally, but do not include buckets in the API response.
        buckets = summarize_with_sma(user_id, dt_from, dt_to, granularity=gran, tz=tzname)

        wc_total = sum(float(b['wc_sum']) for b in buckets)
        mins_total = sum(float(b['mins_sum']) for b in buckets)

        if include_empty:
            denom = len(buckets) or 1
        else:
            active = [b for b in buckets if (float(b['wc_sum']) > 0.0 or float(b['mins_sum']) > 0.0)]
            denom = len(active) or 1

        wc_mean = wc_total / denom
        mins_mean = mins_total / denom

        wc_total_fmt, mins_total_fmt = _fmt_small_values(wc_total, mins_total)
        wc_mean_fmt, mins_mean_fmt = _fmt_small_values(wc_mean, mins_mean)

        return Response({
            'user_id': user_id,
            'from': dt_from,
            'to': dt_to,
            'granularity': gran,
            'tz': tzname,
            'include_empty': include_empty,
            'totals': {
                'word_count': wc_total_fmt,
                'study_minutes': mins_total_fmt,
            },
            'averages_per_bucket': {
                'word_count': wc_mean_fmt,
                'study_minutes': mins_mean_fmt,
            },
        }, status=status.HTTP_200_OK)
