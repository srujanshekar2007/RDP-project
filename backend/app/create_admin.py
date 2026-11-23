import getpass

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import Role, User, utc_now
from app.security import hash_password


def main() -> None:
    email = input("Administrator email: ").strip().lower()
    name = input("Administrator name: ").strip()
    password = getpass.getpass("Administrator password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("Use a password with at least 12 characters.")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("An account already uses that email.")
        admin = User(email=email, full_name=name, password_hash=hash_password(password), role=Role.admin, created_at=utc_now())
        db.add(admin)
        db.commit()
        print(f"Administrator account created: {admin.email}")


if __name__ == "__main__":
    main()
