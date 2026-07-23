"""Test OAuth account find-or-create/link logic (no provider round-trip)."""
import asyncio

from sqlalchemy import select

from app.database import SessionLocal
from app.models import OAuthAccount, User
from app.routers.oauth import find_or_create_user


async def main():
    async with SessionLocal() as db:
        # 1. brand-new external identity -> new passwordless user
        norm = {
            "sub": "g-newuser-001",
            "email": "sso.newperson@example.com",
            "email_verified": True,
            "name": "SSO New Person",
        }
        u1 = await find_or_create_user(db, "google", norm)
        await db.commit()
        print("created username:", u1.username, "| password_hash is None:", u1.password_hash is None)
        print("PASS new user created:", u1.username.startswith("ssonewperson"))

        # 2. same identity again -> same user (no duplicate)
        u2 = await find_or_create_user(db, "google", norm)
        await db.commit()
        print("PASS same identity returns same user:", u1.id == u2.id)

        # 3. link to an existing account by VERIFIED email (alice registered w/ pw)
        alice = (
            await db.execute(select(User).where(User.username == "alice"))
        ).scalar_one()
        link = {
            "sub": "g-alice-999",
            "email": alice.email,
            "email_verified": True,
            "name": "Alice via Google",
        }
        linked = await find_or_create_user(db, "google", link)
        await db.commit()
        print("PASS verified-email links to existing user:", linked.id == alice.id)

        # 4. UNVERIFIED email must NOT link — creates a separate account
        unv = {
            "sub": "g-attacker-000",
            "email": alice.email,
            "email_verified": False,
            "name": "Not Alice",
        }
        other = await find_or_create_user(db, "google", unv)
        await db.commit()
        print("PASS unverified email does NOT hijack account:", other.id != alice.id)

        # confirm oauth_accounts rows exist
        cnt = len(
            (
                await db.execute(
                    select(OAuthAccount).where(OAuthAccount.provider == "google")
                )
            ).scalars().all()
        )
        print("google oauth_accounts rows:", cnt)


asyncio.run(main())
