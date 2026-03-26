from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_update_roles_and_optional_club_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="accountprofile",
            name="team_size",
        ),
        migrations.AddField(
            model_name="accountprofile",
            name="child_name",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
