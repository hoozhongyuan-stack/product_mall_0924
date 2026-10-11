from django.urls import path
from . import views
from . import code_views, cost_views, admin_batch

urlpatterns = [
    path('api/v1/admin/dining-alliance/restaurants/batch-status', admin_batch.batch_status),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/tables/batch', admin_batch.batch_tables),
    path('api/v1/admin/dining-alliance/product-ratios/batch', admin_batch.batch_ratios),
    path('api/v1/admin/dining-alliance/categories', admin_batch.categories),
    path('api/v1/admin/dining-alliance/restaurants', views.admin_restaurants),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>', views.admin_restaurants),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/tables', views.admin_tables),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/tables/<uuid:table_id>', views.admin_tables),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/tables/<uuid:table_id>/code', code_views.table_code_view),
    path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/staff', views.admin_staff),
    path('api/v1/admin/dining-alliance/product-ratios', views.admin_ratios),
    path('api/v1/admin/dining-alliance/purchase-costs', cost_views.costs_view),
    path('api/v1/admin/dining-alliance/order-stores', views.admin_order_stores),
    path('api/v1/admin/dining-alliance/orders', views.admin_orders),
    path('api/v1/admin/dining-alliance/orders/<uuid:order_id>', views.admin_orders),
    path('api/v1/app/dining-alliance/restaurants', views.member_restaurants),
    path('api/v1/app/dining-alliance/restaurants/<uuid:restaurant_id>/products', views.member_products),
    path('api/v1/app/dining-alliance/restaurants/<uuid:restaurant_id>/orders', views.member_orders),
    path('api/v1/app/dining-alliance/restaurants/<uuid:restaurant_id>/orders/<uuid:order_id>', views.member_orders),
    path('api/v1/app/dining-alliance/scan', views.scan_view),
    path('api/v1/app/dining-alliance/sessions/<uuid:session_id>', views.session_view),
    path('api/v1/app/dining-alliance/sessions/<uuid:session_id>/products', views.session_view, {'products':True}),
]
for prefix,view in [('admin',views.admin_finance),('app',views.member_finance)]:
    urlpatterns += [path(f'api/v1/{prefix}/dining-alliance/restaurants/<uuid:restaurant_id>/account',view),path(f'api/v1/{prefix}/dining-alliance/restaurants/<uuid:restaurant_id>/withdrawals',view,{'withdrawals':True})]
urlpatterns += [path('api/v1/admin/dining-alliance/restaurants/<uuid:restaurant_id>/withdrawals/<uuid:withdrawal_id>/<str:action>',views.admin_finance)]
