"""The only write path for SKU and aggregate product sale states."""

from django.db import transaction
from django.utils import timezone

from accounts.security import audit, operation_permissions, require_live

from .models import Category, Product, Sku
from .validation import CatalogError


def _revalidate_sale_actor(request, actor):
    """Refresh both sale permissions after acquiring the product row lock."""
    if not hasattr(request, "user"):
        granted = set(operation_permissions(actor))
        if not granted:
            raise CatalogError("请重新登录。", "SESSION_EXPIRED", 401)
        if not {"catalog.write", "sku.status.write"} <= granted:
            raise CatalogError("当前账号没有此操作权限。", "PERMISSION_DENIED", 403)
        return actor
    fresh = None
    for code in ("catalog.write", "sku.status.write"):
        fresh, bad = require_live(request, code)
        if bad is not None:
            if bad.status_code == 401:
                raise CatalogError("请重新登录。", "SESSION_EXPIRED", 401)
            raise CatalogError("当前账号没有此操作权限。", "PERMISSION_DENIED", 403)
    return fresh


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
        # SKU-only writers may lack catalog.write; keep their established
        # permission contract while the parent lock serializes sale changes.
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
        next_status = (Product.Status.OFF_SALE if product.manually_off_sale else
                       Product.Status.ON_SALE if has_live else
                       Product.Status.OFF_SALE if ever_on_sale else Product.Status.DRAFT)
        aggregate_changed = product.status != next_status or product.ever_on_sale != ever_on_sale
        if aggregate_changed:
            previous = {"status": product.status, "everOnSale": product.ever_on_sale}
            product.status = next_status
            product.ever_on_sale = ever_on_sale
        # The SPU revision also protects a batch preview's SKU selection. A
        # manually hidden SPU keeps OFF_SALE while SKU switches still change.
        product.revision += 1
        fields = ["revision", "updated_at"]
        if aggregate_changed:
            fields.extend(["status", "ever_on_sale"])
        product.save(update_fields=fields)
        if aggregate_changed:
            audit(request, "product.sale_state.derived", "product", product.id, actor,
                  before=previous, after={"status": next_status, "everOnSale": ever_on_sale})
        audit(request, action_code, "sku", sku.id, actor, before=before,
              after={"saleStatus": status})
        sku.product = product
        return sku


def validate_product_publish(product, selected_skus):
    """Shared publication prerequisites for SPU writes and their preview."""
    if not selected_skus:
        raise CatalogError("请先选择至少一个已配置销售单位的 SKU。", "SKU_REQUIRED")
    if not product.main_image_id:
        raise CatalogError("上架商品前须先设置商品主图。", "MEDIA_REQUIRED")
    category = product.category
    if (category.status != Category.Status.ACTIVE or not category.parent_id or
            category.parent.status != Category.Status.ACTIVE):
        raise CatalogError("上架商品前须启用商品所属分类。", "CATEGORY_INACTIVE", 409)
    if product.fulfillment_kind == Product.Fulfillment.REDEEM and (
            not product.redeem_valid_until or product.redeem_valid_until < timezone.localdate()):
        raise CatalogError("核销商品上架前须设置尚未到期的核销截止日期。", "REDEEM_VALIDITY_REQUIRED")
    if any(not sku.current_unit_id for sku in selected_skus):
        raise CatalogError("上架的 SKU 须配置销售单位。", "UNIT_REQUIRED")


def change_product_sale_status(request, actor, product_id, expected_revision, status, initial_sku_ids=()):
    """One SPU is one transaction; SKU switches remain independent preferences."""
    if status not in (Product.Status.ON_SALE, Product.Status.OFF_SALE):
        raise CatalogError("商品销售状态不正确。")
    with transaction.atomic():
        product = (Product.objects.select_related("category__parent")
                   .select_for_update(of=("self",)).filter(id=product_id).first())
        if not product:
            raise CatalogError("商品不存在。", "NOT_FOUND", 404)
        actor = _revalidate_sale_actor(request, actor)
        if product.revision != expected_revision:
            raise CatalogError("商品已被其他人修改。", "REVISION_CONFLICT", 409)
        if status == Product.Status.OFF_SALE:
            if product.status == Product.Status.DRAFT:
                raise CatalogError("草稿商品尚未上架。", "DRAFT_NOT_PUBLISHED")
            if initial_sku_ids:
                raise CatalogError("下架商品时不能提供首次上架 SKU。")
            if product.manually_off_sale:
                return product
            previous = {"status": product.status, "manuallyOffSale": False}
            product.manually_off_sale = True
            product.status = Product.Status.OFF_SALE
        else:
            if product.status == Product.Status.ON_SALE and not product.manually_off_sale:
                if initial_sku_ids:
                    raise CatalogError("在售商品不能重新选择首次上架 SKU。")
                return product
            skus = list(Sku.objects.select_related("current_unit").filter(product_id=product.id).order_by("id"))
            if product.status == Product.Status.DRAFT:
                if not initial_sku_ids or len(initial_sku_ids) != len(set(initial_sku_ids)):
                    raise CatalogError("草稿首次上架须选择至少一个不重复的 SKU。", "SKU_REQUIRED")
                selected = [sku for sku in skus if sku.id in initial_sku_ids]
                if len(selected) != len(initial_sku_ids):
                    raise CatalogError("所选 SKU 不属于此商品。", "SKU_NOT_FOUND")
            else:
                if initial_sku_ids:
                    raise CatalogError("非草稿商品不能重新选择首次上架 SKU。")
                selected = [sku for sku in skus if sku.sale_status == Sku.SaleStatus.ON_SALE]
            validate_product_publish(product, selected)
            previous = {"status": product.status, "manuallyOffSale": product.manually_off_sale}
            if product.status == Product.Status.DRAFT:
                for sku in selected:
                    if sku.sale_status != Sku.SaleStatus.ON_SALE:
                        sku.sale_status = Sku.SaleStatus.ON_SALE
                        sku.revision += 1
                        sku.save(update_fields=["sale_status", "revision", "updated_at"])
                        audit(request, "sku.status.product-first-publish", "sku", sku.id, actor,
                              before={"saleStatus": Sku.SaleStatus.OFF_SALE},
                              after={"saleStatus": Sku.SaleStatus.ON_SALE})
            product.manually_off_sale = False
            product.status = Product.Status.ON_SALE
            product.ever_on_sale = True
        product.revision += 1
        product.save(update_fields=["status", "manually_off_sale", "ever_on_sale", "revision", "updated_at"])
        audit(request, "product.sale_status", "product", product.id, actor, before=previous,
              after={"status": product.status, "manuallyOffSale": product.manually_off_sale})
        return product
