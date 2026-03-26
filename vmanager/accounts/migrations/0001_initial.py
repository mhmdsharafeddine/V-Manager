from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("club_admin", "Club Admin"), ("coach", "Coach"), ("player", "Player"), ("parent", "Parent")], max_length=20)),
                ("club_name", models.CharField(max_length=150)),
                ("team_size", models.CharField(choices=[("1-10", "1-10 Members"), ("11-20", "11-20 Members"), ("21-30", "21-30 Members"), ("31+", "31+ Members")], max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["user__first_name", "user__last_name", "user__email"],
            },
        ),
    ]
