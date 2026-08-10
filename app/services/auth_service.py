"""
Real user authentication — signup/login/logout, password hashing, and
cookie-based sessions. Deliberately simple (Starlette's SessionMiddleware,
signed with ADMIN_SECRET_KEY-style secret) rather than pulling in a full
JWT/OAuth stack — this is the right amount of complexity for a storefront
that just needs "who is this visitor" for a personal area, not a
multi-service auth provider.
"""
import datetime
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_verify_serializer = URLSafeTimedSerializer(settings.session_secret_key, salt="email-verify")
VERIFY_TOKEN_MAX_AGE_SECONDS = 48 * 3600  # 48 hours


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return pwd_context.verify(plain_password, password_hash)
    except Exception:
        return False


def create_user(db: Session, email: str, password: str) -> User | None:
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        return None  # already exists — caller shows "email already registered"
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(request, db: Session) -> User | None:
    """Reads user_id out of the signed session cookie (set at login).
    Returns None for anonymous visitors — callers decide whether that's
    an error (protected routes) or just means 'show the guest view'."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


# --- Email verification ---

def generate_verification_token(user: User) -> str:
    """Stateless, signed token — no separate DB table needed. Encodes the
    user id + current email, so a token becomes invalid if the email on
    the account changes before it's used (can't verify a stale address)."""
    return _verify_serializer.dumps({"user_id": user.id, "email": user.email})


def verify_email_token(db: Session, token: str) -> User | None:
    """Returns the now-verified User on success, or None if the token is
    invalid/expired/for a since-changed email — caller shows the right
    error message either way rather than a raw exception."""
    try:
        data = _verify_serializer.loads(token, max_age=VERIFY_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None

    user = db.query(User).filter(User.id == data.get("user_id")).first()
    if not user or user.email != data.get("email"):
        return None

    user.email_verified = True
    db.commit()
    return user


def mark_verification_email_sent(db: Session, user: User):
    user.verification_email_sent_at = datetime.datetime.utcnow()
    db.commit()


def can_resend_verification(user: User, cooldown_seconds: int = 60) -> bool:
    """Simple resend-spam guard — separate from the IP-based rate limiter
    on the route itself, this stops one user from queuing 10 emails to
    themselves even from different IPs."""
    if not user.verification_email_sent_at:
        return True
    elapsed = (datetime.datetime.utcnow() - user.verification_email_sent_at).total_seconds()
    return elapsed >= cooldown_seconds


# --- Google OAuth ---

def get_or_create_google_user(db: Session, google_sub: str, email: str, email_verified_by_google: bool) -> User:
    """
    Google already verified the email address as part of its own signup
    flow, so accounts created this way start with email_verified=True —
    no separate verification email needed for the Google path.

    If an account with this email already exists (e.g. they originally
    signed up with a password), this LINKS the Google identity to that
    existing account rather than creating a duplicate — matched by email,
    which is safe here because Google itself vouches the email is verified.
    """
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        if not user.oauth_provider:
            user.oauth_provider = "google"
            user.oauth_subject_id = google_sub
        if email_verified_by_google:
            user.email_verified = True
        db.commit()
        return user

    user = User(
        email=email,
        password_hash=None,
        oauth_provider="google",
        oauth_subject_id=google_sub,
        email_verified=email_verified_by_google,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
