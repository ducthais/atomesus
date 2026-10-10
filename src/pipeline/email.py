"""Email delivery for registration verification codes."""

import smtplib
from email.message import EmailMessage

from src.pipeline.settings import PipelineSettings


def send_verification_code(config: PipelineSettings, recipient: str, code: str) -> bool:
    """Send a verification code. Return False when local development has no SMTP configured."""
    if not config.smtp_host:
        return False

    message = EmailMessage()
    message["Subject"] = "Mã xác thực tài khoản Atomesus"
    message["From"] = config.smtp_from
    message["To"] = recipient
    message.set_content(
        "Mã xác thực đăng ký Atomesus của bạn là: "
        f"{code}\n\nMã có hiệu lực trong 10 phút. Nếu bạn không yêu cầu đăng ký, hãy bỏ qua email này."
    )

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as smtp:
        if config.smtp_starttls:
            smtp.starttls()
        password = config.smtp_password.get_secret_value()
        if config.smtp_username:
            smtp.login(config.smtp_username, password)
        smtp.send_message(message)
    return True
