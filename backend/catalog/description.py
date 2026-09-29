"""Canonical detail HTML and FK-backed image bindings; client URLs are discarded."""
from dataclasses import dataclass

from .models import ProductDescriptionImage
from .validation import DescriptionSanitizer, description_parser


@dataclass(frozen=True)
class DescriptionDocument:
    html: str
    asset_ids: tuple


def parse_description(value):
    parser = description_parser(value)
    return DescriptionDocument(parser.value(), tuple(dict.fromkeys(parser.image_ids)))


def present_description(value, *, public=False):
    scope = "app" if public else "admin"
    parser = DescriptionSanitizer(lambda identifier: f"/api/v1/{scope}/assets/{identifier}/file")
    parser.feed(value)
    parser.close()
    return parser.value()


def set_description_images(product, identifiers):
    ProductDescriptionImage.objects.filter(product=product).exclude(asset_id__in=identifiers).delete()
    retained = set(product.description_images.values_list("asset_id", flat=True))
    ProductDescriptionImage.objects.bulk_create([
        ProductDescriptionImage(product=product, asset_id=identifier)
        for identifier in identifiers if identifier not in retained
    ])
