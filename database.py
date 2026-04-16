import psycopg2
import os
from datetime import date
from dotenv import load_dotenv
from helper import get_valid_date
from decimal import Decimal

load_dotenv()

DATABASE_URL = (
    os.getenv("DATABASE_URL_DEV")
    if os.getenv("APP_ENV") == "dev"
    else os.getenv("DATABASE_URL_PROD")
)

# -------------------------
# DB CONNECTION
# -------------------------


def connect_db():
    if not DATABASE_URL or "neon.tech" not in DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing or not pointing to Neon")
    return psycopg2.connect(DATABASE_URL)


# -------------------------
# GENERIC INSERT HELPER
# -------------------------


def insert_row(table, columns, values):
    instances = ", ".join(["%s"] * len(values))
    col_names = ", ".join(columns)

    query = f" INSERT INTO {table} ({col_names}) VALUES ({instances}) RETURNING *;"
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, values)
            row = cur.fetchone()
            conn.commit()
            print(f"\n✅ Inserted into {table}: {row}\n")
            return row


# -------------------------
# ADDERS
# -------------------------


def add_student():
    print("\nAdd Student")
    values = [
        input("Legal first name: "),
        input("Legal last name: "),
        input("Preferred name (optional): ") or None,
        input("Grade (optional): ") or None,
        input("City: "),
        input("Email (currently optional):") or None,
        input("Phone (optional): ") or None,
        input("Preferred communication (email / text message): "),
        input("How did they find us? (optional): ") or None,
    ]

    insert_row(
        "students",
        [
            "gov_first_name",
            "gov_last_name",
            "pref_name",
            "grade",
            "city",
            "email",
            "phone",
            "pref_communication",
            "how_found_us",
        ],
        values,
    )


def add_guardian():
    print("\nAdd Guardian")
    values = [
        input("Legal first name: "),
        input("Legal last name: "),
        input("Preferred name (optional): ") or None,
        input("Email: "),
        input("Phone (optional): ") or None,
        input("Preferred communication (email / text message): "),
    ]

    insert_row(
        "guardians",
        [
            "gov_first_name",
            "gov_last_name",
            "pref_name",
            "email",
            "phone",
            "pref_communication",
        ],
        values,
    )


def add_tutor():
    print("\nAdd Tutor")
    values = [
        input("Legal first name: "),
        input("Legal last name: "),
        input("Preferred name (optional): ") or None,
        input("Email: "),
        input("Phone: "),
        date.fromisoformat(input("Date hired (YYYY-MM-DD): ")),
        int(input("Prior experience (years): ")),
        float(input("Current hourly rate: ")),
        input("Emergency contact name: "),
        input("Emergency contact phone: "),
        input("Emergency contact relationship (optional): ") or None,
        input("Availability (optional): ") or None,
        input("In person? (true/false): ").lower() == "true",
        input("Location (optional): ") or None,
        input("Subjects: "),
    ]

    insert_row(
        "tutors",
        [
            "gov_first_name",
            "gov_last_name",
            "pref_name",
            "email",
            "phone",
            "date_hired",
            "prior_experience",
            "current_rate",
            "emerg_contact_name",
            "emerg_contact_phone",
            "emerg_contact_relationship",
            "availability",
            "in_person",
            "location",
            "subjects",
        ],
        values,
    )


def add_student_guardian():
    print("\nLink Student ↔ Guardian")
    values = [
        int(input("Student ID: ")),
        int(input("Guardian ID: ")),
        input("Relationship type (optional): ") or None,
        input("Is primary biller? (true/false): ").lower() == "true",
    ]

    insert_row(
        "student_guardian",
        ["student_id", "guardian_id", "relationship_type", "is_primary_biller"],
        values,
    )


def add_billing_account():
    print("\nAdd Billing Account")
    values = [
        input("Type (guardian / student): "),
        int(input("Owner ID: ")),
        input("Display name: "),
        input("Email: "),
        input("First invoice? (true/false): ").lower() == "true",
    ]

    insert_row(
        "billing_accounts",
        ["type", "owner_id", "display_name", "email", "first_invoice"],
        values,
    )


def add_student_billing():
    print("\nLink Student → Billing Account")
    values = [int(input("Student ID: ")), int(input("Billing Account ID: "))]

    insert_row("student_billing", ["student_id", "billing_id"], values)


def add_student_tutor():
    print("\nAssign Tutor to Student")
    values = [
        int(input("Student ID: ")),
        int(input("Tutor ID: ")),
        float(input("Usual duration (hours): ")),
        float(input("Hourly rate: ")),
        input("Subjects: "),
    ]

    insert_row(
        "student_tutor",
        ["student_id", "tutor_id", "usual_duration", "hourly_rate", "subjects"],
        values,
    )


def add_payment():
    print("\nAdd Payment")
    values = [
        int(input("Billing ID: ")),
        int(input("Invoice ID: ")),
        Decimal(input("Amount: ")),
        get_valid_date("Date: "),
        input("Method (eTransfer, cash) [default: 'eTransfer']: ") or "eTransfer",
    ]

    insert_row(
        "payments",
        ["billing_id", "invoice_id", "amount", "payment_date", "method"],
        values,
    )


# -------------------------
# MAIN CLI
# -------------------------


def main():
    actions = {
        "1": add_student,
        "2": add_guardian,
        "3": add_tutor,
        "4": add_student_guardian,
        "5": add_billing_account,
        "6": add_student_billing,
        "7": add_student_tutor,
        "8": add_payment,
    }

    while True:
        print(
            "\nChoose an action:\n1. Add student\n2. Add guardian\n3. Add tutor\n4. Link student & guardian\n5. Add billing account\n6. Link student to billing account\n7. Assign tutor to student\n8. Record payment\nQ. Quit"
        )

        choice = input(">> ").strip().upper()

        if choice == "Q":
            break
        elif choice in actions:
            actions[choice]()
        else:
            print("❌ Invalid option")


if __name__ == "__main__":
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), inet_server_addr();")
            print(cur.fetchone())
    main()
