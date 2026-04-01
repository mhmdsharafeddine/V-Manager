from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def set_default_status_for_existing_rows(apps, schema_editor):
    TeamMembership = apps.get_model("team_management", "TeamMembership")
    TeamMembership.objects.filter(status="").update(status="approved")


class Migration(migrations.Migration):

    dependencies = [
        ("team_management", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="teammembership",
            name="requested_role",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="teammembership",
            name="status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                default="approved",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="teammembership",
            name="rejection_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="teammembership",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="teammembership",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_team_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(set_default_status_for_existing_rows, migrations.RunPython.noop),
    ]
