# invoice.py
"""
Orchestrates the generation and sending of student invoices.
"""

# Imports
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.logging import RichHandler
from rich.progress import Progress, TaskID

from database import Database, with_cursor

# Module imports
from helper import (
    compile_latex_to_pdf,
    format_progress_update,
    parse_session,
    render_email_template,
    render_latex_template,
    track_progress,
)
from send_email import send_email

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
logger = logging.getLogger(__name__)

load_dotenv()

db = Database()


def fetch_tabs_before_date(date: date):
    """Fetch previous tabs for all billers prior to a given date into a dict."""
    query = """
        SELECT b.billing_id, COALESCE(i.total_invoiced, 0) - COALESCE(p.total_paid, 0) AS current_tab
        FROM billing_accounts b
        LEFT JOIN (
            SELECT billing_id, SUM(total_amount) AS total_invoiced FROM invoices WHERE biweek_start < %s GROUP BY billing_id
        ) i ON i.billing_id = b.billing_id
        LEFT JOIN (
            SELECT billing_id, SUM(amount) AS total_paid FROM payments WHERE payment_date < %s::date GROUP BY billing_id
        ) p ON p.billing_id = b.billing_id;
    """
    results = db.fetch_all(query, (date, date))
    return {row["billing_id"]: float(row["current_tab"]) for row in results}


def fetch_and_parse_biweekly_sessions(biweek_start: date, biweek_end: date):
    """Fetches and parses all session records for the biweekly period in a single query."""
    query = """SELECT * FROM session_billing_details WHERE session_date >= %s::date AND session_date < %s::date ORDER BY session_date;"""
    raw_sessions = db.fetch_all(query, (biweek_start, biweek_end))
    return [parse_session(session) for session in raw_sessions]


def group_by(items: list[dict], key: str) -> dict:
    """Group a list of dictionaries by a specified key."""
    grouped = defaultdict(list)
    for item in items:
        grouped[item[key]].append(item)
    return dict(grouped)


@with_cursor
def build_invoice(
    cursor,
    billing_id: int,
    sessions: list,
    current_tab: float,
    biweek_start: date,
    biweek_end: date,
):
    """
    Writes, compiles, and creates an invoice based on session data.
    Arguments:
        cursor (psycopg2 cursor): Active database cursor for fetching biller info
        billing_id (int): The billing ID of the student
        sessions (list): List of session records for the student
        current_tab (float): The current tab balance for the billing account
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        dict: Contains the path to the generated invoice PDF and the invoice number
    """

    try:
        # Insert invoice record
        current_date = datetime.now(tz=ZoneInfo("America/Edmonton")).date()
        total_hours = sum(session["duration_hours"] for session in sessions)
        total_amount = sum(session["total_fee"] for session in sessions)

        query = """INSERT INTO invoices (billing_id, biweek_start, date_sent, total_hours, total_amount) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (billing_id, biweek_start) DO NOTHING RETURNING invoice_id"""
        cursor.execute(
            query,
            (billing_id, biweek_start, current_date, total_hours, total_amount),
        )
        result = cursor.fetchone()
        invoice_number = int(result[0]) if result else 1

        # Render LaTeX template
        invoice_context = {
            "invoice_number": invoice_number,
            "invoice_date": current_date.strftime("%B %d, %Y"),
            "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
            "billing_id": billing_id,
            "student_names": ", ".join(
                sorted({session["student_name"] for session in sessions})
            ),
            "subjects_list": ", ".join(
                sorted({session["subjects"] for session in sessions})
            ),
            "sessions": sessions,
            "current_tab": current_tab,
            "propel_email": os.getenv("PROPEL_EMAIL"),
            "propel_phone": os.getenv("PROPEL_PHONE"),
        }
        tex_body = render_latex_template("invoice.j2", invoice_context)

        # Determine output folder and files
        test_str = "test/" if os.getenv("APP_ENV") in ("dev", "test") else ""
        output_dir = Path(f"invoices/{test_str}{os.getenv('CURRENT_SEMESTER')}")
        output_dir.mkdir(parents=True, exist_ok=True)

        tex_path = output_dir / f"INV-{invoice_number:04}.tex"
        tex_path.write_text(tex_body)

        # Compile to PDF
        pdf_path = compile_latex_to_pdf(tex_path, output_dir)

        return {"path": str(pdf_path), "number": invoice_number}

    except Exception as e:
        logger.error(f"Failed to generate invoice for billing ID {billing_id}: {e}")
        raise


