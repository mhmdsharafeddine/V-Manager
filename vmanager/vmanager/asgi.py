"""
ASGI config for vmanager project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vmanager.settings')

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from communication.routing import websocket_urlpatterns
from communication.ws_auth import JWTWebSocketAuthMiddlewareStack

django_asgi_application = get_asgi_application()

application = ProtocolTypeRouter(
	{
		"http": django_asgi_application,
		"websocket": JWTWebSocketAuthMiddlewareStack(
			AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
		),
	}
)
