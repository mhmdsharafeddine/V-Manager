from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_accountprofile_date_of_birth_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountprofile",
            name="linked_player",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="linked_parent",
                to="accounts.accountprofile",
            ),
        ),
    ]