@with_cursor
def send_invoice(
    cursor, billing_id: int, invoice_path: str, biweek_start: date, biweek_end: date
):
    """
    Send the generated invoice via email.
    Arguments:
        cursor (psycopg2 cursor): Active database cursor for fetching biller info
        billing_id (int): The billing ID of the student
        invoice_path (str): Path to the generated invoice file
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        None
    """
    query = """SELECT email, display_name, first_invoice FROM billing_accounts WHERE billing_id = %s"""
    cursor.execute(query, (billing_id,))
    result = cursor.fetchone()
    if not result:
        logger.error(
            f"No biller found for billing ID: {billing_id}. Cannot send invoice."
        )
        return
    email, name, first_invoice = (
        result["email"],
        result["display_name"],
        result["first_invoice"],
    )

    known_emails = os.getenv("KNOWN_EMAILS", "").split(", ")

    context = {
        "name": name,
        "propel_email": os.getenv("PROPEL_EMAIL"),
        "biweek_start": biweek_start.strftime("%B %d"),
        "biweek_end": biweek_end.strftime("%B %d, %Y"),
        "signature": os.getenv("EMAIL_SIGNATURE"),
    }
    template = (
        "standard_invoice.j2"
        if not first_invoice
        else "welcome_known_invoice.j2"
        if email in known_emails
        else "welcome_unknown_invoice.j2"
    )
    body = render_email_template(template, context)

    options = {
        "subject": f"Propel Tutoring Invoice ({biweek_start.strftime('%B %d')} - {biweek_end.strftime('%B %d, %Y')})",
        "from": os.getenv("PROPEL_EMAIL"),
        "to": email,
        "body": body,
        "attachments": [invoice_path],
    }

    if os.getenv("APP_ENV") == "dev" or os.getenv("APP_ENV") == "test":
        options["to"] = "kleeson@ualberta.ca"

    send_email(options)

    # Update first_invoice flag
    if first_invoice:
        cursor.execute(
            """UPDATE billing_accounts SET first_invoice = FALSE WHERE billing_id = %s AND first_invoice = TRUE""",
            (billing_id,),
        )


@with_cursor
def build_tutor_payroll(
    cursor, sessions_by_tutor: dict, biweek_start: date, biweek_end: date
):
    """
    Writes, compiles, and creates a tutor payroll sheet based on session data.
    Arguments:
        cursor (psycopg2 cursor): Active database cursor for fetching biller info
        sessions_by_tutor (dict): Session records sorted by tutor_id
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        dict: Contains the path to the generated payroll PDF and the payroll number
    """
    tutors = []
    for tutor_id, sessions in sessions_by_tutor.items():
        tutors.append(
            {
                "tutor_id": tutor_id,
                "pref_name": sessions[0]["tutor_name"],
                "num_sessions": len(sessions),
                "num_hours": sum(session["duration_hours"] for session in sessions),
                "num_students": len({session["student_id"] for session in sessions}),
                "total_earned": sum(session["total_tutor_fee"] for session in sessions),
                "total_invoiced": sum(session["total_fee"] for session in sessions),
                "total_profit": sum(session["total_profit"] for session in sessions),
            }
        )

    try:
        # Insert payroll record
        cursor.execute(
            """INSERT INTO payroll (biweek_start, total_hours, total_amount, date_generated) VALUES (%s, %s, %s, %s) RETURNING payroll_id""",
            (
                biweek_start,
                sum(tutor["num_hours"] for tutor in tutors),
                sum(tutor["total_earned"] for tutor in tutors),
                datetime.now(tz=ZoneInfo("America/Edmonton")).date(),
            ),
        )
        result = cursor.fetchone()
        payroll_number = int(result[0]) if result else 1

        # Insert payroll entries
        for tutor in tutors:
            cursor.execute(
                """INSERT INTO payroll_entries (payroll_id, tutor_id, total_hours, total_amount) VALUES (%s, %s, %s, %s)""",
                (
                    payroll_number,
                    tutor["tutor_id"],
                    tutor["num_hours"],
                    tutor["total_earned"],
                ),
            )

        # Render LaTeX template
        payroll_context = {
            "payroll_number": payroll_number,
            "payroll_date": datetime.now(tz=ZoneInfo("America/Edmonton"))
            .date()
            .strftime("%B %d, %Y"),
            "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
            "tutors": sorted(tutors, key=lambda t: t["tutor_id"]),
        }
        tex_body = render_latex_template("payroll.j2", payroll_context)

        # Determine output folder and files
        test_str = "test/" if os.getenv("APP_ENV") in ("dev", "test") else ""
        output_dir = Path(f"payroll/{test_str}{os.getenv('CURRENT_SEMESTER')}")
        output_dir.mkdir(parents=True, exist_ok=True)

        tex_path = output_dir / f"PAY-{payroll_number:04}.tex"
        tex_path.write_text(tex_body)

        # Compile to PDF and return path
        pdf_path = compile_latex_to_pdf(tex_path, output_dir)

        return {"path": str(pdf_path), "number": payroll_number}

    except Exception as e:
        logger.error(f"Failed to generate tutor payroll: {e}")
        raise


