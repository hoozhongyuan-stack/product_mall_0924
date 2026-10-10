"""Named store read and authorization boundary used by checkout and fulfillment."""
from .models import Store, StoreStaff, StoreProduct


class StoreError(ValueError):
    def __init__(self,message,code='STORE_INVALID',status=400):
        super().__init__(message)
        self.code,self.status = code,status


def get_store(store_id, for_sale=False):
    from django.core.exceptions import ValidationError
    try:
        store = Store.objects.select_related('warehouse').filter(pk=store_id).first()
    except (ValidationError,ValueError,TypeError) as exc:
        raise StoreError('前置仓编号无效。') from exc
    if store is None:
        raise StoreError('前置仓不存在。','STORE_NOT_FOUND',404)
    if for_sale and (not store.enabled or not store.accepting_orders or not store.warehouse.enabled):
        raise StoreError('该前置仓暂不接受新订单，请切换前置仓。','STORE_UNAVAILABLE',409)
    return store


def require_staff(member,store_id,permission='orders'):
    store = get_store(store_id)
    membership = StoreStaff.objects.filter(store=store,member=member,enabled=True,member__enabled=True,member__auth_version=member.auth_version).first()
    if not membership or permission not in membership.permissions:
        raise StoreError('没有该前置仓的操作权限。','STORE_FORBIDDEN',403)
    return store


def sale_product_ids(store):
    return StoreProduct.objects.filter(store=store,on_sale=True,product__status='ON_SALE').values_list('product_id',flat=True)


def available_base_units(store,sku_ids):
    from inventory.store_access import available_base_units as read
    return read(store,sku_ids)
