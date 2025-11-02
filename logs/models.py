from django.db import models

class LearningRecord(models.Model):
    user_id = models.CharField(max_length=64, db_index=True)        # User identifier
    idempotency_key = models.CharField(max_length=64)               # Idempotency key (unique per record)
    word_count = models.PositiveIntegerField()                      # Total words learned in the session
    start_at = models.DateTimeField(null=True, blank=True, db_index=True)  # Session start time (optional)
    end_at = models.DateTimeField(null=True, blank=True, db_index=True)    # Session end time (optional)
    created_at = models.DateTimeField(auto_now_add=True)            # Record creation time (server-side)


    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "idempotency_key"],
                                     name="uq_user_idempotency"),
        ]
        indexes = [
            models.Index(fields=["user_id", "end_at"], name="idx_user_end"),
            models.Index(fields=["user_id", "start_at"], name="idx_user_start"),
        ]

