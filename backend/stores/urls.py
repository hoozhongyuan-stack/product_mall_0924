from django.urls import path
from . import views
from .finance_rules import profit_rules
from .finance_views import withdrawal_action
from .map_views import map_search
from .map_configuration import map_settings
from .settlement_settings import settlement_settings

urlpatterns=[
    path('api/v1/admin/stores',views.admin_stores),
    path('api/v1/admin/stores/profit-rules',profit_rules),
    path('api/v1/admin/stores/profit-rules/<uuid:sku_id>',profit_rules),
    path('api/v1/admin/stores/<uuid:store_id>/withdrawals/<uuid:withdrawal_id>/<str:action>',withdrawal_action),
    path('api/v1/admin/stores/accounts',views.admin_accounts),
    path('api/v1/admin/stores/map-search',map_search),
    path('api/v1/admin/stores/map-settings',map_settings),
    path('api/v1/admin/stores/settlement-settings',settlement_settings),
    path('api/v1/admin/stores/<uuid:store_id>',views.admin_stores),
    path('api/v1/admin/stores/<uuid:store_id>/staff',views.admin_staff),
    path('api/v1/admin/stores/<uuid:store_id>/account',views.admin_accounts),
    path('api/v1/app/stores',views.public_stores),
    path('api/v1/app/stores/<uuid:store_id>',views.public_stores),
    path('api/v1/app/store-center/stores',views.member_stores),
    path('api/v1/app/store-center/stores/<uuid:store_id>/products',views.member_products),
    path('api/v1/app/store-center/stores/<uuid:store_id>/products/<uuid:product_id>',views.member_products),
    path('api/v1/app/store-center/stores/<uuid:store_id>/account',views.member_account),
    path('api/v1/app/store-center/stores/<uuid:store_id>/withdrawals',views.member_account),
]
