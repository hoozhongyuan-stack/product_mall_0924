from django.urls import path
from . import admin_views
urlpatterns = [
    path('config',admin_views.config_view),path('templates',admin_views.templates_view),
    path('templates/<uuid:identifier>',admin_views.templates_view),
    path('zones',admin_views.zones_view),path('zones/<uuid:identifier>',admin_views.zones_view),
    path('bulk',admin_views.bulk_view),path('weights',admin_views.weights_view),
    path('trial',admin_views.trial_view),path('regions',admin_views.regions_view),
    path('targets',admin_views.targets_view),path('stores',admin_views.stores_view),path('stores/<uuid:identifier>',admin_views.stores_view),
]
