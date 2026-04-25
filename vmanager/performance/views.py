from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import AccountProfile
from scheduling.models import EventAttendance
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


def _default_participation_map_for_member(member, event_ids):
	defaults = {str(event_id): TeamPerformanceRecord.PARTICIPATION_PRESENT for event_id in event_ids}
	if member is None or not event_ids:
		return defaults

	attendance_rows = EventAttendance.objects.filter(
		event_id__in=event_ids,
		player_id=member.user_id,
	).values("event_id", "status", "not_attending_reason")

	for row in attendance_rows:
		event_key = str(row["event_id"])
		status = row["status"]
		reason = row["not_attending_reason"]

		if status == EventAttendance.STATUS_ATTENDING:
			defaults[event_key] = TeamPerformanceRecord.PARTICIPATION_PRESENT
		elif status == EventAttendance.STATUS_NOT_ATTENDING:
			defaults[event_key] = (
				TeamPerformanceRecord.PARTICIPATION_INJURED
				if reason == EventAttendance.REASON_INJURED
				else TeamPerformanceRecord.PARTICIPATION_DID_NOT_ATTEND
			)
		elif status == EventAttendance.STATUS_MAYBE:
			defaults[event_key] = TeamPerformanceRecord.PARTICIPATION_ABSENT

	return defaults


def _summary_from_records(records_qs):
	attended_qs = records_qs.filter(participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT)
	event_rows = list(attended_qs.values("event_id", "result", "event__event_type").distinct())

	wins = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_WIN)
	losses = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_LOSS)
	draws = sum(1 for row in event_rows if row["result"] == TeamPerformanceRecord.RESULT_DRAW)
	total_events = len(event_rows)
	win_rate = round((wins / total_events) * 100, 1) if total_events else 0.0

	avg_target = attended_qs.aggregate(avg_target=Avg("target_achieved"))["avg_target"]
	avg_target_percent = round((float(avg_target or 0)) * 100, 1)

	return {
		"total_events": total_events,
		"wins": wins,
		"losses": losses,
		"draws": draws,
		"win_rate": win_rate,
		"avg_target_percent": avg_target_percent,
	}


def _build_dashboard_payload(records_qs):
	attended_qs = records_qs.filter(participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT)

	monthly_rows = list(
		attended_qs.values("event__scheduled_at__year", "event__scheduled_at__month")
		.annotate(avg_points=Avg("points_scored"), total_errors=Sum("unforced_errors"))
		.order_by("event__scheduled_at__year", "event__scheduled_at__month")
	)[-12:]

	month_labels = [f"{row['event__scheduled_at__month']}/{row['event__scheduled_at__year']}" for row in monthly_rows]
	month_avg_points = [round(float(row["avg_points"] or 0), 2) for row in monthly_rows]
	month_errors = [int(row["total_errors"] or 0) for row in monthly_rows]

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

	return {
		"summary": _summary_from_records(records_qs),
		"charts": {
			"month_labels": month_labels,
			"month_avg_points": month_avg_points,
			"month_errors": month_errors,
			"distribution": distribution,
			"top_labels": top_labels,
			"top_kills": top_kills,
			"top_aces": top_aces,
			"top_blocks": top_blocks,
		},
	}


def _month_bounds(year, month, tz):
	start = timezone.make_aware(datetime(year, month, 1, 0, 0), tz)
	if month == 12:
		end = timezone.make_aware(datetime(year + 1, 1, 1, 0, 0), tz)
	else:
		end = timezone.make_aware(datetime(year, month + 1, 1, 0, 0), tz)
	return start, end


def _parse_month_value(raw_month, now_local):
	if not raw_month:
		return now_local.year, now_local.month
	try:
		year_str, month_str = raw_month.split("-", 1)
		year = int(year_str)
		month = int(month_str)
		if 1 <= month <= 12:
			return year, month
	except (TypeError, ValueError):
		pass
	return now_local.year, now_local.month


def _shift_month(year, month, delta):
	month_index = (year * 12 + (month - 1)) + delta
	return month_index // 12, (month_index % 12) + 1


def _build_trend_payload(current_summary, previous_summary):
	def _trend_for(metric):
		current_value = float(current_summary.get(metric, 0) or 0)
		previous_value = float(previous_summary.get(metric, 0) or 0)
		delta = round(current_value - previous_value, 2)
		if delta > 0:
			direction = "up"
		elif delta < 0:
			direction = "down"
		else:
			direction = "flat"
		return {
			"direction": direction,
			"delta": delta,
			"current": current_value,
			"previous": previous_value,
		}

	return {
		"total_events": _trend_for("total_events"),
		"wins": _trend_for("wins"),
		"losses": _trend_for("losses"),
		"win_rate": _trend_for("win_rate"),
		"avg_target_percent": _trend_for("avg_target_percent"),
	}


