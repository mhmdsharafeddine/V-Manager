import os
import threading
from datetime import timedelta

from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    name = 'communication'

    def ready(self):
        import sys
        # Skip during management commands that don't need a scheduler
        _skip = {'migrate', 'makemigrations', 'check', 'collectstatic',
                 'shell', 'test', 'showmigrations', 'dbshell', 'createsuperuser'}
        if len(sys.argv) > 1 and sys.argv[1] in _skip:
            return
        # In runserver the reloader spawns two processes; start only in the child
        if 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return
        _schedule_next_digest()


def _seconds_until_9pm():
    from django.utils import timezone as tz
    now = tz.localtime(tz.now())
    target = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_digest():
    import logging
    from django.core.management import call_command
    logger = logging.getLogger(__name__)
    try:
        call_command('send_digest_emails')
        logger.info('Daily 9pm digest email job completed.')
    except Exception:
        logger.exception('Daily 9pm digest email job failed.')
    finally:
        _schedule_next_digest()


def _schedule_next_digest():
    secs = _seconds_until_9pm()
    t = threading.Timer(secs, _run_digest)
    t.daemon = True
    t.start()
