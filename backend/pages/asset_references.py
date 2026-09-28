"""Retention and paginated reference facts exposed to the material library."""
from django.db.models import Case, CharField, F, IntegerField, Value, When
from django.db.models.functions import Cast

from .models import PageDraftAsset, PageVersionAsset, StartupConfig, StartupConfigVersion


FIELDS = ("domain", "object_id", "label", "role", "ref_version", "state")


def reference_query(query, *, domain, object_id, label, role, version, state):
    return query.order_by().annotate(
        domain=Value(domain, output_field=CharField()),
        object_id=Cast(F(object_id), CharField()),
        label=F(label) if isinstance(label, str) else label,
        role=Value(role, output_field=CharField()),
        ref_version=F(version) if isinstance(version, str) else Value(version, output_field=IntegerField()),
        state=Value(state, output_field=CharField()) if isinstance(state, str) else state,
    ).values(*FIELDS)


def retained_asset_id_queries():
    return [PageDraftAsset.objects.values("asset_id"), PageVersionAsset.objects.values("asset_id"),
            StartupConfig.objects.filter(gif_asset__isnull=False).values("gif_asset_id"),
            StartupConfig.objects.filter(fallback_asset__isnull=False).values("fallback_asset_id"),
            StartupConfigVersion.objects.values("gif_asset_id"),
            StartupConfigVersion.objects.values("fallback_asset_id")]


def reference_queries(asset_id):
    queries = [reference_query(PageDraftAsset.objects.filter(asset_id=asset_id), domain="PAGE",
        object_id="page_id", label="page__name", role="COMPONENT", version="page__draft_revision", state="DRAFT"),
        reference_query(PageVersionAsset.objects.filter(asset_id=asset_id), domain="PAGE",
        object_id="version__page_id", label="version__name", role="COMPONENT", version="version__revision",
        state=Case(When(version__page__pagepublication__current_version_id=F("version_id"),
                        then=Value("CURRENT")), default=Value("HISTORY"), output_field=CharField()))]
    for field, role in (("gif_asset_id", "GIF"), ("fallback_asset_id", "FALLBACK")):
        queries.append(reference_query(StartupConfig.objects.filter(**{field: asset_id}), domain="STARTUP",
            object_id="id", label=Value("启动配置", output_field=CharField()), role=role,
            version="draft_revision", state="DRAFT"))
        queries.append(reference_query(StartupConfigVersion.objects.filter(**{field: asset_id}), domain="STARTUP",
            object_id="id", label=Value("启动配置", output_field=CharField()), role=role,
            version="revision", state=Case(When(startuppublication__current_version_id=F("id"),
            then=Value("CURRENT")), default=Value("HISTORY"), output_field=CharField())))
    return queries
