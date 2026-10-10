from django.urls import path
from .store_views import orders_view
urlpatterns = [
    path('api/v1/app/store-center/stores/<uuid:store_id>/orders', orders_view),
    path('api/v1/app/store-center/stores/<uuid:store_id>/orders/<uuid:order_id>', orders_view),
    path('api/v1/app/store-center/stores/<uuid:store_id>/orders/<uuid:order_id>/fulfillment', orders_view),
]
from .store_views import aftersales_view
urlpatterns += [
    path('api/v1/app/store-center/stores/<uuid:store_id>/aftersales',aftersales_view),
    path('api/v1/app/store-center/stores/<uuid:store_id>/aftersales/<uuid:case_id>',aftersales_view),
    path('api/v1/app/store-center/stores/<uuid:store_id>/aftersales/<uuid:case_id>/notes',aftersales_view),
]
