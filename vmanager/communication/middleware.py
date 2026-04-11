from django.utils import timezone

from .models import UserPresence


class LastSeenMiddleware:
    SESSION_KEY = "communication_last_seen_ts"
    UPDATE_INTERVAL_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return response

        now = timezone.now()
        last_seen_ts = request.session.get(self.SESSION_KEY)
        should_update = True
        if isinstance(last_seen_ts, (int, float)):
            should_update = (now.timestamp() - float(last_seen_ts)) >= self.UPDATE_INTERVAL_SECONDS

        if should_update:
            UserPresence.objects.update_or_create(user=user, defaults={"last_seen": now})
            request.session[self.SESSION_KEY] = int(now.timestamp())

        return response
