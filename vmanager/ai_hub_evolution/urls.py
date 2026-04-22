# ai_hub/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='ai_hub_home'),
    path("generate-insights/", views.generate_insights, name="generate_insights"),
    path(
        "player/<int:member_id>/monthly-stats/",
        views.player_monthly_stats,
        name="player_monthly_stats"
    ),
]
