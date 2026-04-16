-- schema.sql
BEGIN;

-- =========================
-- DROP TABLES
-- =========================

-- DROP TABLE IF EXISTS payments;
-- DROP TABLE IF EXISTS invoices;
-- DROP TABLE IF EXISTS student_billing;
-- DROP TABLE IF EXISTS billing_accounts;
-- DROP TABLE IF EXISTS sessions;
-- DROP TABLE IF EXISTS student_tutor;
-- DROP TABLE IF EXISTS payroll_entries;
-- DROP TABLE IF EXISTS payroll;
-- DROP TABLE IF EXISTS student_guardian;
-- DROP TABLE IF EXISTS tutors;
-- DROP TABLE IF EXISTS tutor_subjects;
-- DROP TABLE IF EXISTS guardians;
-- DROP TABLE IF EXISTS students;

-- SELECT table_name
-- FROM information_schema.tables
-- WHERE table_schema = 'public'
-- ORDER BY table_name;

-- COMMIT;

-- BEGIN;

-- =========================
-- CLIENTS
-- =========================

CREATE TABLE IF NOT EXISTS students (
    student_id SERIAL PRIMARY KEY,
    gov_first_name TEXT NOT NULL,
    gov_last_name TEXT NOT NULL,
    pref_name TEXT,
    grade INTEGER NOT NULL CHECK (grade >= 1 AND grade <= 12),
    city TEXT NOT NULL,
    -- email TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    phone TEXT,
    pref_communication TEXT NOT NULL
        CHECK (pref_communication IN ('email','text message')),
    how_found_us TEXT
        CHECK (how_found_us IN ('teacher', 'word of mouth', 'advertisement', 'web search', 'other'))
);
-- inputed: through the website form during sign up
-- updated: through interface when student details change, eventually app
-- deleted: never deleted
-- email is currently nullable. add not null afterwards:
-- ALTER TABLE students
-- ALTER COLUMN email SET NOT NULL;

CREATE TABLE IF NOT EXISTS guardians (
    guardian_id SERIAL PRIMARY KEY,
    gov_first_name TEXT NOT NULL,
    gov_last_name TEXT NOT NULL,
    pref_name TEXT,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    pref_communication TEXT NOT NULL
        CHECK (pref_communication IN ('email','text message'))
);
-- inputed: through the website form during sign up
-- updated: through interface when guardian details change, eventually app
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS student_guardian (
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    guardian_id INTEGER NOT NULL REFERENCES guardians(guardian_id),
    relationship_type TEXT,
    is_primary_biller BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (student_id, guardian_id)
);
-- inputed: through the website form during sign up
-- inputed: through interface when a guardian is added to a student profile
-- updated: through interface when is_primary_biller is changed
-- deleted: through interface when a guardian is removed from a student profile

CREATE UNIQUE INDEX IF NOT EXISTS one_primary_biller
ON student_guardian (student_id)
WHERE is_primary_biller;

-- =========================
-- TUTORS
-- =========================
CREATE TYPE tutoring_mode AS ENUM ('Online Only', 'In-Person Only', 'Hybrid');

CREATE TABLE IF NOT EXISTS tutors (
    tutor_id SERIAL PRIMARY KEY,
    
    gov_first_name TEXT NOT NULL,
    gov_last_name TEXT NOT NULL,
    pref_name TEXT,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL UNIQUE,
    
    date_hired DATE NOT NULL,
    prior_experience INTEGER NOT NULL DEFAULT 0,
    current_rate NUMERIC(6, 2) NOT NULL DEFAULT 25,
    accepting_students INTEGER NOT NULL DEFAULT 0,
    
    emerg_contact_name TEXT NOT NULL,
    emerg_contact_phone TEXT NOT NULL,
    emerg_contact_relationship TEXT,

    availability TEXT,
    in_person tutoring_mode NOT NULL DEFAULT 'Hybrid',
    city TEXT,
    location TEXT,
    
    subjects TEXT NOT NULL,
    
    current_uni TEXT,
    current_degree TEXT,
    field_of_study TEXT,
    year_of_study INTEGER,
    current_fav_class TEXT,
    academic_interests TEXT,

    bio TEXT,
    hobbies TEXT,

    high_school TEXT,
    high_school_city TEXT,
    fav_high_school_class TEXT,
    ap_ib_credentials TEXT
);
-- inputed: through the website during onboarding form
-- updated: through the website during semesterly forms when tutor details change
-- updated: through interface when tutor details change
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS tutor_subjects (
    tutor_subject_id SERIAL PRIMARY KEY,
    tutor_id INTEGER NOT NULL REFERENCES tutors(tutor_id),
    subject TEXT NOT NULL,
    UNIQUE (tutor_id, subject)
);
-- inputed: through the website during onboarding form
-- updated/deleted: through the website during semesterly forms when tutor details change
-- updated/deleted: through interface when tutor details change

-- =========================
-- SESSIONS
-- =========================

