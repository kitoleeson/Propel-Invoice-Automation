# invoice.py
"""
Orchestrates the generation and sending of student invoices.
"""

# Imports
from datetime import date, timedelta, datetime
import psycopg2
from psycopg2.extras import DictCursor
import os
import logging
from collections import defaultdict
from rich.logging import RichHandler
from rich.progress import Progress, TaskID
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import subprocess

# Module imports
from helper import format_progress_update
from send_email import send_email

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
load_dotenv()

DATABASE_URL = (
    os.getenv("DATABASE_URL_DEV")
    if os.getenv("APP_ENV") == "dev"
    else os.getenv("DATABASE_URL_PROD")
)


# Helper functions
def cleanup_latex_artifacts(directory: Path, keep_exts={".pdf", ".tex"}):
    """
    Cleanup LaTeX artifacts in the specified directory, keeping only files with specified extensions.
    Arguments:
        directory (Path): Directory to clean up
        keep_exts (set): Set of file extensions to keep
    Returns:
        None
    """
    for file in directory.iterdir():
        if file.is_file() and file.suffix not in keep_exts:
            file.unlink()


# Main functions
def find_sessions(biweek_start: date, biweek_end: date):
    """
    Find all sessions in the database within the specified biweekly period.
    Arguments:
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        sessions_by_biller (dict): Session records sorted by biller_id
    """
    query = """
        SELECT
            sb.billing_id,
            st.student_id,
            CASE
                WHEN d.pref_name IS NOT NULL THEN d.gov_first_name || ' (' || d.pref_name || ') ' || d.gov_last_name
                ELSE d.gov_first_name || ' ' || d.gov_last_name
            END AS student_name,
            COALESCE(d.pref_name, d.gov_first_name) AS student_pref_name,
            t.tutor_id,
            COALESCE(t.pref_name, t.gov_first_name) AS tutor_name,
            s.session_date,
            s.duration_hours,
            (st.hourly_rate + st.markup) AS hourly_rate,
            (s.duration_hours * (st.hourly_rate + st.markup)) AS total_fee,
            (s.duration_hours * st.hourly_rate) AS total_tutor_fee,
            st.subjects
        FROM sessions s
        JOIN student_tutor st ON s.assignment_id = st.assignment_id
        JOIN students d ON st.student_id = d.student_id
        JOIN tutors t ON st.tutor_id = t.tutor_id
        JOIN student_billing sb ON st.student_id = sb.student_id
        WHERE s.session_date::date >= %s AND s.session_date::date < %s
        ORDER BY sb.billing_id, s.session_date;
    """
    with (
        psycopg2.connect(DATABASE_URL) as conn,
        conn.cursor(cursor_factory=DictCursor) as cursor,
    ):
        cursor.execute(query, (biweek_start, biweek_end))
        results = cursor.fetchall()
    sessions_by_biller = defaultdict(list)
    with Progress() as progress:
        session_bar = progress.add_task(
            format_progress_update("Ingesting sessions from sheets", "cyan"),
            total=len(results),
        )
        for session in results:
            sessions_by_biller[session["billing_id"]].append(
                {
                    "student_id": session["student_id"],
                    "student_name": session["student_name"],
                    "student_pref_name": session["student_pref_name"],
                    "subjects": session["subjects"],
                    "tutor_id": session["tutor_id"],
                    "tutor_name": session["tutor_name"],
                    "session_date": session["session_date"].strftime("%b %d"),
                    "duration_hours": float(session["duration_hours"]),
                    "hourly_rate": float(session["hourly_rate"]),
                    "total_fee": float(session["total_fee"]),
                    "total_tutor_fee": float(session["total_tutor_fee"]),
                }
            )
            progress.update(session_bar, advance=1)
        progress.update(
            session_bar,
            description=format_progress_update("All sessions ingested ✓", "green"),
        )
    return sessions_by_biller


