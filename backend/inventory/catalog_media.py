"""Narrow media read authorization for current inventory product thumbnails."""
from catalog.inventory_access import stock_skus


def asset_is_available_to_inventory_reader(asset_id):
    return stock_skus().filter(product__main_image_id=asset_id).exists()
