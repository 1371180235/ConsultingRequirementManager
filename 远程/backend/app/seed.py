from __future__ import annotations

import os
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal
from .models import RequirementTag, Tag, User
from .security import hash_password, password_policy_error


DEFAULT_TAGS = (
    ("业务痛点", "#DC2626"),
    ("功能优化", "#2563EB"),
    ("运维 Bug", "#D97706"),
    ("招投标要求", "#7C3AED"),
    ("验收整改", "#DB2777"),
    ("客户新增", "#0891B2"),
    ("版本必做", "#059669"),
    ("待确认", "#64748B"),
)

LEGACY_TAG_ALIASES = {"运维Bug": "运维 Bug"}


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def normalize_legacy_tags(db: Session) -> None:
    for legacy_name, canonical_name in LEGACY_TAG_ALIASES.items():
        legacy = db.scalar(select(Tag).where(Tag.name == legacy_name))
        if not legacy:
            continue
        canonical = db.scalar(select(Tag).where(Tag.name == canonical_name))
        if not canonical:
            legacy.name = canonical_name
            db.flush()
            continue

        canonical_requirement_ids = set(
            db.scalars(
                select(RequirementTag.requirement_id).where(
                    RequirementTag.tag_id == canonical.id
                )
            ).all()
        )
        if canonical_requirement_ids:
            db.execute(
                delete(RequirementTag).where(
                    RequirementTag.tag_id == legacy.id,
                    RequirementTag.requirement_id.in_(canonical_requirement_ids),
                )
            )
        db.execute(
            update(RequirementTag)
            .where(RequirementTag.tag_id == legacy.id)
            .values(tag_id=canonical.id)
        )
        db.delete(legacy)
        db.flush()


def seed_database(db: Session, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    username = settings.admin_username.strip().casefold()
    if not USERNAME_PATTERN.fullmatch(username):
        raise RuntimeError("ADMIN_USERNAME must contain 3-64 letters, digits, dots, underscores, or hyphens")

    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    configured_user = db.scalar(select(User).where(User.username == username))
    created_admin = False
    if not configured_user:
        if user_count:
            raise RuntimeError(
                "ADMIN_USERNAME does not match the initialized administrator; "
                "do not change it after the first successful bootstrap"
            )
        password = settings.admin_password or ("Admin@123456" if settings.app_env != "production" else "")
        if not password:
            raise RuntimeError("Production requires ADMIN_PASSWORD when creating the initial administrator")
        if error := password_policy_error(password):
            raise RuntimeError(f"ADMIN_PASSWORD is invalid: {error}")
        db.add(
            User(
                username=username,
                full_name="系统管理员",
                password_hash=hash_password(password),
                role="admin",
                must_change_password=True,
            )
        )
        created_admin = True
    elif configured_user.role != "admin":
        raise RuntimeError("ADMIN_USERNAME belongs to a non-administrator account")
    normalize_legacy_tags(db)
    existing = set(db.scalars(select(Tag.name)).all())
    for name, color in DEFAULT_TAGS:
        if name not in existing:
            db.add(Tag(name=name, color=color))
    db.commit()
    return created_admin


def main() -> None:
    with SessionLocal() as db:
        created_admin = seed_database(db)
    if created_admin:
        print("Database initialized. The initial administrator must change the password at first login.")
    else:
        print("Database seed data is up to date.")


if __name__ == "__main__":
    main()
