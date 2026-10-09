from django.db import IntegrityError, transaction

from accounts.security import audit

from .description import parse_description, set_description_images
from .media import resolve_media, set_gallery
from .models import (Category, MemberGrade, Product, Sku, SkuGradePrice, SkuSpecSelection,
                     SkuUnitVersion, SpecAxis, SpecOption)
from .validation import (CatalogError, code_field, list_field, number_field,
                         object_field, redeem_valid_until, text_field, uuid_field)


def active_leaf(category_id):
    category = Category.objects.select_for_update().filter(id=uuid_field(category_id, "分类 ID")).first()
    if not category or not category.parent_id or category.status != Category.Status.ACTIVE:
        raise CatalogError("请选择已启用的二级分类。")
    parent = Category.objects.select_for_update().get(pk=category.parent_id)
    if parent.status != Category.Status.ACTIVE:
        raise CatalogError("所属一级分类已停用。")
    return category


def parse_axes(raw_axes):
    axes = []
    option_by_key = {}
    axis_keys = set()
    axis_names = set()
    for index, raw_axis in enumerate(list_field(raw_axes, "规格项", 2)):
        raw_axis = object_field(raw_axis, f"规格项 {index + 1}")
        if set(raw_axis) - {"clientKey", "name", "sortOrder", "options"}:
            raise CatalogError("规格项含有不支持的字段。")
        key = text_field(raw_axis.get("clientKey"), "规格项标识", 80)
        name = text_field(raw_axis.get("name"), "规格项名称", 60)
        order = number_field(raw_axis.get("sortOrder"), "规格项顺序", 0, 100)
        if key in axis_keys or name.casefold() in axis_names or any(item[2] == order for item in axes):
            raise CatalogError("规格项标识、名称及顺序不能重复。")
        axis_keys.add(key)
        axis_names.add(name.casefold())
        options = []
        values = set()
        orders = set()
        for raw_option in list_field(raw_axis.get("options"), "规格值", 20, 1):
            raw_option = object_field(raw_option, "规格值")
            if set(raw_option) - {"clientKey", "value", "sortOrder"}:
                raise CatalogError("规格值含有不支持的字段。")
            option_key = text_field(raw_option.get("clientKey"), "规格值标识", 80)
            value = text_field(raw_option.get("value"), "规格值", 60)
            option_order = number_field(raw_option.get("sortOrder"), "规格值顺序", 0, 100)
            if option_key in option_by_key or value.casefold() in values or option_order in orders:
                raise CatalogError("同一规格项的值、顺序及规格值标识不能重复。")
            values.add(value.casefold())
            orders.add(option_order)
            options.append((option_key, value, option_order))
            option_by_key[option_key] = key
        axes.append((key, name, order, options))
    return axes, option_by_key


def parse_unit(raw):
    raw = object_field(raw, "单位换算")
    if set(raw) - {"baseUnit", "saleUnit", "ratio"}:
        raise CatalogError("单位换算含有不支持的字段。")
    return {
        "base_unit": text_field(raw.get("baseUnit"), "基本单位", 30),
        "sale_unit": text_field(raw.get("saleUnit"), "销售单位", 30),
        "ratio": number_field(raw.get("ratio"), "换算比例", 1, 1000000000),
    }


def parse_prices(raw, enabled_grades):
    prices = []
    used = set()
    for item in list_field(raw, "等级价", 100):
        item = object_field(item, "等级价")
        if set(item) - {"gradeId", "priceFen"}:
            raise CatalogError("等级价含有不支持的字段。")
        grade_id = uuid_field(item.get("gradeId"), "等级 ID")
        if grade_id not in enabled_grades or grade_id in used:
            raise CatalogError("会员等级不存在、已停用或重复。")
        used.add(grade_id)
        prices.append((grade_id, number_field(item.get("priceFen"), "等级价", 0, 2**53 - 1)))
    return prices


def parse_skus(raw_skus, axes, option_by_key, conversion=None, raw_axes=None):
    from .unit_conversion import derived_unit
    enabled_grades = set(MemberGrade.objects.filter(enabled=True).values_list("id", flat=True))
    skus = []
    codes = set()
    combos = set()
    axis_keys = {axis[0] for axis in axes}
    for index, raw in enumerate(list_field(raw_skus, "SKU", 100, 1)):
        raw = object_field(raw, f"SKU {index + 1}")
        if set(raw) - {"skuCode", "specOptionKeys", "listPriceFen", "saleStatus", "gradePrices", "unit"}:
            raise CatalogError("SKU 含有不支持的字段。")
        code = code_field(raw.get("skuCode"), "SKU 编码")
        option_keys = list_field(raw.get("specOptionKeys"), "SKU 规格选择", 2)
        if any(not isinstance(key, str) or key not in option_by_key for key in option_keys):
            raise CatalogError("SKU 规格值不属于此商品。")
        selected_axes = [option_by_key[key] for key in option_keys]
        if len(option_keys) != len(axes) or set(selected_axes) != axis_keys:
            raise CatalogError("每个 SKU 必须从每项规格各选一个值。")
        combo = tuple(sorted(option_keys)) if axes else ("single",)
        if code.casefold() in codes or combo in combos:
            raise CatalogError("SKU 编码或规格组合重复。")
        codes.add(code.casefold())
        combos.add(combo)
        status = raw.get("saleStatus")
        if status not in Sku.SaleStatus.values:
            raise CatalogError("SKU 销售状态不正确。")
        skus.append({
            "code": code, "option_keys": option_keys,
            "price": number_field(raw.get("listPriceFen"), "日常价", 0, 2**53 - 1),
            "status": status,
            "grade_prices": parse_prices(raw.get("gradePrices", []), enabled_grades),
            "unit": derived_unit(conversion, raw_axes or [], option_keys, raw.get("unit")),
        })
    return skus