def _filter_records_by_range(records_qs, range_key, month_value):
	now_local = timezone.localtime(timezone.now())
	tz = timezone.get_current_timezone()
	selected_range = range_key if range_key in {"full_season", "month", "last_match"} else "full_season"
	selected_month_value = ""

	filtered_qs = records_qs
	reference_year = now_local.year
	reference_month = now_local.month

	if selected_range == "month":
		year, month = _parse_month_value(month_value, now_local)
		selected_month_value = f"{year:04d}-{month:02d}"
		reference_year, reference_month = year, month
		start, end = _month_bounds(year, month, tz)
		filtered_qs = records_qs.filter(event__scheduled_at__gte=start, event__scheduled_at__lt=end)
	elif selected_range == "last_match":
		last_match = (
			records_qs.filter(participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT)
			.select_related("event")
			.order_by("-event__scheduled_at")
			.first()
		)
		if last_match is not None:
			filtered_qs = records_qs.filter(event_id=last_match.event_id)
			local_match_dt = timezone.localtime(last_match.event.scheduled_at)
			reference_year, reference_month = local_match_dt.year, local_match_dt.month

	previous_year, previous_month = _shift_month(reference_year, reference_month, -1)
	prev_start, prev_end = _month_bounds(previous_year, previous_month, tz)
	previous_month_qs = records_qs.filter(event__scheduled_at__gte=prev_start, event__scheduled_at__lt=prev_end)

	return {
		"selected_range": selected_range,
		"selected_month": selected_month_value,
		"filtered_qs": filtered_qs,
		"previous_month_qs": previous_month_qs,
		"previous_month_label": f"{previous_month:02d}/{previous_year}",
	}


def _build_dashboard_response(records_qs, range_key, month_value):
	range_payload = _filter_records_by_range(records_qs, range_key, month_value)
	current_payload = _build_dashboard_payload(range_payload["filtered_qs"])
	previous_summary = _summary_from_records(range_payload["previous_month_qs"])

	return {
		"range": range_payload["selected_range"],
		"month": range_payload["selected_month"],
		"summary": current_payload["summary"],
		"charts": current_payload["charts"],
		"previous_month_summary": previous_summary,
		"previous_month_label": range_payload["previous_month_label"],
		"trends": _build_trend_payload(current_payload["summary"], previous_summary),
	}


@login_required
def dashboard_view(request):
	if not _can_view_performance(request.user):
		messages.error(request, "You do not have access to performance analytics.")
		return redirect("home")

	team = _resolve_team_for_user(request.user)
	records_qs = TeamPerformanceRecord.objects.select_related("event", "member__user").filter(team=team)
	dashboard_data = _build_dashboard_response(records_qs, "full_season", "")
	summary = dashboard_data["summary"]
	charts = dashboard_data["charts"]

	context = {
		"team": team,
		"can_manage_performance": _can_manage_performance(request.user),
		"total_events": summary["total_events"],
		"wins": summary["wins"],
		"losses": summary["losses"],
		"draws": summary["draws"],
		"win_rate": summary["win_rate"],
		"avg_target_percent": summary["avg_target_percent"],
		"month_labels": charts["month_labels"],
		"month_avg_points": charts["month_avg_points"],
		"month_errors": charts["month_errors"],
		"dist_match": charts["distribution"]["match"],
		"dist_tournament": charts["distribution"]["tournament"],
		"dist_practice": charts["distribution"]["practice"],
		"dist_training": charts["distribution"]["training"],
		"dist_other": charts["distribution"]["other"],
		"top_labels": charts["top_labels"],
		"top_kills": charts["top_kills"],
		"top_aces": charts["top_aces"],
		"top_blocks": charts["top_blocks"],
		"dashboard_data": dashboard_data,
		"recent_records": records_qs.order_by("-created_at")[:8],
		"now": timezone.localtime(timezone.now()),
	}
	return render(request, "performance/dashboard.html", context)


@login_required
def dashboard_summary_api(request):
	if not _can_view_performance(request.user):
		return JsonResponse({"error": "You do not have access to performance analytics."}, status=403)

	team = _resolve_team_for_user(request.user)
	records_qs = TeamPerformanceRecord.objects.select_related("event", "member__user").filter(team=team)
	range_key = (request.GET.get("range") or "full_season").strip().lower()
	month_value = (request.GET.get("month") or "").strip()

	return JsonResponse(_build_dashboard_response(records_qs, range_key, month_value))


@login_required
def create_record_view(request):
	if not _can_manage_performance(request.user):
		messages.error(request, "Only coaches can enter performance data.")
		return redirect("performance:dashboard")

	team = _resolve_team_for_user(request.user)
	event_participation_defaults = {}
	selected_member_obj = None

	if request.method == "POST":
		form = TeamPerformanceRecordForm(request.POST, team=team)
		posted_member = (request.POST.get("member") or "").strip()
		if posted_member.isdigit():
			selected_member_obj = form.fields["member"].queryset.filter(pk=int(posted_member)).first()
		event_ids = list(form.fields["event"].queryset.values_list("id", flat=True))
		event_participation_defaults = _default_participation_map_for_member(selected_member_obj, event_ids)
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
		if selected_member.isdigit():
			selected_member_obj = form.fields["member"].queryset.filter(pk=int(selected_member)).first()
		event_ids = list(form.fields["event"].queryset.values_list("id", flat=True))
		event_participation_defaults = _default_participation_map_for_member(selected_member_obj, event_ids)

	return render(
		request,
		"performance/record_form.html",
		{
			"form": form,
			"team": team,
			"event_participation_defaults": event_participation_defaults,
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