CREATE TABLE IF NOT EXISTS student_tutor (
    assignment_id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    tutor_id INTEGER NOT NULL REFERENCES tutors(tutor_id),
    usual_duration NUMERIC(4, 2) NOT NULL,
    hourly_rate NUMERIC(6, 2) NOT NULL,
    subjects TEXT NOT NULL,
    UNIQUE (student_id, tutor_id)
);
-- inputed: through interface when a tutor is assigned to a student
-- updated: through interface when assignment details change
-- updated: through invoice system when count is incremented
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS sessions (
    session_id SERIAL PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES student_tutor(assignment_id),
    session_date DATE NOT NULL,
    duration_hours NUMERIC(4, 2) NOT NULL CHECK (duration_hours > 0),
    exam_prep BOOLEAN DEFAULT FALSE,
    notes TEXT
);
-- inputed: through interface or sheets ingestion when a session is completed
-- updated: through interface when session details change
-- deleted: never deleted

CREATE UNIQUE INDEX IF NOT EXISTS one_session_per_day
ON sessions (assignment_id, session_date);

-- =========================
-- BILLING
-- =========================

CREATE TABLE IF NOT EXISTS billing_accounts (
    billing_id SERIAL PRIMARY KEY,
    type TEXT NOT NULL
        CHECK (type IN ('guardian','student')),
    owner_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    first_invoice BOOLEAN DEFAULT TRUE
);
-- inputed: through website when a student chooses a billing account during sign up
-- updated: through interface when billing account details change
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS student_billing (
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    billing_id INTEGER NOT NULL REFERENCES billing_accounts(billing_id),
    PRIMARY KEY (student_id)
);
-- inputed: through website when a student chooses a billing account during sign up
-- updated: through interface when a student's billing account changes
-- deleted: never deleted

-- =========================
-- INVOICING
-- =========================

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id SERIAL PRIMARY KEY,
    billing_id INTEGER NOT NULL REFERENCES billing_accounts(billing_id),
    biweek_start DATE NOT NULL,
    date_sent DATE NOT NULL,
    total_hours NUMERIC(5, 2) NOT NULL CHECK (total_hours > 0),
    total_amount NUMERIC(8, 2) NOT NULL,
    UNIQUE (billing_id, biweek_start)
);
-- inputed: through invoice system when an invoice is generated
-- updated: never updated
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS payments (
    payment_id SERIAL PRIMARY KEY,
    billing_id INTEGER NOT NULL REFERENCES billing_accounts(billing_id),
    invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id),
    amount NUMERIC(8, 2) NOT NULL CHECK (amount > 0),
    payment_date DATE NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('eTransfer', 'cash'))
);
-- inputed: through invoice system when a payment is recorded
-- updated: never updated
-- deleted: never deleted

-- =========================
-- PAYROLL
-- =========================

CREATE TABLE IF NOT EXISTS payroll (
    payroll_id SERIAL PRIMARY KEY,
    biweek_start DATE NOT NULL UNIQUE,
    total_hours NUMERIC(5, 2) NOT NULL CHECK (total_hours > 0),
    total_amount NUMERIC(8, 2) NOT NULL CHECK (total_amount > 0),
    date_paid DATE NOT NULL,
    date_generated DATE NOT NULL DEFAULT CURRENT_DATE
);
-- inputed: through invoice system when payroll completed
-- updated: never updated
-- deleted: never deleted

CREATE TABLE IF NOT EXISTS payroll_entries (
    entry_id SERIAL PRIMARY KEY,
    payroll_id INTEGER NOT NULL REFERENCES payroll(payroll_id) ON DELETE RESTRICT,
    tutor_id INTEGER NOT NULL REFERENCES tutors(tutor_id),
    total_hours NUMERIC(5, 2) NOT NULL CHECK (total_hours > 0),
    total_amount NUMERIC(8, 2) NOT NULL CHECK (total_amount > 0),
    UNIQUE(payroll_id, tutor_id)
);
-- inputed: through invoice system when payroll completed
-- updated: never updated
-- deleted: never deleted

COMMIT;

-- ADD UNIQUE CONSTRAINT BACK TO EMAIL AFTER TESTING
-- ALTER TABLE billing_accounts
-- ADD CONSTRAINT billing_accounts_email_key UNIQUE (email);

-- -- invoices.invoice_id
-- SELECT setval(
--     pg_get_serial_sequence('invoices', 'invoice_id'),
--     (SELECT COALESCE(MAX(invoice_id), 1) FROM invoices)
-- );

-- -- payroll.payroll_id
-- SELECT setval(
--     pg_get_serial_sequence('payroll', 'payroll_id'),
--     (SELECT COALESCE(MAX(payroll_id), 1) FROM payroll)
-- );

-- -- payroll_entries.entry_id
-- SELECT setval(
--     pg_get_serial_sequence('payroll_entries', 'entry_id'),
--     (SELECT COALESCE(MAX(entry_id), 1) FROM payroll_entries)
-- );

-- -- sessions.session_id
-- SELECT setval(
--     pg_get_serial_sequence('sessions', 'session_id'),
--     (SELECT COALESCE(MAX(session_id), 1) FROM sessions)
-- );