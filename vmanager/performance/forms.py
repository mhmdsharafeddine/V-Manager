from django import forms

from accounts.models import AccountProfile
from scheduling.models import EventAttendance
from team_management.models import TeamMembership

from .models import TeamPerformanceRecord


class TeamPerformanceRecordForm(forms.ModelForm):
    @staticmethod
    def _participation_from_attendance(event, member):
        if event is None or member is None:
            return TeamPerformanceRecord.PARTICIPATION_PRESENT

        attendance = EventAttendance.objects.filter(
            event=event,
            player_id=member.user_id,
        ).first()
        if attendance is None:
            return TeamPerformanceRecord.PARTICIPATION_PRESENT

        if attendance.status == EventAttendance.STATUS_ATTENDING:
            return TeamPerformanceRecord.PARTICIPATION_PRESENT
        if attendance.status == EventAttendance.STATUS_NOT_ATTENDING:
            if attendance.not_attending_reason == EventAttendance.REASON_INJURED:
                return TeamPerformanceRecord.PARTICIPATION_INJURED
            return TeamPerformanceRecord.PARTICIPATION_DID_NOT_ATTEND
        if attendance.status == EventAttendance.STATUS_MAYBE:
            return TeamPerformanceRecord.PARTICIPATION_ABSENT
        return TeamPerformanceRecord.PARTICIPATION_PRESENT

    class Meta:
        model = TeamPerformanceRecord
        fields = [
            "event",
            "member",
            "result",
            "participation_status",
            "injury_status",
            "points_scored",
            "points_conceded",
            "target_score",
            "target_achieved",
            "kills",
            "aces",
            "blocks",
            "assists",
            "digs",
            "unforced_errors",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

        if team is not None:
            event_queryset = self.fields["event"].queryset.filter(team=team).order_by("-scheduled_at")
            self.fields["member"].queryset = (
                TeamMembership.objects.select_related("user", "user__profile")
                .filter(
                    team=team,
                    status=TeamMembership.STATUS_APPROVED,
                    is_active=True,
                    user__profile__role=AccountProfile.ROLE_PLAYER,
                )
                .order_by("user__first_name", "user__last_name", "user__email")
            )

            selected_member_id = None
            if self.is_bound:
                raw_member_id = self.data.get("member")
                selected_member_id = str(raw_member_id).strip() if raw_member_id not in (None, "") else None
            elif self.initial.get("member"):
                selected_member_id = str(self.initial.get("member"))
            elif getattr(self.instance, "member_id", None):
                selected_member_id = str(self.instance.member_id)

            if selected_member_id and selected_member_id.isdigit():
                used_event_ids_qs = TeamPerformanceRecord.objects.filter(
                    team=team,
                    member_id=int(selected_member_id),
                )
                if self.instance and self.instance.pk:
                    used_event_ids_qs = used_event_ids_qs.exclude(pk=self.instance.pk)

                used_event_ids = list(used_event_ids_qs.values_list("event_id", flat=True))
                if self.instance and self.instance.event_id in used_event_ids:
                    used_event_ids.remove(self.instance.event_id)

                event_queryset = event_queryset.exclude(id__in=used_event_ids)

            self.fields["event"].queryset = event_queryset
            self.fields["event"].empty_label = "Select an event"

            has_initial_event = bool(self.initial.get("event") or getattr(self.instance, "event_id", None))
            if not self.is_bound and not has_initial_event:
                self.initial["event"] = ""

        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"team-input {css}".strip()

        self.fields["target_achieved"].widget.attrs["class"] = "team-checkbox"
        self.fields["injury_status"].choices = [("", "Select Injury Status"), *TeamPerformanceRecord.INJURY_STATUS_CHOICES]
        self.fields["participation_status"].disabled = True
        self.fields["participation_status"].help_text = "Auto-synced from this player's schedule RSVP for the selected event."

        selected_event = None
        selected_member = None
        if self.is_bound:
            raw_event_id = str(self.data.get("event") or "").strip()
            raw_member_id = str(self.data.get("member") or "").strip()
            if raw_event_id.isdigit():
                selected_event = self.fields["event"].queryset.filter(pk=int(raw_event_id)).first()
            if raw_member_id.isdigit():
                selected_member = self.fields["member"].queryset.filter(pk=int(raw_member_id)).first()
        else:
            selected_event = self.initial.get("event") or getattr(self.instance, "event", None)
            selected_member = self.initial.get("member") or getattr(self.instance, "member", None)
            if isinstance(selected_event, str) and selected_event.isdigit():
                selected_event = self.fields["event"].queryset.filter(pk=int(selected_event)).first()
            if isinstance(selected_member, str) and selected_member.isdigit():
                selected_member = self.fields["member"].queryset.filter(pk=int(selected_member)).first()

        auto_participation = self._participation_from_attendance(selected_event, selected_member)
        self.initial["participation_status"] = auto_participation
        self.fields["participation_status"].initial = auto_participation

    def clean(self):
        cleaned = super().clean()
        event = cleaned.get("event")
        member = cleaned.get("member")

        if self.team is None:
            raise forms.ValidationError("Team context is required.")

        if event and event.team_id != self.team.id:
            self.add_error("event", "Please select an event from your team.")

        if member and member.team_id != self.team.id:
            self.add_error("member", "Please select a member from your team.")

        if event and member:
            duplicate_qs = TeamPerformanceRecord.objects.filter(
                team=self.team,
                event=event,
                member=member,
            )
            if self.instance and self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)

            if duplicate_qs.exists():
                self.add_error(
                    "event",
                    "This player already has data for this event. Edit the existing record instead of creating a new one.",
                )

        if event and member:
            cleaned["participation_status"] = self._participation_from_attendance(event, member)
        else:
            cleaned["participation_status"] = TeamPerformanceRecord.PARTICIPATION_PRESENT

        participation_status = cleaned.get("participation_status")
        injury_status = (cleaned.get("injury_status") or "").strip()
        if participation_status == TeamPerformanceRecord.PARTICIPATION_INJURED:
            if not injury_status:
                self.add_error("injury_status", "Please choose an injury status.")
        else:
            cleaned["injury_status"] = ""

        return cleaned
