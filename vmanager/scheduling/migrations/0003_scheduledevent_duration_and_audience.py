from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0002_event_slot_uniqueness_constraints"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledevent",
            name="duration_minutes",
            field=models.PositiveIntegerField(default=90),
        ),
        migrations.AddField(
            model_name="scheduledevent",
            name="audience",
            field=models.CharField(
                choices=[
                    ("all", "Entire Team"),
                    ("players", "Players"),
                    ("coaches", "Coaches"),
                    ("staff", "Staff"),
                    ("managers", "Managers"),
                    ("private", "Only Me"),
                ],
                default="all",
                max_length=20,
            ),
        ),
    ]
