# send_email.py
"""
Send emails with attached invoices to students.
"""

# Imports
import os
import logging
import smtplib
from email.message import EmailMessage

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
        # Create the email message
        msg = EmailMessage()
        msg['Subject'] = email_info['subject']
        msg['From'] = email_info['from']
        msg['To'] = email_info['to']
        msg.set_content(email_info['body'])

        # Attach files
        for attachment_path in email_info['attachments']:
            with open(attachment_path, 'rb') as f:
                file_data = f.read()
            file_name = os.path.basename(attachment_path)
            msg.add_attachment(file_data, maintype='application', subtype='pdf', filename=file_name)

        # Send the email
        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT"))) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            server.send_message(msg)
    except Exception as e:
        logging.error(f"Failed to send email to {email_info['to']}: {e}")
        raise