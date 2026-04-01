from django.db import migrations, models
from django.db.models import Count, Min


def remove_duplicate_slots(apps, schema_editor):
    ScheduledEvent = apps.get_model("scheduling", "ScheduledEvent")

    team_duplicates = (
        ScheduledEvent.objects.filter(team_id__isnull=False)
        .values("team_id", "scheduled_at")
        .annotate(total=Count("id"), keep_id=Min("id"))
        .filter(total__gt=1)
    )
    for item in team_duplicates:
        ScheduledEvent.objects.filter(
            team_id=item["team_id"],
            scheduled_at=item["scheduled_at"],
        ).exclude(id=item["keep_id"]).delete()

    personal_duplicates = (
        ScheduledEvent.objects.filter(team_id__isnull=True)
        .values("created_by_id", "scheduled_at")
        .annotate(total=Count("id"), keep_id=Min("id"))
        .filter(total__gt=1)
    )
    for item in personal_duplicates:
        ScheduledEvent.objects.filter(
            team_id__isnull=True,
            created_by_id=item["created_by_id"],
            scheduled_at=item["scheduled_at"],
        ).exclude(id=item["keep_id"]).delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_slots, noop_reverse),
        migrations.AddConstraint(
            model_name="scheduledevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(team__isnull=False),
                fields=("team", "scheduled_at"),
                name="uniq_team_scheduled_slot",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduledevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(team__isnull=True),
                fields=("created_by", "scheduled_at"),
                name="uniq_personal_scheduled_slot",
            ),
        ),
    ]