def build_invoice_biweek_summary(
    sessions_by_biller: dict,
    invoices: dict,
    payroll_number: int,
    biweek_start: date,
    biweek_end: date,
):
    """
    Writes, compiles, and creates a biweek invoice summary sheet based on session data.
    Arguments:
        sessions_by_biller (dict): Session records sorted by billing_id
        invoices (dict): Contains the path to the generated invoice PDF and the invoice number
        payroll_number (int): The number of the payroll being generated
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        invoice_summary_path (str): Path to the generated invoice summary file
    """
    billers = []
    for billing_id, sessions in sessions_by_biller.items():
        billers.append(
            {
                "billing_id": billing_id,
                "biller_name": sessions[0]["biller_name"],
                "invoice_num": invoices[billing_id]["number"],
                "num_sessions": len(sessions),
                "num_hours": sum(session["duration_hours"] for session in sessions),
                "num_students": len({session["student_id"] for session in sessions}),
                "total_due": sum(session["total_fee"] for session in sessions),
            }
        )

    try:
        summary_number = payroll_number

        # Render LaTeX template
        summary_context = {
            "invoice_summary_number": summary_number,
            "invoice_summary_date": datetime.now(tz=ZoneInfo("America/Edmonton"))
            .date()
            .strftime("%B %d, %Y"),
            "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
            "billers": sorted(billers, key=lambda t: t["billing_id"]),
        }
        tex_body = render_latex_template("invoice_summary.j2", summary_context)

        # Determine output folder and files
        test_str = "test/" if os.getenv("APP_ENV") in ("dev", "test") else ""
        output_dir = Path(f"payroll/{test_str}{os.getenv('CURRENT_SEMESTER')}")
        output_dir.mkdir(parents=True, exist_ok=True)

        tex_path = output_dir / f"IVS-{summary_number:04}.tex"
        tex_path.write_text(tex_body)

        # Compile to PDF and return path
        pdf_path = compile_latex_to_pdf(tex_path, output_dir)

        return {"path": str(pdf_path), "number": summary_number}

    except Exception as e:
        logger.error(f"Failed to generate invoice summary: {e}")
        raise


def send_admin_update(
    sessions: list,
    invoices: dict,
    biweek_start: date,
    biweek_end: date,
    progress: Progress,
    progress_bar: TaskID,
):
    # Group sessions by biller and by tutor
    sessions_by_biller = group_by(sessions, "billing_id")
    sessions_by_tutor = group_by(sessions, "tutor_id")

    # Generate payroll and invoice summary
    progress.update(
        progress_bar,
        advance=1,
        description=format_progress_update("Generating payroll", "yellow"),
    )
    payroll = build_tutor_payroll(sessions_by_tutor, biweek_start, biweek_end)
    progress.update(
        progress_bar,
        advance=1,
        description=format_progress_update("Generating invoice summary", "yellow"),
    )
    invoice_summary = build_invoice_biweek_summary(
        sessions_by_biller, invoices, payroll["number"], biweek_start, biweek_end
    )

    progress.update(
        progress_bar,
        advance=1,
        description=format_progress_update("Sending email", "yellow"),
    )

    # Send email
    context = {
        "biweek_start": biweek_start.strftime("%b %d"),
        "biweek_end": biweek_end.strftime("%b %d, %Y"),
    }
    body = render_email_template("admin_update.j2", context)

    options = {
        "subject": f"Payroll and Invoice Summary: {payroll['number']:04}",
        "from": os.getenv("PROPEL_EMAIL"),
        "to": os.getenv("PROPEL_EMAIL"),
        "body": body,
        "attachments": [payroll["path"], invoice_summary["path"]],
    }
    send_email(options)

    progress.update(progress_bar, advance=1)


def generate_and_send_invoices(biweek_start: date, biweek_end: date):
    """
    Generate and send student invoices for the specified biweekly period.
    Arguments:
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        None
    """
    # Fetch tabs before this biweek
    prior_tabs = fetch_tabs_before_date(biweek_start)

    # Fetch and parse sessions from this biweek
    sessions = fetch_and_parse_biweekly_sessions(biweek_start, biweek_end)
    sessions_by_biller = group_by(sessions, "billing_id")

    with Progress() as progress:
        # Generate invoices
        invoices = {}
        for billing_id, session_list in track_progress(
            sessions_by_biller.items(),
            "Generating and sending invoices",
            "All invoices generated ✓",
            lambda item: f"Generating invoice for billing ID: {item[0]}",
            progress,
        ):
            prior_tab = prior_tabs.get(billing_id, 0.0)
            invoices[billing_id] = build_invoice(
                billing_id, session_list, prior_tab, biweek_start, biweek_end
            )

        # Send invoices
        for billing_id, invoice in track_progress(
            invoices.items(),
            "Sending invoices",
            "All invoices sent ✓",
            lambda item: f"Sending invoice for billing ID: {item[0]}",
            progress,
        ):
            send_invoice(billing_id, invoice["path"], biweek_start, biweek_end)

        # Generate and send tutor payroll summary
        admin_bar = progress.add_task(
            format_progress_update("Generating and sending admin summary", "cyan"),
            total=4,
        )
        send_admin_update(
            sessions, invoices, biweek_start, biweek_end, progress, admin_bar
        )
        progress.update(
            admin_bar,
            description=format_progress_update("Admin summary sent ✓", "green"),
        )
