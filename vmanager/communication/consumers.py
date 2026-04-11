from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from team_management.models import TeamMembership

from .models import UserPresence, UserPresenceConnection

HEARTBEAT_TIMEOUT_SECONDS = getattr(settings, "COMMUNICATION_PRESENCE_HEARTBEAT_TIMEOUT_SECONDS", 45)


class TeamPresenceConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        team_id = await self._get_active_team_id(user.id)
        if team_id is None:
            await self.close(code=4403)
            return

        self.user_id = user.id
        self.team_id = team_id
        self.group_name = f"communication-team-{self.team_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self._mark_connected()
        await self._broadcast_presence_count()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

        if hasattr(self, "user_id"):
            await self._mark_disconnected()
            if hasattr(self, "group_name"):
                await self._broadcast_presence_count()

    async def receive_json(self, content, **kwargs):
        message_type = content.get("type")

        if message_type == "presence.ping":
            await self._mark_heartbeat()
            await self._broadcast_presence_count()
        elif message_type == "presence.request":
            await self._send_presence_count()

    async def presence_update(self, event):
        await self.send_json({
            "type": "presence.count",
            "online_now": event["online_now"],
        })

    async def _broadcast_presence_count(self):
        online_now = await self._count_live_online_members()
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "presence.update",
                "online_now": online_now,
            },
        )

    async def _send_presence_count(self):
        online_now = await self._count_live_online_members()
        await self.send_json(
            {
                "type": "presence.count",
                "online_now": online_now,
            }
        )

    @database_sync_to_async
    def _get_active_team_id(self, user_id):
        return (
            TeamMembership.objects.filter(
                user_id=user_id,
                is_active=True,
                status=TeamMembership.STATUS_APPROVED,
            )
            .values_list("team_id", flat=True)
            .first()
        )

    @database_sync_to_async
    def _mark_connected(self):
        now = timezone.now()
        UserPresence.objects.update_or_create(
            user_id=self.user_id,
            defaults={"last_seen": now},
        )
        UserPresenceConnection.objects.update_or_create(
            channel_name=self.channel_name,
            defaults={
                "user_id": self.user_id,
                "team_id": self.team_id,
                "last_heartbeat_at": now,
                "disconnected_at": None,
            },
        )

    @database_sync_to_async
    def _mark_heartbeat(self):
        now = timezone.now()
        UserPresence.objects.update_or_create(
            user_id=self.user_id,
            defaults={"last_seen": now},
        )
        updated = UserPresenceConnection.objects.filter(channel_name=self.channel_name).update(
            last_heartbeat_at=now,
            disconnected_at=None,
        )
        if not updated:
            UserPresenceConnection.objects.create(
                channel_name=self.channel_name,
                user_id=self.user_id,
                team_id=self.team_id,
                last_heartbeat_at=now,
            )

    @database_sync_to_async
    def _mark_disconnected(self):
        UserPresenceConnection.objects.filter(
            channel_name=self.channel_name,
            disconnected_at__isnull=True,
        ).update(disconnected_at=timezone.now())

    @database_sync_to_async
    def _count_live_online_members(self):
        cutoff = timezone.now() - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
        return (
            UserPresenceConnection.objects.filter(
                team_id=self.team_id,
                disconnected_at__isnull=True,
                last_heartbeat_at__gte=cutoff,
            )
            .values("user_id")
            .distinct()
            .count()
        )
