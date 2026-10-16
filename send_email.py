# send_email.py
"""
Send emails with attached invoices to students.
"""

# Imports
import logging
import os
import smtplib
from email.message import EmailMessage

from rich.logging import RichHandler

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
logger = logging.getLogger(__name__)


def send_email(email_info: dict):
    """
    Send the generated invoice via email.
    Arguments:
        email_info (dict): Information required to send the email. Should contain:
            • 'subject': Subject of the email
            • 'from': Sender email address
            • 'to': Recipient email address
            • 'body': Body text of the email
            • 'attachments': List of attachment file paths
    Returns:
        None
    """
    try:
        test = os.getenv("APP_ENV") != "prod"
        host = os.getenv("TEST_SMTP_HOST") if test else os.getenv("SMTP_HOST")
        port = int(os.getenv("TEST_SMTP_PORT")) if test else int(os.getenv("SMTP_PORT"))
        user = os.getenv("TEST_SMTP_USER") if test else os.getenv("SMTP_USER")
        password = (
            os.getenv("TEST_SMTP_PASSWORD") if test else os.getenv("SMTP_PASSWORD")
        )

        # Create the email message
        msg = EmailMessage()
        msg["Subject"] = email_info["subject"]
        msg["From"] = email_info["from"] if not test else os.getenv("TEST_SMTP_USER")
        msg["To"] = email_info["to"]
        msg.set_content(email_info["body"])

        # Attach files
        for attachment_path in email_info["attachments"]:
            with open(attachment_path, "rb") as f:
                file_data = f.read()
            file_name = os.path.basename(attachment_path)
            msg.add_attachment(
                file_data, maintype="application", subtype="pdf", filename=file_name
            )

        # Send the email
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)
    except Exception as e:
        logger.error(f"Failed to send email to {email_info['to']}: {e}")
        raise
