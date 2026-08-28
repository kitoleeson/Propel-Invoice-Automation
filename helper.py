# All with help from Google Gemini
import logging
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader
from rich.logging import RichHandler
from rich.progress import Progress

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler()])
logger = logging.getLogger(__name__)


# Progress updates
def track_progress(
    sequence,
    task_title: str,
    completed_title: str,
    description_fn=None,
    progress_obj=None,
):
    """
    Wraps an iterable with Rich Progress bar tracking.
    With help from Google Gemini.
    """
    items = list(sequence)

    def _run_with_progress(progress):
        bar = progress.add_task(
            format_progress_update(task_title, "cyan"),
            total=len(items),
        )
        for item in items:
            if description_fn:
                progress.update(
                    bar,
                    description=format_progress_update(description_fn(item), "yellow"),
                )
            yield item
            progress.update(bar, advance=1)
        progress.update(
            bar,
            description=format_progress_update(completed_title, "green"),
        )

    if progress_obj:
        yield from _run_with_progress(progress_obj)
    else:
        with Progress() as progress:
            yield from _run_with_progress(progress)


def format_progress_update(string: str, colour: str = "") -> str:
    """Format a progress update string with a checkmark."""
    return f"[{colour}]{string:<40}" if colour else f"{string:<40}"


# User input validation
def get_valid_yes_no(query: str) -> bool:
    """Collects a valid yes/no user input. Returns True for 'yes' and False for 'no'."""
    valid_responses = ["yes", "y", "no", "n"]
    response = input(f"{query} (yes/no): ").strip().lower()
    while response not in valid_responses:
        print("Invalid input. Please enter 'yes' or 'no'.")
        response = input(f"{query} (yes/no): ").strip().lower()
    return response in ["yes", "y"]


def get_valid_date(query: str) -> date:
    while True:
        date_str = input(query).strip()
        try:
            return (
                datetime.strptime(date_str, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc)
                .date()
            )
        except ValueError:
            print("Invalid date format. Please use YYYY-MM-DD.")


# Data processing
def parse_session(session: dict):
    """
    Parse session data from the database and return a structured format.
    Update on session_billing_details view update.
    Returns:
        session (dict): A single session record
    """
    return {
        "billing_id": session["billing_id"],
        "biller_name": session["biller_name"],
        "student_id": session["student_id"],
        "student_name": session["student_name"],
        "student_pref_name": session["student_pref_name"],
        "tutor_id": session["tutor_id"],
        "tutor_name": session["tutor_name"],
        "first_session": session["first_session"],
        "session_date": session["session_date"].strftime("%b %d"),
        "duration_hours": float(session["duration_hours"]),
        "applied_travel_fee": float(session["applied_travel_fee"]),
        "applied_rush_fee": float(session["applied_rush_fee"]),
        "hourly_rate": float(session["hourly_rate"]),
        "total_fee": float(session["total_fee"]),
        "total_tutor_fee": float(session["total_tutor_fee"]),
        "total_profit": float(session["total_profit"]),
        "subjects": session["subjects"],
    }


LOCAL_TZ = ZoneInfo("America/Edmonton")


def get_today() -> date:
    """Returns the current date in the local timezone."""
    return datetime.now(tz=LOCAL_TZ).date()


# Jinja2 template rendering
TEMPLATES_DIR = Path(__file__).parent

latex_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR / "latex_templates"),
    autoescape=False,
)

email_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR / "email_bodies"),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_latex_template(template_name: str, context: dict) -> str:
    """Renders LaTeX templates with LaTeX-safe configurations."""
    return latex_env.get_template(template_name).render(**context)


def render_email_template(template_name: str, context: dict) -> str:
    """Renders plain text email templates with whitespace trimming."""
    return email_env.get_template(template_name).render(**context)


def cleanup_latex_artifacts(directory: Path, keep_exts=None):
    """
    Cleanup LaTeX artifacts in the specified directory, keeping only files with specified extensions.
    Arguments:
        directory (Path): Directory to clean up
        keep_exts (set): Set of file extensions to keep
    Returns:
        None
    """
    if keep_exts is None:
        keep_exts = {".pdf", ".tex"}
    for file in directory.iterdir():
        if file.is_file() and file.suffix not in keep_exts:
            file.unlink()


def compile_latex_to_pdf(tex_path: Path, output_dir: Path) -> Path:
    """
    Compiles a .tex file to PDF using pdflatex and cleans up build artifacts.
    Returns the path to the compiled .pdf file.
    """
    pdf_path = output_dir / f"{tex_path.stem}.pdf"

    try:
        subprocess.run(
            [
                "pdflatex",
                "-interaction=batchmode",
                "-include-directory=.",
                "-output-directory",
                str(output_dir),
                str(tex_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return pdf_path

    except subprocess.CalledProcessError as err:
        log_file = output_dir / f"{tex_path.stem}.log"
        if log_file.exists():
            log_tail = "\n".join(log_file.read_text().splitlines()[-20:])
            logger.error(f"LaTeX Compilation Failed for {tex_path.name}:\n{log_tail}")
        else:
            logger.error(f"pdflatex process failed with return code {err.returncode}")
        raise

    finally:
        cleanup_latex_artifacts(output_dir)
