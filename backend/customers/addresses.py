from .models import CustomerAddress


def member_owns_active_address(member, address_id):
    """Public read boundary for checkout and other customer-owned operations."""
    return CustomerAddress.objects.filter(id=address_id, member_id=member.id, active=True).exists()


def shipping_destination(member,address_id,*,lock=False):
    """Owned active address snapshot for authoritative shipping quote/order checks."""
    if not member or not address_id:return {}
    rows=CustomerAddress.objects.select_for_update() if lock else CustomerAddress.objects
    row=rows.filter(pk=address_id,member=member,active=True).first()
    if not row:return {}
    return dict(province=row.province,city=row.city,district=row.district,
                latitude=float(row.latitude) if row.latitude is not None else None,
                longitude=float(row.longitude) if row.longitude is not None else None,
                revision=row.revision,id=str(row.pk))
