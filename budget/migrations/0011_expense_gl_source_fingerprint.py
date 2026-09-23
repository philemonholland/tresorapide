from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0010_grandlivreentry_unique_gl_row_per_upload"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="gl_source_fingerprint",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Empreinte déterministe de la ligne du Grand Livre ayant "
                    "créé cette dépense. Vide pour les autres sources."
                ),
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="expense",
            constraint=models.UniqueConstraint(
                condition=~models.Q(gl_source_fingerprint=""),
                fields=("budget_year", "gl_source_fingerprint"),
                name="unique_gl_source_per_budget_year",
            ),
        ),
    ]
