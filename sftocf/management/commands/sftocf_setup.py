from django.core.management.base import BaseCommand

from coldfront.core.resource.models import AttributeType, ResourceAttributeType


class Command(BaseCommand):
    help = 'Set up Coldfront configuration required by the sftocf plugin'

    def handle(self, *args, **options):
        text_attribute_type, _ = AttributeType.objects.get_or_create(name='Text')
        _, created = ResourceAttributeType.objects.get_or_create(
            name='starfish_name',
            defaults={'attribute_type': text_attribute_type},
        )
        if created:
            self.stdout.write(self.style.SUCCESS('Created ResourceAttributeType "starfish_name"'))
        else:
            self.stdout.write('ResourceAttributeType "starfish_name" already exists')
