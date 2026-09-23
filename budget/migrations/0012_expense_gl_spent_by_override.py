from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0011_expense_gl_source_fingerprint"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="gl_spent_by_override",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Vrai lorsque l'attribution 'Dépensé par' d'un import GL "
                    "a été confirmée manuellement et ne doit plus être recalculée."
                ),
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="gl_spent_by_override_reason",
            field=models.CharField(
                blank=True,
                help_text="Motif de l'attribution manuelle d'un import GL.",
                max_length=255,
            ),
        ),
    ]
