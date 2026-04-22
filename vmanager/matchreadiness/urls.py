
from django.urls import path
from . import views

urlpatterns = [
    path('', views.match_readiness_page, name='match_readiness'),
]

