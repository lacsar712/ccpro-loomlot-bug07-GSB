#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
python - <<'PY'
import os, time
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
for i in range(60):
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Database is ready.")
        break
    except Exception as e:
        print(f"DB not ready ({i+1}/60): {e}")
        time.sleep(2)
else:
    raise SystemExit("Database not ready after retries")
PY

echo "Creating tables..."
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"

echo "Ensuring vats unique constraint..."
python - <<'PY'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as conn:
    duplicated = conn.execute(text(
        "SELECT COUNT(*) FROM ("
        " SELECT 1 FROM vats GROUP BY dye_house_id, vat_code HAVING COUNT(*) > 1"
        ") t"
    )).scalar()
    exists = conn.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname = 'uq_vat_house_code'"
    )).first()
    if exists:
        print("Constraint uq_vat_house_code already exists.")
    elif duplicated:
        print("WARNING: duplicate (dye_house_id, vat_code) rows exist; "
              "resolve them before constraint uq_vat_house_code can be added.")
    else:
        conn.execute(text(
            "ALTER TABLE vats ADD CONSTRAINT uq_vat_house_code "
            "UNIQUE (dye_house_id, vat_code)"
        ))
        print("Constraint uq_vat_house_code added.")
PY

echo "Seeding data..."
python -c "from app.seed import seed; seed()"

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8600