def build_invoice(
    billing_id: int, sessions: list, biweek_start: date, biweek_end: date
):
    """
    Build and send invoices based on session data.
    Arguments:
        billing_id (int): The billing ID of the student
        sessions (list): List of session records for the student
    Returns:
        invoice_path (str): Path to the generated invoice file
    """

    # Determine invoice number and current tab
    with (
        psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor) as conn,
        conn.cursor() as cursor,
    ):
        try:
            # Calculate current tab before this biweek
            query = """
                SELECT
                    COALESCE(SUM(i.total_amount), 0) - COALESCE(SUM(p.total_paid), 0) AS current_tab
                FROM invoices i
                LEFT JOIN (
                    SELECT invoice_id, SUM(amount) AS total_paid
                    FROM payments
                    GROUP BY invoice_id
                ) p ON p.invoice_id = i.invoice_id
                WHERE i.billing_id = %s
                AND i.biweek_start < %s;
            """
            cursor.execute(query, (billing_id, biweek_start))
            result = cursor.fetchone()
            current_tab = float(result[0]) if result else 0.0

            # Insert invoice record
            cursor.execute(
                """INSERT INTO invoices (billing_id, biweek_start, date_sent, total_hours, total_amount) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (billing_id, biweek_start) DO NOTHING RETURNING invoice_id""",
                (
                    billing_id,
                    biweek_start,
                    date.today(),
                    sum(session["duration_hours"] for session in sessions),
                    sum(session["total_fee"] for session in sessions),
                ),
            )
            result = cursor.fetchone()
            invoice_number = int(result[0]) if result else 1

            # Prepare invoice data
            invoice_data = {
                "invoice_number": invoice_number,
                "invoice_date": date.today().strftime("%B %d, %Y"),
                "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
                "billing_id": billing_id,
                "student_name": ", ".join(
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

            # Render LaTeX template
            env = Environment(
                loader=FileSystemLoader(Path(__file__).parent / "latex_templates"),
                autoescape=False,
            )
            template = env.get_template("invoice.j2")

            # Generate PDF
            test_str = (
                "test/"
                if os.getenv("APP_ENV") == "dev" or os.getenv("APP_ENV") == "test"
                else ""
            )
            output_path = Path(
                f"invoices/{test_str}{os.getenv('CURRENT_SEMESTER')}/INV-{invoice_number:04}.pdf"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Render and compile LaTeX
            rendered_tex = template.render(**invoice_data)
            tex_path = output_path.with_suffix(".tex")
            with open(tex_path, "w") as f:
                f.write(rendered_tex)
            subprocess.run(
                [
                    "pdflatex",
                    "-interaction=batchmode",
                    "-output-directory",
                    str(output_path.parent),
                    str(tex_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cleanup_latex_artifacts(output_path.parent)

            conn.commit()
            return str(output_path.with_suffix(".pdf"))
        except Exception as e:
            logging.error(f"Error generating invoice for billing ID {billing_id}: {e}")
            conn.rollback()
            raise e


def send_invoice(
    billing_id: int, invoice_path: str, biweek_start: date, biweek_end: date
):
    """
    Send the generated invoice via email.
    Arguments:
        billing_id (int): The billing ID of the student
        invoice_path (str): Path to the generated invoice file
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        None
    """
    with (
        psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor) as conn,
        conn.cursor() as cursor,
    ):
        query = """
            SELECT email, display_name, first_invoice
            FROM billing_accounts
            WHERE billing_id = %s
        """
        cursor.execute(query, (billing_id,))
        result = cursor.fetchone()
        if not result:
            logging.error(
                f"No biller found for billing ID: {billing_id}. Cannot send invoice."
            )
            return
        email, name, first_invoice = (
            result["email"],
            result["display_name"],
            result["first_invoice"],
        )

    if first_invoice:
        if email in os.getenv("KNOWN_EMAILS").split(", "):
            body = f"""Hi {name},|I'd like to welcome you to Propel Tutoring, the expansion of my tutoring business.|For Propel, I have updated my invoicing system to handle more tutors and upgrade to a full database backend. For you, not very much changes compared to our current invoicing system:\n• Invoices are still sent biweekly directly to your email.\n• All fees can still be paid via e-transfer to the email and phone number listed on each invoice. Please include the invoice number in the message.\n• All sessions are still billed in increments of 15 mins, rounded up or down to the nearest 0.25 hours; and no matter if you continue tutoring with me, another one of my tutors, or both, hourly rates per tutor remain locked in forever once set.\nPlease do note, however, that the email for e-transfer has been changed -- I am now using the official Propel Tutoring email (propeltutoringyeg@gmail.com), so please direct your e-transfers to that address. Payment is now due within 7 days from the day you receive the invoice, no longer 10 days; this allows me to make sure my tutors get paid on time for their work.|More information on our billing policies can be found in the client agreement which you will be getting an email about shortly.|Here are some of the improvements I've made with the new invoicing system:\n• Redesign of the database backend to improve data integrity and allow for easier expansion in the future (this will not affect your experience).\n• Improved invoice layout for better readability.\n• Introduction of "billing accounts" allowing you to manage multiple students under one email if needed in the future. Each current student you have with me is already linked to a billing account under your email.\n• More information for each session including tutor name, client name, and rate. This way if you have multiple students or tutors, everything is clearly laid out in one invoice.|I'd also like to thank you. I never thought that my tutoring would grow to a point that I couldn't handle the amount of client requests I had coming in. It is because of your support that I'm able to start this business and help as many students chase their dreams as possible, so thank you.|I'm currently working on building my website so that I can direct incoming client traffic through a controlled platform. Once it is complete I will send the link out in another email for your reference, but until then, if you know of anybody that is looking for tutoring, please send them my way via this business email (propeltutoringyeg@gmail.com).|Please find attached your first Propel Tutoring invoice, for {biweek_start.strftime("%B %d")} (inclusive) to {biweek_end.strftime("%B %d, %Y")} (exclusive).|Please feel free to reach out if you have any questions or concerns regarding invoices, payments, or scheduling. I'm here to help!\nAs always, I appreciate your continued trust and support, and I'm excited to see the progress we will achieve in this upcoming semester!|Kito Lee Son"""
        else:
            body = f"""Hello {name},|I'd like to welcome you to Propel Tutoring.|We use an automated invoicing system to keep our billing simple and consistent, and to make it easier for you to keep track of your tutoring expenses. Here's what to expect going forward:\n• Invoices are sent biweekly directly to your email, each covering a 2-week period of tutoring sessions.\n• Payment is due within 7 days from the day you receive the invoice.\n• All fees can be paid via e-transfer to the email and phone number listed on each invoice. Please include the invoice number in the message.\n• Our system operates based on "billing accounts", which allow you to manage multiple students under one email if needed. Your email is already linked to a billing account that includes your current student(s).\n• Each invoice will contain information for each session including tutor name, client name, and rate. This way if you have multiple students or tutors, everything is clearly laid out in one invoice.|I'd also like to inform/remind you that my sessions are billed in increments of 15 mins, rounded up or down to the nearest 0.25 hours; and that once set, hourly rates per tutor are locked in forever. More information on our billing policies can be found in the client agreement which you will be getting an email about shortly.|Please find attached your first tutoring invoice for the semester, for {biweek_start.strftime("%B %d")} (inclusive) to {biweek_end.strftime("%B %d, %Y")} (exclusive).|Please feel free to reach out if you have any questions or concerns regarding invoices, payments, or scheduling. I'm here to help!\nI appreciate your trust and support, and I'm excited to see the progress this semester will bring!|Kito Lee Son"""
    else:
        body = f"Hello {name},|Please find attached your tutoring invoice for the period of {biweek_start.strftime('%B %d')} (inclusive) to {biweek_end.strftime('%B %d, %Y')} (exclusive).|If you have any questions or concerns regarding invoices, payments, or scheduling, please feel free to reach out. I'm here to help!|Kito Lee Son"
    # body = f"Hi again {name},|I am so sorry for the confusion, but I just realized that there was a bug in my code which sent you the wrong invoice! I have fixed the error and attached the corrected invoice to this email.|Please use this updated invoice for your records and disregard the previous one. I apologize for any inconvenience this may have caused, especially since this happened last invoice day too! The development process has been a rocky road, however all bugs are now fixed and we will not be having this issue again.|Please let me know if you have any questions or concerns!|Kito Lee Son"

    options = {
        "subject": f"Propel Tutoring Invoice ({biweek_start.strftime('%B %d')} - {biweek_end.strftime('%B %d, %Y')})",
        "from": os.getenv("PROPEL_EMAIL"),
        "to": email,
        "body": body.replace("|", "\n\n"),
        "attachments": [invoice_path],
    }
    if os.getenv("APP_ENV") == "dev" or os.getenv("APP_ENV") == "test":
        options["to"] = "kleeson@ualberta.ca"
    send_email(options)

    with (
        psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor) as conn,
        conn.cursor() as cursor,
    ):
        # Update first_invoice flag
        if first_invoice:
            cursor.execute(
                """UPDATE billing_accounts SET first_invoice = FALSE WHERE billing_id = %s AND first_invoice = TRUE""",
                (billing_id,),
            )
            conn.commit()


def generate_and_send_tutor_payroll(
    sessions_by_biller: dict,
    biweek_start: date,
    biweek_end: date,
    progress: Progress,
    payroll_bar: TaskID,
):
    """
    Generate and send tutor payroll summaries for the specified biweekly period.
    Arguments:
        sessions_by_biller (dict): Session records sorted by billing_id
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
        progress (Progress): Rich Progress instance for updating progress bars
        payroll_bar (TaskID): Specific progress bar for payroll generation
    Returns:
        None
    """
    # Generate tutor payroll summaries
    progress.update(
        payroll_bar,
        description=format_progress_update("Gathering payroll data", "yellow"),
    )
    # Build tutor payroll data
    sessions_by_tutor = defaultdict(list)
    for session_list in sessions_by_biller.values():
        for session in session_list:
            sessions_by_tutor[session["tutor_id"]].append(session)
    tutors = []
    for tutor_id, sessions in sessions_by_tutor.items():
        tutors.append(
            {
                "tutor_id": tutor_id,
                "pref_name": sessions[0]["tutor_name"],
                "num_sessions": len(sessions),
                "num_hours": sum(session["duration_hours"] for session in sessions),
                "num_students": len(set(session["student_id"] for session in sessions)),
                "total_earned": sum(session["total_tutor_fee"] for session in sessions),
            }
        )

    # Determine payroll number
    with (
        psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor) as conn,
        conn.cursor() as cursor,
    ):
        try:
            cursor.execute(
                """INSERT INTO payroll (biweek_start, total_hours, total_amount, date_paid, date_generated) VALUES (%s, %s, %s, %s, %s) RETURNING payroll_id""",
                (
                    biweek_start,
                    sum(tutor["num_hours"] for tutor in tutors),
                    sum(tutor["total_earned"] for tutor in tutors),
                    date.today() + timedelta(days=7),
                    date.today(),
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
            conn.commit()
        except Exception as e:
            logging.error(f"Error generating payroll record: {e}")
            conn.rollback()
            raise e

    # Prepare payroll data
    payroll_data = {
        "payroll_number": payroll_number,
        "payroll_date": date.today().strftime("%B %d, %Y"),
        "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
        "tutors": sorted(tutors, key=lambda t: t["tutor_id"]),
    }

    progress.update(
        payroll_bar,
        advance=1,
        description=format_progress_update("Generating payroll summary", "yellow"),
    )
    # Render LaTeX template
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "latex_templates"),
        autoescape=False,
    )
    template = env.get_template("payroll.j2")

    # Generate PDF
    test_str = (
        "test/"
        if os.getenv("APP_ENV") == "dev" or os.getenv("APP_ENV") == "test"
        else ""
    )
    output_path = Path(
        f"payroll/{test_str}{os.getenv('CURRENT_SEMESTER')}/PAY-{payroll_number:04}.pdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render and compile LaTeX
    rendered_tex = template.render(**payroll_data)
    tex_path = output_path.with_suffix(".tex")
    with open(tex_path, "w") as f:
        f.write(rendered_tex)
    subprocess.run(
        [
            "pdflatex",
            "-interaction=batchmode",
            "-output-directory",
            str(output_path.parent),
            str(tex_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cleanup_latex_artifacts(output_path.parent)
    progress.update(
        payroll_bar,
        advance=1,
        description=format_progress_update("Sending email", "yellow"),
    )

    # Send email
    options = {
        "subject": f"Payroll Summary - PAY-{payroll_number:04}",
        "from": os.getenv("PROPEL_EMAIL"),
        "to": os.getenv("PROPEL_EMAIL"),
        "body": f"Tutor payroll summary for {biweek_start.strftime('%b %d')} to {biweek_end.strftime('%b %d, %Y')}.",
        "attachments": [output_path.with_suffix(".pdf")],
    }
    send_email(options)

    progress.update(payroll_bar, advance=1)

    return payroll_number, output_path.with_suffix(".pdf")


# TO DO
# gather billing info during build_invoice, and save into data structure to pass into generate_and_send_invoice_summary
def generate_and_send_invoice_summary(
    sessions_by_biller: dict,
    invoice_summary_number: int,
    biweek_start: date,
    biweek_end: date,
    progress: Progress,
    payroll_bar: TaskID,
):
    """
    Generate and send invoice summary for the specified biweekly period.
    Arguments:
        sessions_by_biller (dict): Session records sorted by billing_id
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
        progress (Progress): Rich Progress instance for updating progress bars
        payroll_bar (TaskID): Specific progress bar for payroll generation
    Returns:
        None
    """
    # Generate tutor payroll summaries
    progress.update(
        payroll_bar,
        description=format_progress_update("Gathering payroll data", "yellow"),
    )
    # Build biller data: billing_id, biller_name, invoice_num, num_sessions, num_students, total_due
    billers = []
    for billing_id, sessions in sessions_by_biller.items():
        billers.append(
            {
                "billing_id": billing_id,
                "biller_name": sessions[0]["student_name"],
                "invoice_num": sessions[0]["billing_id"],
                "num_sessions": len(sessions),
                "num_students": len(set(session["student_id"] for session in sessions)),
                "total_due": sum(session["total_fee"] for session in sessions),
            }
        )

    # Build tutor payroll data
    sessions_by_tutor = defaultdict(list)
    for session_list in sessions_by_biller.values():
        for session in session_list:
            sessions_by_tutor[session["tutor_id"]].append(session)
    tutors = []
    for tutor_id, sessions in sessions_by_tutor.items():
        tutors.append(
            {
                "tutor_id": tutor_id,
                "pref_name": sessions[0]["tutor_name"],
                "num_sessions": len(sessions),
                "num_hours": sum(session["duration_hours"] for session in sessions),
                "num_students": len(set(session["student_id"] for session in sessions)),
                "total_earned": sum(session["total_tutor_fee"] for session in sessions),
            }
        )

    # Prepare payroll data
    payroll_data = {
        "invoice_summary_number": invoice_summary_number,
        "invoice_summary_date": date.today().strftime("%B %d, %Y"),
        "biweek": f"{biweek_start.strftime('%b %d')} - {biweek_end.strftime('%b %d, %Y')}",
        "tutors": sorted(tutors, key=lambda t: t["tutor_id"]),
    }

    progress.update(
        payroll_bar,
        advance=1,
        description=format_progress_update("Generating payroll summary", "yellow"),
    )
    # Render LaTeX template
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "latex_templates"),
        autoescape=False,
    )
    template = env.get_template("payroll.j2")

    # Generate PDF
    test_str = (
        "test/"
        if os.getenv("APP_ENV") == "dev" or os.getenv("APP_ENV") == "test"
        else ""
    )
    output_path = Path(
        f"payroll/{test_str}{os.getenv('CURRENT_SEMESTER')}/IVS-{invoice_summary_number:04}.pdf"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Render and compile LaTeX
    rendered_tex = template.render(**payroll_data)
    tex_path = output_path.with_suffix(".tex")
    with open(tex_path, "w") as f:
        f.write(rendered_tex)
    subprocess.run(
        [
            "pdflatex",
            "-interaction=batchmode",
            "-output-directory",
            str(output_path.parent),
            str(tex_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cleanup_latex_artifacts(output_path.parent)
    progress.update(
        payroll_bar,
        advance=1,
        description=format_progress_update("Sending email", "yellow"),
    )

    # Send email
    options = {
        "subject": f"Payroll Summary - IVS-{invoice_summary_number:04}",
        "from": os.getenv("PROPEL_EMAIL"),
        "to": os.getenv("PROPEL_EMAIL"),
        "body": f"Tutor payroll summary for {biweek_start.strftime('%b %d')} to {biweek_end.strftime('%b %d, %Y')}.",
        "attachments": [output_path.with_suffix(".pdf")],
    }
    send_email(options)

    progress.update(payroll_bar, advance=1)


def generate_and_send_invoices(biweek_start: date, biweek_end: date):
    """
    Generate and send student invoices for the specified biweekly period.
    Arguments:
        biweek_start (date): The start date of the 2-week billing period.
        biweek_end (date): The end date of the 2-week billing period.
    Returns:
        None
    """
    # Find sessions
    sessions_by_biller = find_sessions(biweek_start, biweek_end)

    with Progress() as progress:
        # Generate invoices
        invoice_bar = progress.add_task(
            format_progress_update("Generating and sending invoices", "cyan"),
            total=len(sessions_by_biller),
        )
        invoices = {}
        for billing_id, session_list in sessions_by_biller.items():
            progress.update(
                invoice_bar,
                description=format_progress_update(
                    f"Generating invoice for billing ID: {billing_id}", "yellow"
                ),
            )
            invoices[billing_id] = build_invoice(
                billing_id, session_list, biweek_start, biweek_end
            )
            progress.update(invoice_bar, advance=1)
        progress.update(
            invoice_bar,
            description=format_progress_update("All invoices generated ✓", "green"),
        )

        # Send invoices
        email_bar = progress.add_task(
            format_progress_update("Sending invoices", "cyan"), total=len(invoices)
        )
        for billing_id, invoice_path in invoices.items():
            progress.update(
                email_bar,
                description=format_progress_update(
                    f"Sending invoice for billing ID: {billing_id}", "yellow"
                ),
            )
            send_invoice(billing_id, invoice_path, biweek_start, biweek_end)
            progress.update(email_bar, advance=1)
        progress.update(
            email_bar,
            description=format_progress_update("All invoices sent ✓", "green"),
        )

        # Generate and send tutor payroll summary
        payroll_bar = progress.add_task(
            format_progress_update(
                "Generating and sending tutor payroll summary", "cyan"
            ),
            total=3,
        )
        generate_and_send_tutor_payroll(
            sessions_by_biller, biweek_start, biweek_end, progress, payroll_bar
        )
        progress.update(
            payroll_bar,
            description=format_progress_update("Tutor payroll summary sent ✓", "green"),
        )


"""
CREATE VIEW current_tabs AS
SELECT
    b.billing_id AS billing_id,
    b.display_name AS biller_name,
    COALESCE(i.total_invoiced, 0) - COALESCE(p.total_paid, 0) AS current_tab
FROM billing_accounts b
    LEFT JOIN (
        SELECT i.billing_id, SUM(i.total_amount) AS total_invoiced
        FROM invoices i
        GROUP BY i.billing_id
    ) i ON i.billing_id = b.billing_id
    LEFT JOIN (
        SELECT i.billing_id, SUM(p.amount) AS total_paid
        FROM payments p
        GROUP BY p.billing_id
    ) p ON p.billing_id = b.billing_id
GROUP BY b.billing_id;
"""
