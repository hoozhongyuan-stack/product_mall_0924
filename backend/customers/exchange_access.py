"""Member-owned active address snapshot for points exchange orders."""
from .models import CustomerAddress


def exchange_address_snapshot(member,address_id):
    row=CustomerAddress.objects.filter(pk=address_id,member_id=member.id,active=True).first()
    if row is None:return None
    return {'recipientName':row.recipient_name,'phone':row.phone,'province':row.province,
            'city':row.city,'district':row.district,'detail':row.detail}
