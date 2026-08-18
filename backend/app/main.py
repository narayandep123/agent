from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_routes import admin_router, router as auth_router
from app.api.routes import router
from app.auth.security import hash_password
from app.db import Base, SessionLocal, engine
from app.db_models import User

# Create tables at import time so the app (and the test client, which does not run
# lifespan events) always has a ready schema.
Base.metadata.create_all(bind=engine)

# SQLite's default UNIQUE comparison is case-sensitive and the original schema
# predates roll-number uniqueness. These indexes enforce identity uniqueness at
# the database layer as well as in the signup validation, including concurrent
# requests. Empty roll numbers remain allowed for accounts that do not have one.
with engine.begin() as connection:
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_nocase ON users(email COLLATE NOCASE)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_roll_no_nocase "
        "ON users(roll_no COLLATE NOCASE) WHERE roll_no <> ''"
    )


def seed_admin() -> None:
    """Ensure a bootstrap administrator exists so faculty/staff can be approved."""
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == "ADMIN").first():
            db.add(User(
                name="Campus Administrator",
                roll_no="ADMIN-001",
                email="admin@campusflow.edu",
                mobile="9999999999",
                role="ADMIN",
                status="ACTIVE",
                password_hash=hash_password("admin123"),
            ))
            db.commit()
    finally:
        db.close()


seed_admin()

app = FastAPI(title="CampusFlow AI", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(router)

@app.get("/health")
def health(): return {"status": "ok", "service": "campusflow-api"}
