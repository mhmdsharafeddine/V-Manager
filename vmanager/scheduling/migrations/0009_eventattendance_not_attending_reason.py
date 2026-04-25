from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0008_alter_eventnotificationread_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventattendance",
            name="not_attending_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("absent", "Absent"),
                    ("did_not_attend", "Didn't Attend"),
                    ("injured", "Injured"),
                    ("excused", "Excused"),
                ],
                max_length=30,
            ),
        ),
    ]
