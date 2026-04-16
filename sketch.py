#!/usr/bin/env python3
"""
Main entry point for running the biweekly billing cycle.
Orchestrates the full billing process, including:
    • Ingesting new sessions from Sheets (eventually web input)
    • Generating student invoices
    • Generating tutor payroll summary
    • Emailing invoices
Arguments:
    None
Returns:
    None
"""

# Imports
from datetime import date, timedelta
import logging
from rich.logging import RichHandler
import os
from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[RichHandler()],
)

# Module imports
from sheets_ingest import ingest_sessions
from invoice import generate_and_send_invoices
from helper import get_valid_yes_no


# Main function
def run_billing_cycle(biweek_start: date = date.today() - timedelta(days=14)):
    """
    Run the biweekly billing cycle.
    This function performs the following steps:
        1. Ingest new sessions from the data source
        2. Generate and send student invoices
        3. Generate tutor payroll summary
    Arguments:
        biweek_start (date): The start date of the 2-week billing period. Defaults to 14 days before the current date.
    Returns:
        None
    """
    biweek_end = biweek_start + timedelta(days=14)
    logging.info(
        f"Starting billing cycle for period: {biweek_start} (inclusive) → {biweek_end} (exclusive)"
    )
    if get_valid_yes_no("Would you like to ingest new sessions?"):
        ingest_sessions(biweek_start, biweek_end)
    if get_valid_yes_no("Would you like to generate and send invoices?"):
        generate_and_send_invoices(biweek_start, biweek_end)


# Manual entry point
if __name__ == "__main__":
    import argparse

    valid_production_environments = ["prod", "dev", "test"]
    if os.getenv("APP_ENV") not in valid_production_environments:
        logging.warning("APP_ENV is not valid. Please check your .env configuration.")

    parser = argparse.ArgumentParser(description="Run the biweekly billing cycle")
    parser.add_argument(
        "--start", type=str, help="Optional start date of biweek (YYYY-MM-DD)"
    )
    args = parser.parse_args()
    try:
        biweek_start = (
            date.fromisoformat(args.start)
            if args.start
            else date.today() - timedelta(days=14)
        )
        if biweek_start > date.today():
            logging.error(f"Start date {biweek_start} cannot be in the future.")
            exit(1)
    except ValueError:
        logging.error(f"Invalid date format for --start: {args.start}. Use YYYY-MM-DD.")
        exit(1)

    run_billing_cycle(biweek_start)