def create_product(request, actor, body):
    allowed = {"productNo", "name", "categoryId", "fulfillmentKind", "status", "descriptionHtml",
               "specAxes", "skus", "unitConversion", "mainImageAssetId", "galleryAssetIds", "videoAssetId", "redeemValidUntil"}
    if set(body) - allowed:
        raise CatalogError("商品请求含有不支持的字段。")
    product_no = code_field(body.get("productNo"), "商品编号")
    name = text_field(body.get("name"), "商品名称", 120)
    fulfillment = body.get("fulfillmentKind")
    if fulfillment not in Product.Fulfillment.values:
        raise CatalogError("履约类型不正确。")
    valid_until = redeem_valid_until(body.get("redeemValidUntil"), fulfillment)
    if body.get("status", "DRAFT") != "DRAFT":
        raise CatalogError("新商品必须先保存为草稿。")
    document = parse_description(body.get("descriptionHtml", ""))
    axes, option_by_key = parse_axes(body.get("specAxes", []))
    from .unit_conversion import parse_conversion, persisted_conversion, grouped_rows
    conversion = parse_conversion(body.get("unitConversion"), body.get("specAxes", []))
    skus = parse_skus(body.get("skus"), axes, option_by_key, conversion, body.get("specAxes", []))
    if conversion is None:
        from .unit_conversion import validate_single_unit
        for item in skus:
            validate_single_unit(item["unit"])
    if any(item["status"] == Sku.SaleStatus.ON_SALE for item in skus):
        raise CatalogError("新商品须先保存为草稿，再从 SKU 列表上架。")
    with transaction.atomic():
        category = active_leaf(body.get("categoryId"))
        main_image, gallery, video = resolve_media(body, description_ids=document.asset_ids)
        from .asset_access import authorize_asset_binding
        from .media import revalidate_asset_actor
        for code in ("catalog.write", "sku.price.write", "sku.status.write", "sku.unit.write"):
            actor = revalidate_asset_actor(request, code, actor)
        authorize_asset_binding(actor, [*document.asset_ids,
                *[asset.id for asset in [main_image, *gallery, video] if asset]])
        if Product.objects.filter(product_no__iexact=product_no).exists():
            raise CatalogError("商品编号已存在。", "PRODUCT_NO_DUPLICATE", 409)
        if Sku.objects.filter(sku_code__in=[item["code"] for item in skus]).exists() or any(
            Sku.objects.filter(sku_code__iexact=item["code"]).exists() for item in skus
        ):
            raise CatalogError("SKU 编码已存在。", "SKU_CODE_DUPLICATE", 409)
        try:
            product = Product.objects.create(product_no=product_no, name=name, category=category,
                fulfillment_kind=fulfillment, redeem_valid_until=valid_until, description_html=document.html,
                main_image=main_image, video=video)
            set_gallery(product, gallery)
            set_description_images(product, document.asset_ids)
            options = {}
            axis_ids = {}
            for key, axis_name, order, option_rows in axes:
                axis = SpecAxis.objects.create(product=product, name=axis_name, sort_order=order)
                axis_ids[key] = axis.id
                for option_key, value, option_order in option_rows:
                    options[option_key] = SpecOption.objects.create(axis=axis, value=value, sort_order=option_order)
            created_skus = []
            for item in skus:
                selection = [options[key] for key in item["option_keys"]]
                spec_key = ":".join(sorted(str(option.id) for option in selection)) if selection else "single"
                sku = Sku.objects.create(product=product, sku_code=item["code"], spec_key=spec_key,
                    list_price_fen=item["price"], sale_status=item["status"])
                SkuSpecSelection.objects.bulk_create([
                    SkuSpecSelection(sku=sku, axis=option.axis, option=option) for option in selection
                ])
                version = SkuUnitVersion.objects.create(sku=sku, **item["unit"])
                sku.current_unit = version
                sku.save(update_fields=["current_unit", "updated_at"])
                SkuGradePrice.objects.bulk_create([
                    SkuGradePrice(sku=sku, grade_id=grade_id, price_fen=price)
                    for grade_id, price in item["grade_prices"]
                ])
                created_skus.append(sku)
            if conversion:
                from inventory.pool_access import configure_unit_groups
                product.unit_conversion = persisted_conversion(conversion, axis_ids, {key: option.id for key, option in options.items()})
                product.save(update_fields=["unit_conversion"])
                rows = [{"sku": sku, "selection": [(options[key].axis_id, options[key].id) for key in item["option_keys"]]}
                        for sku, item in zip(created_skus, skus)]
                configure_unit_groups(product.unit_conversion, grouped_rows(product.unit_conversion, rows))
            audit(request, "product.create", "product", product.id, actor,
                  after={"productNo": product_no, "unitConversion": product.unit_conversion, "skuIds": [str(item.id) for item in created_skus],
                         "mainImageAssetId": str(main_image.id) if main_image else None,
                         "galleryAssetIds": [str(asset.id) for asset in gallery],
                         "descriptionImageAssetIds": [str(identifier) for identifier in document.asset_ids],
                         "videoAssetId": str(video.id) if video else None})
        except IntegrityError as exc:
            raise CatalogError("商品编号或 SKU 编码已存在。", "CATALOG_CONFLICT", 409) from exc
    return {"productId": str(product.id), "productRevision": product.revision,
            "skus": [{"skuId": str(item.id), "skuRevision": item.revision} for item in created_skus]}
