from .models import CustomerAddress


def member_owns_active_address(member, address_id):
    """Public read boundary for checkout and other customer-owned operations."""
    return CustomerAddress.objects.filter(id=address_id, member_id=member.id, active=True).exists()
