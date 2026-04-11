from django.urls import path

from .consumers import TeamPresenceConsumer

websocket_urlpatterns = [
    path("ws/communication/presence/", TeamPresenceConsumer.as_asgi()),
]
