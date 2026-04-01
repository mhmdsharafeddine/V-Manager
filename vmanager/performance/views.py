from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import AccountProfile
from team_management.access import can_access_team_features, get_team_role

from .forms import TeamPerformanceRecordForm
from .models import TeamPerformanceRecord


MANAGE_PERFORMANCE_ROLES = {
	AccountProfile.ROLE_COACH,
}


def _resolve_team_for_user(user):
	membership = getattr(user, "team_membership", None)
	if membership and membership.is_active:
		return membership.team
	return None


def _can_view_performance(user):
	return getattr(user, "is_authenticated", False) and can_access_team_features(user) and _resolve_team_for_user(user) is not None


def _can_manage_performance(user):
	return _can_view_performance(user) and get_team_role(user) in MANAGE_PERFORMANCE_ROLES


@login_required
def dashboard_view(request):
	if not _can_view_performance(request.user):
		messages.error(request, "You do not have access to performance analytics.")
		return redirect("home")

	team = _resolve_team_for_user(request.user)
	records_qs = TeamPerformanceRecord.objects.select_related("event", "member__user").filter(team=team)
	attended_qs = records_qs.filter(participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT)

	# Keep only the most recent 12 months when rendering trend labels.
	monthly_rows = list(
		attended_qs.values("event__scheduled_at__year", "event__scheduled_at__month")
		.annotate(avg_points=Avg("points_scored"), total_errors=Sum("unforced_errors"))
		.order_by("event__scheduled_at__year", "event__scheduled_at__month")
	)[-12:]

	month_labels = [f"{row['event__scheduled_at__month']}/{row['event__scheduled_at__year']}" for row in monthly_rows]
	month_avg_points = [round(float(row["avg_points"] or 0), 2) for row in monthly_rows]
	month_errors = [int(row["total_errors"] or 0) for row in monthly_rows]

	event_rows = attended_qs.values("event_id", "result", "event__event_type").distinct()
	wins = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_WIN)
	losses = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_LOSS)
	draws = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_DRAW)
	total_events = len(event_rows)
	win_rate = round((wins / total_events) * 100, 1) if total_events else 0

	avg_target = attended_qs.aggregate(avg_target=Avg("target_achieved"))["avg_target"]
	avg_target_percent = round((float(avg_target or 0)) * 100, 1)

	distribution = {
		"match": 0,
		"tournament": 0,
		"practice": 0,
		"training": 0,
		"other": 0,
	}
	for row in attended_qs.values("event_id", "event__event_type").distinct():
		event_type = row["event__event_type"]
		if event_type in distribution:
			distribution[event_type] += 1
		else:
			distribution["other"] += 1

	top_players_qs = (
		attended_qs.values("member__user__first_name", "member__user__last_name")
		.annotate(
			kills_total=Sum("kills"),
			aces_total=Sum("aces"),
			blocks_total=Sum("blocks"),
		)
		.order_by("-kills_total", "-aces_total")[:5]
	)
	top_labels = [
		f"{row['member__user__first_name']} {row['member__user__last_name']}".strip() or "Member"
		for row in top_players_qs
	]
	top_kills = [int(row["kills_total"] or 0) for row in top_players_qs]
	top_aces = [int(row["aces_total"] or 0) for row in top_players_qs]
	top_blocks = [int(row["blocks_total"] or 0) for row in top_players_qs]

	context = {
		"team": team,
		"can_manage_performance": _can_manage_performance(request.user),
		"total_events": total_events,
		"wins": wins,
		"losses": losses,
		"draws": draws,
		"win_rate": win_rate,
		"avg_target_percent": avg_target_percent,
		"month_labels": month_labels,
		"month_avg_points": month_avg_points,
		"month_errors": month_errors,
		"dist_match": distribution["match"],
		"dist_tournament": distribution["tournament"],
		"dist_practice": distribution["practice"],
		"dist_training": distribution["training"],
		"dist_other": distribution["other"],
		"top_labels": top_labels,
		"top_kills": top_kills,
		"top_aces": top_aces,
		"top_blocks": top_blocks,
		"recent_records": records_qs.order_by("-created_at")[:8],
		"now": timezone.localtime(timezone.now()),
	}
	return render(request, "performance/dashboard.html", context)


@login_required
def create_record_view(request):
	if not _can_manage_performance(request.user):
		messages.error(request, "Only coaches can enter performance data.")
		return redirect("performance:dashboard")

	team = _resolve_team_for_user(request.user)

	if request.method == "POST":
		form = TeamPerformanceRecordForm(request.POST, team=team)
		if form.is_valid():
			record = form.save(commit=False)
			record.team = team
			record.recorded_by = request.user
			record.save()
			messages.success(request, "Performance record saved successfully.")
			return redirect("performance:dashboard")
	else:
		selected_member = (request.GET.get("member") or "").strip()
		initial = {"member": selected_member} if selected_member.isdigit() else None
		form = TeamPerformanceRecordForm(team=team, initial=initial)

	return render(
		request,
		"performance/record_form.html",
		{
			"form": form,
			"team": team,
			"form_title": "Input Performance Data",
			"form_subtitle": f"Save event-linked player performance entries for {team.name}.",
			"submit_label": "Save Record",
			"is_edit": False,
		},
	)


@login_required
def edit_record_view(request, record_id):
	if not _can_manage_performance(request.user):
		messages.error(request, "Only coaches can edit performance data.")
		return redirect("performance:dashboard")

	team = _resolve_team_for_user(request.user)
	record = (
		TeamPerformanceRecord.objects.select_related("event", "member__user")
		.filter(pk=record_id, team=team)
		.first()
	)
	if record is None:
		messages.error(request, "This performance record was already removed.")
		return redirect("performance:dashboard")

	if request.method == "POST":
		form = TeamPerformanceRecordForm(request.POST, instance=record, team=team)
		if form.is_valid():
			updated_record = form.save(commit=False)
			updated_record.team = team
			updated_record.recorded_by = request.user
			updated_record.save()
			messages.success(request, "Performance record updated successfully.")
			return redirect("performance:dashboard")
	else:
		form = TeamPerformanceRecordForm(instance=record, team=team)

	member_name = record.member.user.get_full_name() or record.member.user.email
	return render(
		request,
		"performance/record_form.html",
		{
			"form": form,
			"team": team,
			"form_title": "Edit Performance Data",
			"form_subtitle": f"Update the saved record for {member_name}.",
			"submit_label": "Save Changes",
			"is_edit": True,
		},
	)
