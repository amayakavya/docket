from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger("docket.database")


ROOT = Path(__file__).resolve().parents[2]
VAR_DIR = ROOT / "var"
VAR_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("Docket_OPS_DATABASE_URL", f"sqlite:///{VAR_DIR / 'docket_operations.db'}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_existing_db() -> None:
    """
    Add new columns to existing SQLite tables without a migrations framework.
    Each ALTER TABLE is wrapped in try/except — SQLite raises OperationalError
    if the column already exists, which we silently ignore.
    """
    migrations = [
        # CaseDepartment.priority — dept-local priority, independent of Case.priority
        "ALTER TABLE case_departments ADD COLUMN priority VARCHAR(32) NOT NULL DEFAULT 'LOW'",
        # Case.resolution_text — mandatory solution summary written when closing a case
        "ALTER TABLE cases ADD COLUMN resolution_text TEXT",
        # Case.queue_position — explicit operator-controlled sort order in central queue
        "ALTER TABLE cases ADD COLUMN queue_position INTEGER",
        # Case.intake_flags — smart-intake pre-flight results (thread/external provider/sparse)
        "ALTER TABLE cases ADD COLUMN intake_flags JSON",
        # Case.queue_pinned — True when operator has manually positioned a case
        "ALTER TABLE cases ADD COLUMN queue_pinned INTEGER NOT NULL DEFAULT 0",
        # Subscriber expanded fields
        "ALTER TABLE subscribers ADD COLUMN email VARCHAR(128)",
        "ALTER TABLE subscribers ADD COLUMN tax_id VARCHAR(12)",
        "ALTER TABLE subscribers ADD COLUMN id_last4 VARCHAR(8)",
        "ALTER TABLE subscribers ADD COLUMN date_of_birth VARCHAR(12)",
        "ALTER TABLE subscribers ADD COLUMN customer_segment VARCHAR(32) DEFAULT 'RETAIL'",
        "ALTER TABLE subscribers ADD COLUMN occupation VARCHAR(64)",
        "ALTER TABLE subscribers ADD COLUMN annual_income REAL",
        "ALTER TABLE subscribers ADD COLUMN payment_score INTEGER",
        "ALTER TABLE subscribers ADD COLUMN address JSON",
        "ALTER TABLE subscribers ADD COLUMN connection_details JSON",
        "ALTER TABLE subscribers ADD COLUMN device_details JSON",
        "ALTER TABLE subscribers ADD COLUMN contracts JSON",
        "ALTER TABLE subscribers ADD COLUMN addon_services JSON",
        "ALTER TABLE subscribers ADD COLUMN static_ip_blocks JSON",
        "ALTER TABLE subscribers ADD COLUMN premises_equipment JSON",
        "ALTER TABLE subscribers ADD COLUMN protection_plans JSON",
        "ALTER TABLE subscribers ADD COLUMN roaming_profile JSON",
        "ALTER TABLE subscribers ADD COLUMN account_manager VARCHAR(128)",
        "ALTER TABLE subscribers ADD COLUMN self_care_portal VARCHAR(32) DEFAULT 'ACTIVE'",
        "ALTER TABLE subscribers ADD COLUMN mobile_app_access VARCHAR(32) DEFAULT 'ACTIVE'",
        "ALTER TABLE subscribers ADD COLUMN sms_alerts INTEGER DEFAULT 1",
        "ALTER TABLE subscribers ADD COLUMN email_alerts INTEGER DEFAULT 1",
        "ALTER TABLE subscribers ADD COLUMN alternate_contact JSON",
        # Case.customer_customer_ref — customer reference of the matched customer at intake time
        "ALTER TABLE cases ADD COLUMN customer_customer_ref VARCHAR(16)",
        "CREATE INDEX IF NOT EXISTS ix_cases_customer_customer_ref ON cases(customer_customer_ref)",
    ]
    # subscribers table created by create_all; no column migrations needed yet
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.info("DB migration applied: %s", sql[:60])
            except Exception:
                pass  # column already exists — safe to ignore


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_existing_db()

    # Seed default department priority rules (idempotent)
    from .services.priority import seed_department_priority_rules
    from .services.seed_subscribers import seed_subscribers
    db = SessionLocal()
    try:
        inserted = seed_department_priority_rules(db)
        if inserted:
            logger.info("Seeded %d department priority rules", inserted)
        inserted_customers = seed_subscribers(db)
        if inserted_customers:
            logger.info("Seeded %d mock customer records", inserted_customers)
    finally:
        db.close()
