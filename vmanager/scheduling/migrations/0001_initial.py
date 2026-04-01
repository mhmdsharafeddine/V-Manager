from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("team_management", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduledEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("practice", "Practice"),
                            ("match", "Match"),
                            ("tournament", "Tournament"),
                            ("training", "Strength & Conditioning"),
                            ("other", "Other"),
                        ],
                        default="other",
                        max_length=20,
                    ),
                ),
                ("scheduled_at", models.DateTimeField()),
                ("location", models.CharField(blank=True, max_length=180)),
                ("details", models.TextField(blank=True)),
                ("attendees_count", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[("scheduled", "Scheduled"), ("cancelled", "Cancelled")],
                        default="scheduled",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scheduled_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="team_management.team",
                    ),
                ),
            ],
            options={"ordering": ["scheduled_at", "id"]},
        ),
    ]
