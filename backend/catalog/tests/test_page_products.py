from .test_public_browsing import PublicBrowsingTests
from catalog.page_products import hydrate_products


class PageProductTests(PublicBrowsingTests):
    def test_page_products_are_current_public_bounded_and_sorted(self):
        root = self.category('商品')
        category = self.category('分类', parent=root)
        image = self.image()
        first, sku = self.product(category, 'CARD-1', main=image, price=3500)
        second, _ = self.product(category, 'CARD-2', main=image, price=1200)
        hidden, _ = self.product(category, 'CARD-HIDDEN', main=image, status='DRAFT')
        props = {'source': 'CATEGORY', 'productIds': [],
                 'categoryId': str(root.id), 'limit': 1, 'sort': 'PRICE_ASC', 'layout': 'GRID'}
        cards = hydrate_products(props)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['productId'], str(second.id))
        self.assertEqual(cards[0]['priceFen'], 1200)
        self.assertFalse(cards[0]['purchasable'])
        sku.list_price_fen = 600
        sku.save(update_fields=['list_price_fen'])
        self.assertEqual(hydrate_products(props)[0]['priceFen'], 600)
        self.assertEqual(hydrate_products({**props, 'source': 'CATEGORY', 'categoryId': str(root.id), 'productIds': []})[0]['productId'], str(first.id))
        root.status = 'INACTIVE'
        root.save(update_fields=['status'])
        self.assertEqual(hydrate_products(props), [])

    def test_manual_cards_preserve_configured_order_before_limit(self):
        root = self.category('手选商品')
        leaf = self.category('手选分类', parent=root)
        image = self.image()
        first, _ = self.product(leaf, 'ORDER-1', main=image, price=3000)
        second, _ = self.product(leaf, 'ORDER-2', main=image, price=1000)
        props = {'source': 'MANUAL', 'productIds': [str(first.id), str(second.id)],
                 'categoryId': '', 'limit': 1, 'sort': 'PRICE_ASC', 'layout': 'GRID'}
        self.assertEqual(hydrate_products(props)[0]['productId'], str(first.id))
        self.assertEqual(hydrate_products({**props, 'limit': 2, 'sort': 'NEWEST'})[1]['productId'], str(second.id))
