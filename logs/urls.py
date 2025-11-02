from django.urls import path
from .views import RecordCreateView, UserSummaryView

urlpatterns = [
    path("records", RecordCreateView.as_view(), name="record-create"),
    path("users/<str:user_id>/summary", UserSummaryView.as_view(), name="user-summary"),
]
