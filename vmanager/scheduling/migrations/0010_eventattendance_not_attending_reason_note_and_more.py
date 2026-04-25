from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0009_eventattendance_not_attending_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="eventattendance",
            name="not_attending_reason",
            field=models.CharField(
                blank=True,
                choices=[("injured", "Injured"), ("other", "Other")],
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="eventattendance",
            name="not_attending_reason_note",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
