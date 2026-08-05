"""Refresh-token TOCTOU / concurrent rotation hardening tests.

Guarantees exactly one winner when two clients refresh the same parent
token. Never logs raw refresh tokens or JWT contents. No sleeps.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import RefreshTokenReuseError
from app.core.security.password import hash_password
from app.core.security.tokens import generate_refresh_token, hash_token
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.tests.database import get_test_engine


def _make_tenant_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Refresh TOCTOU Tenant {suffix}",
        slug=f"refresh-toctou-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"refresh-toctou-{suffix}@example.com",
        hashed_password=hash_password("correct-password"),
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _make_auth_service(session: Session) -> AuthService:
    return AuthService(
        TenantRepository(session),
        UserRepository(session),
        RefreshTokenRepository(session),
        session,
    )


def _count_family_tokens(session: Session, family_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.family_id == family_id)
        )
        or 0
    )


# --- repository primitives ----------------------------------------------------


def test_claim_rotation_succeeds_once(db_session: Session) -> None:
    _tenant, user = _make_tenant_user(db_session, suffix="claim-ok")
    now = datetime.now(timezone.utc)
    parent = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hash_token(generate_refresh_token()),
        expires_at=now + timedelta(days=30),
    )
    child_id = uuid.uuid4()
    child = RefreshToken(
        id=child_id,
        user_id=user.id,
        family_id=parent.family_id,
        token_hash=hash_token(generate_refresh_token()),
        expires_at=now + timedelta(days=30),
    )
    repo = RefreshTokenRepository(db_session)
    repo.create(parent)
    repo.create(child)
    db_session.flush()

    affected = repo.claim_rotation(
        parent.id, replaced_by_token_id=child_id, revoked_at=now
    )
    db_session.flush()
    db_session.refresh(parent)

    assert affected == 1
    assert parent.replaced_by_token_id == child_id
    assert parent.revoked_at == now


def test_claim_rotation_second_caller_gets_zero(db_session: Session) -> None:
    _tenant, user = _make_tenant_user(db_session, suffix="claim-race")
    now = datetime.now(timezone.utc)
    parent = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hash_token(generate_refresh_token()),
        expires_at=now + timedelta(days=30),
    )
    child_a = uuid.uuid4()
    child_b = uuid.uuid4()
    repo = RefreshTokenRepository(db_session)
    repo.create(parent)
    repo.create(
        RefreshToken(
            id=child_a,
            user_id=user.id,
            family_id=parent.family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
    )
    repo.create(
        RefreshToken(
            id=child_b,
            user_id=user.id,
            family_id=parent.family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
    )
    db_session.flush()

    first = repo.claim_rotation(
        parent.id, replaced_by_token_id=child_a, revoked_at=now
    )
    second = repo.claim_rotation(
        parent.id,
        replaced_by_token_id=child_b,
        revoked_at=now + timedelta(seconds=1),
    )
    db_session.refresh(parent)

    assert first == 1
    assert second == 0
    assert parent.replaced_by_token_id == child_a


def test_repository_methods_never_commit(db_session: Session) -> None:
    _tenant, user = _make_tenant_user(db_session, suffix="no-commit")
    now = datetime.now(timezone.utc)
    raw = generate_refresh_token()
    parent = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=hash_token(raw),
        expires_at=now + timedelta(days=30),
    )
    repo = RefreshTokenRepository(db_session)
    repo.create(parent)
    db_session.flush()
    parent_id = parent.id

    locked = repo.get_by_token_hash_for_update(hash_token(raw))
    assert locked is not None
    child_id = uuid.uuid4()
    repo.create(
        RefreshToken(
            id=child_id,
            user_id=user.id,
            family_id=parent.family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
    )
    db_session.flush()
    assert (
        repo.claim_rotation(
            parent_id, replaced_by_token_id=child_id, revoked_at=now
        )
        == 1
    )
    db_session.flush()
    db_session.rollback()

    assert db_session.get(RefreshToken, parent_id) is None


def test_concurrent_claim_rotation_one_winner() -> None:
    """Two threads race claim_rotation; conditional UPDATE yields exactly one winner."""
    engine = get_test_engine()
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)

    setup = Session(bind=engine)
    try:
        tenant = Tenant(
            name=f"Claim Race {suffix}",
            slug=f"claim-race-{suffix}",
        )
        setup.add(tenant)
        setup.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"claim-race-{suffix}@example.com",
            hashed_password=hash_password("correct-password"),
            role="member",
        )
        setup.add(user)
        setup.flush()
        family_id = uuid.uuid4()
        parent = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
        child_a = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
        child_b = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_token(generate_refresh_token()),
            expires_at=now + timedelta(days=30),
        )
        setup.add_all([parent, child_a, child_b])
        setup.commit()
        tenant_id = tenant.id
        parent_id = parent.id
        child_ids = (child_a.id, child_b.id)
    finally:
        setup.close()

    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def _worker(replaced_by: uuid.UUID, revoked_at: datetime) -> None:
        session = Session(bind=engine)
        try:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait()
            affected = RefreshTokenRepository(session).claim_rotation(
                parent_id,
                replaced_by_token_id=replaced_by,
                revoked_at=revoked_at,
            )
            session.commit()
            with lock:
                results.append(affected)
        finally:
            session.close()

    threads = [
        threading.Thread(
            target=_worker,
            args=(child_ids[0], now),
        ),
        threading.Thread(
            target=_worker,
            args=(child_ids[1], now + timedelta(seconds=1)),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)

    assert sorted(results) == [0, 1]

    verify = Session(bind=engine)
    try:
        parent = verify.get(RefreshToken, parent_id)
        assert parent is not None
        assert parent.replaced_by_token_id in child_ids
        assert parent.revoked_at is not None
    finally:
        verify.close()
        cleanup = Session(bind=engine)
        try:
            cleanup.execute(
                delete(RefreshToken).where(RefreshToken.family_id == family_id)
            )
            cleanup.execute(delete(User).where(User.tenant_id == tenant_id))
            cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup.commit()
        finally:
            cleanup.close()


# --- service concurrent refresh ----------------------------------------------


def test_concurrent_refresh_one_winner_one_loser() -> None:
    """Two threads refresh the same parent; exactly one TokenPair is issued."""
    engine = get_test_engine()
    suffix = uuid.uuid4().hex[:10]

    setup = Session(bind=engine)
    try:
        tenant = Tenant(
            name=f"Concurrent Refresh {suffix}",
            slug=f"concurrent-refresh-{suffix}",
        )
        setup.add(tenant)
        setup.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"concurrent-refresh-{suffix}@example.com",
            hashed_password=hash_password("correct-password"),
            role="member",
        )
        setup.add(user)
        setup.flush()
        tenant_id = tenant.id
        user_id = user.id
        auth = _make_auth_service(setup)
        pair = auth.login(tenant.slug, user.email, "correct-password")
        raw_parent = pair.refresh_token
        parent_hash = hash_token(raw_parent)
        parent = RefreshTokenRepository(setup).get_by_token_hash(parent_hash)
        assert parent is not None
        parent_id = parent.id
        family_id = parent.family_id
    finally:
        setup.close()

    winners: list[str] = []
    losers: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def _worker() -> None:
        session = Session(bind=engine)
        try:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            service = _make_auth_service(session)
            barrier.wait()
            result = service.refresh(raw_parent)
            with lock:
                winners.append(result.refresh_token)
        except BaseException as exc:
            with lock:
                losers.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)

    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], RefreshTokenReuseError)

    verify = Session(bind=engine)
    try:
        parent = verify.get(RefreshToken, parent_id)
        assert parent is not None
        assert parent.revoked_at is not None
        assert parent.replaced_by_token_id is not None

        children = list(
            verify.scalars(
                select(RefreshToken).where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.id != parent_id,
                )
            )
        )
        # Exactly one child issued (no duplicate rotation). Loser hits
        # reuse detection and revoke_family, so the child may also be revoked.
        assert len(children) == 1
        assert children[0].id == parent.replaced_by_token_id
        assert hash_token(winners[0]) == children[0].token_hash
        assert _count_family_tokens(verify, family_id) == 2
    finally:
        verify.close()

    cleanup = Session(bind=engine)
    try:
        cleanup.execute(
            delete(RefreshToken).where(RefreshToken.family_id == family_id)
        )
        cleanup.execute(delete(User).where(User.id == user_id))
        cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
        cleanup.commit()
    finally:
        cleanup.close()


def test_refresh_issues_single_child_under_lock(db_session: Session) -> None:
    tenant, user = _make_tenant_user(db_session, suffix="single")
    service = _make_auth_service(db_session)
    pair = service.login(tenant.slug, user.email, "correct-password")
    parent_hash = hash_token(pair.refresh_token)
    parent = RefreshTokenRepository(db_session).get_by_token_hash(parent_hash)
    assert parent is not None
    family_id = parent.family_id

    rotated = service.refresh(pair.refresh_token)
    assert rotated.refresh_token != pair.refresh_token
    assert rotated.access_token

    db_session.expire_all()
    parent = RefreshTokenRepository(db_session).get_by_token_hash(parent_hash)
    assert parent is not None
    assert parent.revoked_at is not None
    assert parent.replaced_by_token_id is not None
    assert _count_family_tokens(db_session, family_id) == 2

    with pytest.raises(RefreshTokenReuseError):
        service.refresh(pair.refresh_token)
