import uuid

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Upper


class Category(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "category"
        ordering = ["parent_id", "sort_order", "name"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(id=models.F("parent_id")), name="category_not_self_parent"),
        ]


class Product(models.Model):
    class Fulfillment(models.TextChoices):
        SHIP = "SHIP", "Ship"
        REDEEM = "REDEEM", "Redeem"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ON_SALE = "ON_SALE", "On sale"
        OFF_SALE = "OFF_SALE", "Off sale"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_no = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    fulfillment_kind = models.CharField(max_length=6, choices=Fulfillment.choices)
    redeem_valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    unit_conversion = models.JSONField(null=True, blank=True, default=None)
    ever_on_sale = models.BooleanField(default=False)
    manually_off_sale = models.BooleanField(default=False)
    description_html = models.TextField(blank=True)
    main_image = models.ForeignKey("Asset", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="main_for_products")
    video = models.ForeignKey("Asset", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="video_for_products")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product"
        indexes = [models.Index(fields=["category", "status"], name="product_cat_status_idx")]
        constraints = [
            models.UniqueConstraint(Upper("product_no"), name="unique_product_no_upper"),
            models.CheckConstraint(
                condition=(models.Q(status="DRAFT", ever_on_sale=False) |
                           models.Q(status__in=["ON_SALE", "OFF_SALE"], ever_on_sale=True)),
                name="product_sale_lifecycle_consistent",
            ),
        ]


class SpecAxis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="spec_axes")
    name = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "product_spec_axis"
        ordering = ["sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["product", "sort_order"], name="unique_product_axis_order")]


class SpecOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    axis = models.ForeignKey(SpecAxis, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "product_spec_option"
        ordering = ["sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["axis", "sort_order"], name="unique_axis_option_order")]


class Sku(models.Model):
    class SaleStatus(models.TextChoices):
        ON_SALE = "ON_SALE", "On sale"
        OFF_SALE = "OFF_SALE", "Off sale"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="skus")
    sku_code = models.CharField(max_length=64)
    spec_key = models.CharField(max_length=150)
    list_price_fen = models.BigIntegerField(validators=[MinValueValidator(0)])
    sale_status = models.CharField(max_length=8, choices=SaleStatus.choices, default=SaleStatus.OFF_SALE)
    current_unit = models.ForeignKey("SkuUnitVersion", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="current_for")
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sku"
        indexes = [models.Index(fields=["product", "sale_status"], name="sku_prod_status_idx")]
        constraints = [
            models.UniqueConstraint(Upper("sku_code"), name="unique_sku_code_upper"),
            models.UniqueConstraint(fields=["product", "spec_key"], name="unique_product_spec_key"),
            models.CheckConstraint(condition=models.Q(list_price_fen__gte=0), name="sku_price_nonnegative"),
        ]


class SkuSpecSelection(models.Model):
    sku = models.ForeignKey(Sku, on_delete=models.CASCADE, related_name="selections")
    axis = models.ForeignKey(SpecAxis, on_delete=models.PROTECT)
    option = models.ForeignKey(SpecOption, on_delete=models.PROTECT)

    class Meta:
        db_table = "sku_spec_selection"
        constraints = [models.UniqueConstraint(fields=["sku", "axis"], name="unique_sku_axis_selection")]


class MemberGrade(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=40)
    rank = models.PositiveSmallIntegerField(unique=True)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "member_grade"
        ordering = ["rank"]


class SkuGradePrice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.ForeignKey(Sku, on_delete=models.PROTECT, related_name="grade_prices")
    grade = models.ForeignKey(MemberGrade, on_delete=models.PROTECT)
    price_fen = models.BigIntegerField(validators=[MinValueValidator(0)])
    effective_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "sku_grade_price"
        constraints = [
            models.UniqueConstraint(fields=["sku", "grade"], condition=models.Q(active=True),
                                    name="unique_active_sku_grade_price"),
            models.CheckConstraint(condition=models.Q(price_fen__gte=0), name="sku_grade_price_nonnegative"),
        ]


class SkuUnitVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.ForeignKey(Sku, on_delete=models.PROTECT, related_name="unit_versions")
    base_unit = models.CharField(max_length=30)
    sale_unit = models.CharField(max_length=30)
    ratio = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    effective_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sku_unit_version"
        constraints = [models.CheckConstraint(condition=models.Q(ratio__gte=1), name="sku_unit_ratio_positive")]


class BatchCategoryRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.UUIDField()
    key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    result = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "catalog_batch_category_request"
        constraints = [models.UniqueConstraint(fields=["actor_id", "key"], name="unique_batch_category_key")]


class Asset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "IMAGE", "Image"
        VIDEO = "VIDEO", "Video"
        GIF = "GIF", "Animated GIF"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=5, choices=Kind.choices)
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64)
    original_name = models.CharField(max_length=120)
    stored_name = models.CharField(max_length=255, unique=True)
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "media_asset"
        indexes = [
            models.Index(fields=["-created_at", "-id"], name="asset_created_page_idx"),
            models.Index(fields=["kind", "-created_at"], name="asset_kind_created_idx"),
            models.Index(fields=["created_by", "-created_at"], name="asset_owner_created_idx"),
        ]


class ProductGalleryImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery_images")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    position = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "product_gallery_image"
        constraints = [
            models.UniqueConstraint(fields=["product", "position"], name="unique_product_gallery_position"),
            models.UniqueConstraint(fields=["product", "asset"], name="unique_product_gallery_asset"),
        ]


class ProductDescriptionImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="description_images")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)

    class Meta:
        db_table = "product_description_image"
        constraints = [models.UniqueConstraint(fields=["product", "asset"],
                                               name="unique_product_description_asset")]
