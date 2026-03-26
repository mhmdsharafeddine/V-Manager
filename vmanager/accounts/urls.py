from django.urls import path

from . import views


app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("verify-2fa/", views.verify_2fa_view, name="verify_2fa"),
    path("verify-2fa/resend/", views.resend_2fa_code_view, name="resend_2fa"),
    path("forgot-password/", views.forgot_password_view, name="forgot_password"),
    path("forgot-password/sent/", views.password_reset_sent_view, name="password_reset_sent"),
    path("forgot-password/verify-code/", views.verify_password_reset_code_view, name="verify_password_reset_code"),
    path(
        "forgot-password/verify-code/resend/",
        views.resend_password_reset_code_view,
        name="resend_password_reset_code",
    ),
    path("reset-password/new/", views.password_reset_confirm_view, name="password_reset_confirm"),
    path("reset-password/complete/", views.password_reset_complete_view, name="password_reset_complete"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("api/me/", views.me_view, name="me"),
]
