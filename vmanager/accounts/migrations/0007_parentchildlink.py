from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_accountprofile_linked_player"),
    ]

    operations = [
        migrations.CreateModel(
            name="ParentChildLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "child_profile",
                    models.OneToOneField(
                        on_delete=models.CASCADE,
                        related_name="parent_link",
                        to="accounts.accountprofile",
                    ),
                ),
                (
                    "parent_profile",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="child_links",
                        to="accounts.accountprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="parentchildlink",
            constraint=models.UniqueConstraint(
                fields=("parent_profile", "child_profile"),
                name="uniq_parent_child_link",
            ),
        ),
    ]
