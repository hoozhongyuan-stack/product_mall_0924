"""The only write path for SKU and aggregate product sale states."""

from django.db import transaction
from django.utils import timezone

from accounts.security import audit

from .models import Category, Product, Sku
from .validation import CatalogError


def change_sku_sale_status(request, actor, sku_id, expected_revision, status, action_code):
    if status not in Sku.SaleStatus.values:
        raise CatalogError("SKU 销售状态不正确。")
    with transaction.atomic():
        owner_id = Sku.objects.filter(id=sku_id).values_list("product_id", flat=True).first()
        if owner_id is None:
            raise CatalogError("SKU 不存在。", "NOT_FOUND", 404)
        # Every sale-state writer locks the parent first. SKU writers for the
        # same product therefore serialize before changing the aggregate.
        product = (Product.objects.select_related("category__parent")
                   .select_for_update(of=("self",)).filter(id=owner_id).first())
        if product is None:
            raise CatalogError("商品不存在。", "NOT_FOUND", 404)
        sku = (Sku.objects.select_related("current_unit")
               .select_for_update(of=("self",)).filter(id=sku_id).first())
        if sku is None:
            raise CatalogError("SKU 不存在。", "NOT_FOUND", 404)
        if sku.product_id != product.id:
            raise CatalogError("SKU 所属商品已变化。", "REVISION_CONFLICT", 409)
        if sku.revision != expected_revision:
            raise CatalogError("SKU 已被其他人修改。", "REVISION_CONFLICT", 409)
        if sku.sale_status == status:
            sku.product = product
            return sku
        if status == Sku.SaleStatus.ON_SALE:
            if product.fulfillment_kind == Product.Fulfillment.REDEEM and (
                    not product.redeem_valid_until or product.redeem_valid_until < timezone.localdate()):
                raise CatalogError("核销商品上架前须设置尚未到期且对用户可见的核销截止日期。", "REDEEM_VALIDITY_REQUIRED")
            if not product.main_image_id:
                raise CatalogError("上架 SKU 前须先设置商品主图。", "MEDIA_REQUIRED")
            category = product.category
            if (category.status != Category.Status.ACTIVE or not category.parent_id or
                    category.parent.status != Category.Status.ACTIVE):
                raise CatalogError("上架 SKU 前须启用商品所属分类。", "CATEGORY_INACTIVE", 409)
            if not sku.current_unit_id:
                raise CatalogError("上架 SKU 前须配置销售单位。", "UNIT_REQUIRED")
        before = {"saleStatus": sku.sale_status}
        sku.sale_status = status
        sku.revision += 1
        sku.save(update_fields=["sale_status", "revision", "updated_at"])

        has_live = Sku.objects.filter(product_id=product.id, sale_status=Sku.SaleStatus.ON_SALE).exists()
        ever_on_sale = product.ever_on_sale or has_live
        next_status = (Product.Status.ON_SALE if has_live else
                       Product.Status.OFF_SALE if ever_on_sale else Product.Status.DRAFT)
        if product.status != next_status or product.ever_on_sale != ever_on_sale:
            previous = {"status": product.status, "everOnSale": product.ever_on_sale}
            product.status = next_status
            product.ever_on_sale = ever_on_sale
            product.revision += 1
            product.save(update_fields=["status", "ever_on_sale", "revision", "updated_at"])
            audit(request, "product.sale_state.derived", "product", product.id, actor,
                  before=previous, after={"status": next_status, "everOnSale": ever_on_sale})
        audit(request, action_code, "sku", sku.id, actor, before=before,
              after={"saleStatus": status})
        sku.product = product
        return sku
