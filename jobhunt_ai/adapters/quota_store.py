"""Database-backed request allowances, shared by web and worker processes."""

from datetime import UTC, timedelta

import rls
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.services import has_premium
from jobhunt_ai.models import UserAIQuota
from jobhunt_ai.quotas import QuotaExceededException, QuotaLimits
from jobhunt_ai.settings import get


class DjangoAIQuotaStore:
    def consume(self, owner_id: int) -> None:
        # Lock the existing account, including on a user's first ever call.
        # Locking only a not-yet-created quota row allows creation races.
        # The transaction ends BEFORE the provider is called.
        with rls.as_user(owner_id), transaction.atomic():
            user = get_user_model().objects.select_for_update().get(pk=owner_id)
            if not user.is_active:
                raise PermissionDenied("Ce compte est désactivé.")
            tier = "paid" if has_premium(user) else "free"
            limits = QuotaLimits(
                get(f"{tier.upper()}_MONTHLY_REQUESTS"),
                get(f"{tier.upper()}_REQUESTS_PER_MINUTE"),
            )
            # Evaluate time after acquiring the lock (a waiter may cross a boundary).
            now = timezone.now().astimezone(UTC)
            month = now.date().replace(day=1)
            minute = now.replace(second=0, microsecond=0)
            quota, _ = UserAIQuota.objects.get_or_create(
                owner_id=owner_id, month=month,
                defaults={"minute_started_at": minute},
            )
            if quota.requests >= limits.monthly_requests:
                next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
                raise QuotaExceededException(
                    tier=tier, limit=limits.monthly_requests, period="month",
                    reset_at=now.replace(
                        year=next_month.year, month=next_month.month, day=1,
                        hour=0, minute=0, second=0, microsecond=0,
                    ),
                )
            if quota.minute_started_at != minute:
                quota.minute_started_at = minute
                quota.minute_requests = 0
            if quota.minute_requests >= limits.requests_per_minute:
                raise QuotaExceededException(
                    tier=tier, limit=limits.requests_per_minute, period="minute",
                    reset_at=minute + timedelta(minutes=1),
                )
            quota.requests += 1
            quota.minute_requests += 1
            quota.save(update_fields=["requests", "minute_requests", "minute_started_at"])
