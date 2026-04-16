#!/usr/bin/env python3
"""
Ingests session data from Google Sheets. Reads each tutor's sheet, parses session data, and writes new sessions to the database.
"""

# Imports
from datetime import date, timedelta, datetime
import logging
from rich.logging import RichHandler
from rich.progress import Progress
import psycopg2
from psycopg2.extras import DictCursor
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from dotenv import load_dotenv

# Module imports
from helper import format_progress_update

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL_DEV") if os.getenv("APP_ENV") == "dev" else os.getenv("DATABASE_URL_PROD")

def parse_datetime(serial_number):
    """Convert Google Sheets serial date to Python datetime."""
    return datetime(1899, 12, 30) + timedelta(days=float(serial_number))

def parse_session_row(row):
    """
    Parse a single row of session data.
    Arguments:
        row (list): Raw row from Google Sheets
    Returns:
        (dict): session data {student_id, student_name, date, duration_hours}
    """
    return {
        "student_id": int(row[0]),
        "student_name": row[1],
        "date": parse_datetime(row[2]),
        "duration": float(row[3])
    }

def ingest_sessions(biweek_start: date, biweek_end: date):
    """
    Adds sessions to database for all tutors within the biweek.
    Arguments:
        biweek_start (date): Start date of the 2-week period
        biweek_end (date): End date of the 2-week period
    Returns:
        (int): number of sessions ingested
    """

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_KEY"))

    sheets = spreadsheet.worksheets()
    count = 0

    # Ingest sessions in biweek from each tutor's sheet
    with psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor) as conn:
        with Progress() as progress:
            sheets_bar = progress.add_task(format_progress_update("Ingesting sessions from sheets", "cyan"), total=len(sheets))
            for sheet in sheets:
                progress.update(sheets_bar, description=format_progress_update(f"Processing sheet: {sheet.title}", "yellow"))
                if " - " not in sheet.title:
                    progress.update(sheets_bar, advance=1)
                    continue
                tutor_id, tutor_name = sheet.title.split(" - ", 1)
                rows = sheet.get(os.getenv("SESSION_RANGE"), value_render_option='UNFORMATTED_VALUE')
                rows_bar = progress.add_task(format_progress_update(f"Processing rows for tutor: {tutor_name}", "cyan"), total=len(rows))
                num = int(os.getenv("SESSION_RANGE").split(":")[0][1:]) - 1 # Starting row number
                with conn.cursor() as cursor:
                    for row in rows:
                        num += 1
                        progress.update(rows_bar, advance=1)
                        if len(row) < 4 or not row[0]:
                            continue
                        # Parse session
                        session = parse_session_row(row)

                        # Check date range
                        if not (biweek_start <= session["date"].date() < biweek_end):
                            continue
                        
                        # Find assignment_id
                        cursor.execute(
                            """SELECT assignment_id FROM student_tutor WHERE student_id = %s AND tutor_id = %s""",
                            (session["student_id"], int(tutor_id))
                        )
                        result = cursor.fetchone()
                        if not result:
                            logging.warning(f"Skipping session for unknown assignment: Tutor {tutor_name}, Row: {num}, Student ID {session['student_id']}")
                            continue

                        # Insert session
                        assignment_id = result["assignment_id"]
                        cursor.execute(
                            """INSERT INTO sessions (assignment_id, session_date, duration_hours) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                            (assignment_id, session["date"].date(), session["duration"])
                        )
                        count += 1
                conn.commit()
                progress.update(sheets_bar, advance=1)
            progress.update(sheets_bar, description=format_progress_update("All sheets processed ✓", "green"))
    return count