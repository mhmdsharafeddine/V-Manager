from django import forms

from .models import Announcement


class AnnouncementComposeForm(forms.Form):
    title = forms.CharField(max_length=180, strip=True)
    body = forms.CharField(max_length=5000, strip=True)
    audience = forms.ChoiceField(choices=Announcement.AUDIENCE_CHOICES)
    priority = forms.ChoiceField(choices=Announcement.PRIORITY_CHOICES)
    send_push_notification = forms.BooleanField(required=False, initial=True)
    send_email_notification = forms.BooleanField(required=False, initial=True)
    send_sms_notification = forms.BooleanField(required=False, initial=False)
    pin_to_top = forms.BooleanField(required=False, initial=False)
