"""The size of a document, recorded on the row.

Reading it from the storage costs nothing on a local disk and one network
round-trip per rendered row on a remote provider (Azure Blob Storage), so
the library page would spend a request per document. Existing rows are
filled in here, best effort: a file the storage cannot answer for keeps a
null size and the model falls back to asking, exactly as before.
"""

from django.db import migrations, models


def record_sizes(apps, schema_editor):
    Document = apps.get_model("tracker", "Document")
    for document in Document.objects.filter(size_bytes__isnull=True).iterator():
        if not document.file:
            continue
        try:
            size = document.file.size
        except (OSError, ValueError):
            continue  # missing file, or a provider that did not answer
        Document.objects.filter(pk=document.pk).update(size_bytes=size)


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0005_slug_per_owner'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='size_bytes',
            field=models.PositiveBigIntegerField(blank=True, null=True, verbose_name='taille (octets)'),
        ),
        migrations.RunPython(record_sizes, migrations.RunPython.noop, elidable=True),
    ]
