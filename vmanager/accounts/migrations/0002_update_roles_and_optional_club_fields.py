from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accountprofile",
            name="club_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AlterField(
            model_name="accountprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("coach", "Coach"),
                    ("manager", "Manager"),
                    ("staff", "Staff"),
                    ("player", "Player"),
                    ("parent", "Parent"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="accountprofile",
            name="team_size",
            field=models.CharField(
                blank=True,
                choices=[
                    ("1-10", "1-10 Members"),
                    ("11-20", "11-20 Members"),
                    ("21-30", "21-30 Members"),
                    ("31+", "31+ Members"),
                ],
                max_length=10,
            ),
        ),
    ]
