"""Read-only, human-readable SKU specifications for cross-domain summaries."""
from django.db.models import Prefetch
from .models import SkuSpecSelection


def specification_prefetch():
    return Prefetch('selections',queryset=SkuSpecSelection.objects.select_related('axis','option').order_by('axis__sort_order','axis_id'))


def specification_label(sku):
    selections=getattr(sku,'_prefetched_objects_cache',{}).get('selections')
    if selections is None:
        selections=sku.selections.select_related('axis','option').order_by('axis__sort_order','axis_id')
    return ' / '.join(f'{selection.axis.name}：{selection.option.value}' for selection in selections) or '默认规格'
