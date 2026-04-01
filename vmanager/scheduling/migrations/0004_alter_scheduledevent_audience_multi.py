from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0003_scheduledevent_duration_and_audience"),
    ]

    operations = [
        migrations.AlterField(
            model_name="scheduledevent",
            name="audience",
            field=models.CharField(default="all", max_length=120),
        ),
    ]
