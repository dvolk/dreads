import datetime
import hmac
import logging
import math
import mimetypes
import os
import posixpath
import re
import secrets
import sqlite3
import threading
import time
import pathlib
import collections
import shlex

import argh
import ebooklib
import humanize
from bleach import clean, sanitizer
from bs4 import BeautifulSoup
from ebooklib import epub
from flask import (
    Flask,
    Response,
    abort,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy import event
from sqlalchemy.engine import Engine
from tqdm import tqdm
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import waitress
import tabulate

WEAK_SECRET_KEYS = {
    "supersecret",
    "changeme",
    "change-me",
    "secret",
    "password",
    "test",
    "dev",
}


def load_secret_key(flask_app):
    """Return a strong signing key, ignoring weak env values.

    A persisted instance/secret_key keeps sessions valid across restarts when
    the deployment forgets to provide CATREADS_SECRET_KEY.
    """
    env_key = (os.environ.get("CATREADS_SECRET_KEY") or "").strip()
    if len(env_key) >= 32 and env_key.lower() not in WEAK_SECRET_KEYS:
        return env_key
    if env_key:
        logging.warning(
            "Ignoring weak CATREADS_SECRET_KEY (must be at least 32 characters)"
        )

    key_path = pathlib.Path(flask_app.instance_path) / "secret_key"
    try:
        if key_path.is_file():
            persisted = key_path.read_text(encoding="utf-8").strip()
            if len(persisted) >= 32:
                os.chmod(key_path, 0o600)
                return persisted
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(64)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
        os.chmod(key_path, 0o600)
        logging.warning("Generated a persistent secret key at %s", key_path)
        return key
    except OSError:
        logging.exception(
            "Could not persist a secret key; sessions will not survive restarts"
        )
        return secrets.token_urlsafe(64)


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = load_secret_key(app)
app.config["SESSION_COOKIE_NAME"] = "catreads_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
    "CATREADS_HTTPS", ""
).lower() in {"1", "true", "yes", "on"}
# Browsers cap cookie lifetime at 400 days, so this is the longest practical
# "stay logged in" value. SESSION_REFRESH_EACH_REQUEST slides the expiry
# forward on every visit, so an actively used session does not expire.
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=400)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 99999999999

# Flask only autoescapes .html/.xml/.svg by default, not .jinja2. Book
# metadata and tags are EPUB/user-controlled, so escape every template; the
# sanitized chapter body opts out explicitly with |safe.
app.jinja_env.autoescape = True

if os.environ.get("CATREADS_BEHIND_PROXY", "").lower() in {"1", "true", "yes", "on"}:
    # nginx terminates TLS and overwrites X-Forwarded-For itself, so trust
    # exactly one proxy hop for the client address and scheme. Do not trust
    # X-Forwarded-Host; nginx already sets Host to the vhost name.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.session_protection = "strong"
load_books_event = threading.Event()
INDEX_TEMPLATES = {"list": "index.jinja2", "grid": "index-grid.jinja2"}
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri="memory://",
    headers_enabled=True,
    swallow_errors=True,
    default_limits=[],
)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

book_tags = db.Table(
    "book_tags",
    db.Column("book_id", db.Integer, db.ForeignKey("book.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)


# Database Models
class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, unique=True, nullable=False)
    author = db.Column(db.String, unique=False, index=True, nullable=False)
    title = db.Column(db.String, nullable=False)
    chapters_count = db.Column(db.Integer, nullable=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    chapters = db.relationship("Chapter", backref="book", lazy=True)
    progresses = db.relationship("BookProgress", backref="book")
    tags = db.relationship(
        "Tag",
        secondary=book_tags,
        backref=db.backref("books", lazy="dynamic"),
        lazy="dynamic",
    )


class Chapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String, nullable=True)
    content = db.Column(db.Text, nullable=False)
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id"), index=True, nullable=False
    )


class BookProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chapter_index = db.Column(db.Integer, nullable=False)
    paragraph_index = db.Column(db.Integer, nullable=False, default=0)
    updated_datetime = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id"), index=True, nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), index=True, nullable=False
    )


class User(db.Model, UserMixin):
    """User model and flask-login mixin."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    book_progresses = db.relationship(
        "BookProgress",
        backref=db.backref("BookProgress", lazy=True),
    )

    def set_password(self, new_password):
        self.password_hash = generate_password_hash(new_password)

    def check_password(self, maybe_password):
        return check_password_hash(self.password_hash, maybe_password)


CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Hash checked for unknown usernames so a missing user takes as long as a
# wrong password (no username enumeration by timing).
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def login_account_key():
    username = (request.form.get("username") or "").strip().casefold()[:64]
    return f"{get_remote_address()}:{username}"


def only_failed_logins(response):
    return response.status_code == 401


def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_token_is_valid():
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.form.get(CSRF_FORM_FIELD) or request.headers.get(CSRF_HEADER)
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


@app.before_request
def security_checks():
    g.csp_nonce = secrets.token_urlsafe(16)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_token_is_valid():
        abort(400, description="Invalid or missing CSRF token")
    if request.endpoint == "static":
        filename = request.path.rsplit("/", 1)[-1]
        # Covers reveal the library, so they need a session even though the
        # rest of /static (css, fonts, the login logo) stays public.
        if filename.startswith("cover-") and not current_user.is_authenticated:
            abort(404)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    nonce = getattr(g, "csp_nonce", "")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000"
        )
    if request.endpoint == "static":
        if request.path.rsplit("/", 1)[-1].startswith("cover-"):
            response.headers["Cache-Control"] = "private, no-store"
    else:
        response.headers.setdefault("Cache-Control", "no-store")
        response.vary.add("Cookie")
    return response


@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.errorhandler(429)
def rate_limit_error(error):
    logging.warning(
        "rate limit exceeded: %s", getattr(error, "description", "unknown")
    )
    original = error.get_response()
    response = make_response(
        render_template(
            "login.jinja2", error="Too many attempts. Try again later."
        ),
        429,
    )
    for header in (
        "Retry-After",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ):
        value = original.headers.get(header)
        if value:
            response.headers[header] = value
    return response


@app.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "30 per 15 minutes",
    methods=["POST"],
    deduct_when=only_failed_logins,
)
@limiter.limit(
    "10 per 15 minutes",
    methods=["POST"],
    key_func=login_account_key,
    deduct_when=only_failed_logins,
)
def login():
    if request.method == "GET":
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        return render_template("login.jinja2")

    username = (request.form.get("username") or "").strip()[:64]
    maybe_password = (request.form.get("password") or "")[:1024]
    client_ip = request.remote_addr or "unknown"

    user = User.query.filter_by(username=username).first()
    if user is None:
        check_password_hash(DUMMY_PASSWORD_HASH, maybe_password)
        password_ok = False
    else:
        password_ok = user.check_password(maybe_password)

    if user is not None and password_ok:
        session.clear()
        session.permanent = True
        login_user(user)
        return redirect(url_for("index"))

    logging.warning("failed login for %r from %s", username, client_ip)
    return render_template("login.jinja2", error="Wrong username or password."), 401


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("login"))


@login_manager.user_loader
def load_user(user_id):
    """
    This is called by flask-login on every request to load the user
    """
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, user_id)


BOOKS_DIR = "./epub"


def save_cover_image(book_id):
    static_dir = pathlib.Path("./static")
    static_dir.mkdir(exist_ok=True)
    cover_path = static_dir / f"cover-{book_id}.jpg"
    if cover_path.is_file():
        return
    # Try to find the cover ID from metadata
    book = Book.query.get_or_404(book_id)
    book_path = os.path.join(BOOKS_DIR, book.filename)
    book_epub = epub.read_epub(book_path, {"ignore_ncx": True})
    cover_id = None
    for meta in book_epub.get_metadata("OPF", "meta"):
        if meta and len(meta) > 1 and meta[0] and meta[0].get("name") == "cover":
            cover_id = meta[0].get("content")
            break
    # If cover ID is found, get the cover item
    cover_item = None
    if cover_id:
        cover_item = book_epub.get_item_with_id(cover_id)
    else:
        # Fallback: look for an image item with 'cover' in its ID or name
        for item in book_epub.get_items():
            if item.get_type() == ebooklib.ITEM_IMAGE:
                if (
                    "cover" in item.get_id().lower()
                    or "cover" in item.get_name().lower()
                ):
                    cover_item = item
                    break
    if cover_item:
        with open(cover_path, "wb") as f:
            f.write(cover_item.get_content())
    else:
        cover_path.symlink_to(static_dir.resolve() / "no-cover.png")
        print(f"No cover image found for {book_path}.")


def chapter_title(clean_content, index):
    """Derive a chapter title from its first heading, if there is one."""
    soup = BeautifulSoup(clean_content, "html.parser")
    heading = soup.find(re.compile(r"^h[1-3]$"))
    if heading:
        title = heading.get_text(" ", strip=True)[:200]
        if title:
            return title
    return f"Chapter {index + 1}"


# structural tags beyond bleach's minimal default set, so EPUB content
# (headings, tables, line breaks, ...) survives with plain browser styling
EXTRA_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "hr", "div", "span",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "figure", "figcaption",
    "pre", "sub", "sup", "s", "u", "wbr", "dfn", "kbd", "q", "samp",
    "small", "ins", "del", "cite",
]

ALLOWED_ATTRIBUTES = {
    **sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "target", "rel"],
    "img": ["src", "alt"],
}


def extract_chapters(book_epub, book_id):
    """Extract chapters in reading (spine) order with cleaned content.

    - keeps structural tags (headings, tables, lists, ...)
    - saves inline images to instance/images/<book_id>/ and rewrites their
      src to a served URL
    - rewrites links to other chapters to /book/<id>/chapter/<n>
    - external links open in a new tab; dead internal links become plain text
    """
    chapters = []
    for entry in book_epub.spine or []:
        item_id = entry[0] if isinstance(entry, (list, tuple)) else entry
        item = book_epub.get_item_with_id(item_id)
        if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
            chapters.append(item)
    if not chapters:
        chapters = [
            item
            for item in book_epub.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
        ]

    # all items by (lowercased) file path, for resolving image/link targets
    items_by_path = {}
    for item in book_epub.get_items():
        name = item.file_name
        if name:
            items_by_path.setdefault(name.lower(), item)
    path_to_index = {
        c.file_name.lower(): i
        for i, c in enumerate(chapters)
        if c.file_name
    }

    image_dir = pathlib.Path("instance") / "images" / str(book_id)
    image_dir.mkdir(parents=True, exist_ok=True)
    saved_images = {}
    seq = 0

    def resolve(ref, base_dir):
        """Resolve an EPUB-internal reference to its item, or None."""
        ref = (ref or "").strip()
        if not ref or ref.startswith(
            (
                "#", "http://", "https://", "data:", "mailto:",
                "ftp:", "javascript:", "tel:",
            )
        ):
            return None
        ref = ref.split("#", 1)[0]
        if not ref:
            return None
        ref = ref.lstrip("/")
        resolved = posixpath.normpath(posixpath.join(base_dir, ref)).lower()
        return items_by_path.get(resolved)

    def save_image(item):
        nonlocal seq
        key = (item.file_name or "").lower()
        if key not in saved_images:
            seq += 1
            base = os.path.basename(key)
            base = re.sub(r"[^A-Za-z0-9._-]", "_", base) or "image"
            with open(image_dir / f"{seq:03d}_{base}", "wb") as f:
                f.write(item.get_content())
            saved_images[key] = f"{seq:03d}_{base}"
        return saved_images[key]

    allowed_tags = list(sanitizer.ALLOWED_TAGS) + EXTRA_TAGS + ["p", "img"]
    processed_chapters = []
    for index, chapter in enumerate(chapters):
        content = chapter.get_content()
        if isinstance(content, bytes):
            content = content.decode(errors="ignore")
        soup = BeautifulSoup(content, "html.parser")
        base_dir = posixpath.dirname(chapter.file_name or "")

        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if not src or src.startswith(("http://", "https://", "data:")):
                if not src:
                    img.decompose()
                continue
            item = resolve(src, base_dir)
            if item is None:
                img.decompose()
                continue
            media_type = (getattr(item, "media_type", "") or "").lower()
            if (
                item.get_type()
                not in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_VECTOR)
                and not media_type.startswith("image/")
            ):
                img.decompose()
                continue
            img.attrs = {
                "src": f"/book_images/{book_id}/{save_image(item)}",
                "alt": img.get("alt") or "",
            }

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("http://", "https://")):
                a["target"] = "_blank"
                a["rel"] = "noopener noreferrer"
                continue
            if not href or href.startswith(
                ("#", "mailto:", "tel:", "ftp:")
            ):
                continue
            if href.startswith("javascript:"):
                a.unwrap()
                continue
            target = resolve(href, base_dir)
            if target is None or (target.file_name or "").lower() not in path_to_index:
                a.unwrap()
                continue
            a["href"] = f"/book/{book_id}/chapter/{path_to_index[target.file_name.lower()]}"

        clean_content = clean(
            soup.decode(),
            tags=allowed_tags,
            attributes=ALLOWED_ATTRIBUTES,
            strip=True,
        )
        processed_chapters.append(
            Chapter(
                index=index,
                title=chapter_title(clean_content, index),
                content=clean_content,
            )
        )
    return processed_chapters


def load_books():
    existing_filenames = {book.filename for book in Book.query.all()}
    for filename in tqdm(sorted(os.listdir(BOOKS_DIR))):
        try:
            if (
                filename.endswith(".epub")
                and filename not in existing_filenames
                and os.path.isfile(os.path.join(BOOKS_DIR, filename))
            ):
                print(f"processing {filename}")
                book_path = os.path.join(BOOKS_DIR, filename)
                book_epub = epub.read_epub(book_path, {"ignore_ncx": True})
                # print(book_epub.metadata)
                title = (
                    book_epub.get_metadata("DC", "title")[0][0].strip()
                    if book_epub.get_metadata("DC", "title")
                    else "Untitled"
                )
                author = (
                    book_epub.get_metadata("DC", "creator")[0][0].strip()
                    if book_epub.get_metadata("DC", "creator")
                    else "Unknown Author"
                )

                # Create a new Book entry (needs a row before extraction,
                # which stores images under instance/images/<book_id>/)
                new_book = Book(
                    filename=filename,
                    title=title,
                    author=author,
                    chapters_count=0,
                )
                db.session.add(new_book)
                db.session.commit()

                processed_chapters = extract_chapters(book_epub, new_book.id)
                new_book.chapters = processed_chapters
                new_book.chapters_count = len(processed_chapters)
                db.session.commit()
        except Exception as e:
            print(filename)
            print(str(e))
    process_covers()


@app.route("/apply_settings")
@login_required
def apply_settings():
    session.permanent = True
    try:
        zoom = float(request.args.get("zoom", 1))
    except ValueError:
        zoom = 1
    if not math.isfinite(zoom) or not 0.5 <= zoom <= 1.5:
        zoom = 1
    session["zoom"] = zoom
    # fraction of the page width eaten by the two side margins: 0.5 leaves the
    # content half the page
    try:
        margin = float(request.args.get("margin", 0))
    except ValueError:
        margin = 0
    if not math.isfinite(margin):
        margin = 0
    session["margin"] = min(max(margin, 0), 0.9)
    color = request.args.get("color", "#000000")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#000000"
    session["color"] = color
    # the settings form always submits a layout; only act on it when present so
    # older links/bookmarks do not silently reset a user's choice
    layout = request.args.get("layout")
    if layout is not None:
        session["layout"] = layout if layout in INDEX_TEMPLATES else "list"
    return redirect(url_for("index"))


def get_contrast_color(bg_color):
    # Remove '#' and convert hex to RGB
    hex_color = bg_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Calculate brightness
    brightness = ((r * 299) + (g * 587) + (b * 114)) / 1000

    # Decide on the foreground color
    return "black" if brightness > 128 else "white"


@app.route("/update_progress", methods=["POST"])
@login_required
def update_progress():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid data"), 400

    values = []
    for field in ("book_id", "chapter_index", "paragraph_index"):
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            return jsonify(error="Invalid data"), 400
        values.append(value)
    book_id, chapter_index, paragraph_index = values
    if book_id < 1 or chapter_index < 0 or paragraph_index < 0:
        return jsonify(error="Invalid data"), 400
    if paragraph_index > 1_000_000:
        return jsonify(error="Invalid data"), 400

    if db.session.get(Book, book_id) is None:
        return jsonify(error="Unknown book"), 404
    chapter = Chapter.query.filter_by(
        book_id=book_id, index=chapter_index
    ).first()
    if chapter is None:
        return jsonify(error="Unknown chapter"), 400

    progress = BookProgress.query.filter_by(
        book_id=book_id, user_id=current_user.id
    ).first()
    if not progress:
        progress = BookProgress(
            book_id=book_id,
            user_id=current_user.id,
            chapter_index=chapter_index,
            paragraph_index=paragraph_index,
        )
        db.session.add(progress)
    else:
        progress.chapter_index = chapter_index
        progress.paragraph_index = paragraph_index
        progress.updated_datetime = datetime.datetime.utcnow()
    db.session.commit()
    return jsonify(status="success")


@app.template_filter("add_paragraph_ids")
def add_paragraph_ids(content):
    soup = BeautifulSoup(content, "html.parser")
    for idx, p in enumerate(soup.find_all("p")):
        p["id"] = f"paragraph-{idx}"
    return soup.prettify()


def get_opposite_color(color):
    return "black" if color == "white" else "white"


@app.context_processor
def inject_globals():
    return {
        "get_contrast_color": get_contrast_color,
        "get_opposite_color": get_opposite_color,
        "add_paragraph_ids": add_paragraph_ids,
        "csrf_token": get_csrf_token,
        "csp_nonce": getattr(g, "csp_nonce", ""),
    }


@app.route("/hide/<int:book_id>", methods=["POST"])
@login_required
def hide(book_id):
    book = Book.query.get_or_404(book_id)
    book.is_hidden = True
    for p in BookProgress.query.filter_by(book_id=book_id, user_id=current_user.id):
        db.session.delete(p)
    db.session.commit()
    return redirect(url_for("index"))


def add_tags(book_id, tag_names):
    with app.app_context():
        book = Book.query.filter_by(id=book_id).first()
        if book is None:
            return
        names = []
        for raw_name in tag_names.split(","):
            name = re.sub(r"[\x00-\x1f\x7f]", "", raw_name).strip()[:50]
            if name and name not in names:
                names.append(name)
            if len(names) >= 20:
                break
        # go through user-supplied tags and add tags to books, creating
        # new tags if they don't already exist
        book.tags = []
        for tag_name in names:
            existing_tag = Tag.query.filter_by(name=tag_name).first()
            if existing_tag and existing_tag not in list(book.tags):
                book.tags.append(existing_tag)
            elif not existing_tag:
                book.tags.append(Tag(name=tag_name))
        db.session.commit()


@app.route("/add_tags", methods=["POST"])
@login_required
def add_tags_route():
    book_id = request.form.get("book_id", type=int)
    if book_id is None:
        abort(400)
    Book.query.get_or_404(book_id)
    tag_names = request.form.get("tag_names", "")[:1000]
    add_tags(book_id, tag_names)
    return redirect(url_for("continue_reading", book_id=book_id))


def parse_query(text, default_key="title"):
    # parse a search query, like title:"my title" tag:mytag into
    # { 'title': ['my title'], 'tag': ['mytag'] }
    out = collections.defaultdict(list)
    try:
        tokens = shlex.split(text)
    except ValueError:
        logging.warning("Error parsing query: %r", text[:200])
        return out

    for token in tokens:
        if ":" in token:
            key, value = token.split(":", 1)
            if v := value.strip():
                out[key.lower()].append(v)
        else:
            if v := token.strip():
                out[default_key].append(v)
    return out


@app.route("/")
@login_required
def index():
    books = Book.query
    q = (request.args.get("q") or "")[:500]
    filters = parse_query(q)

    if request.args.get("show_all") != "y":
        books = books.filter_by(is_hidden=False)

    for filter_sort_date in filters.get("sort", []):
        if filter_sort_date == "author":
            books = books.order_by(Book.author, Book.title)
        elif filter_sort_date == "!author":
            books = books.order_by(Book.author.desc(), Book.title)
        # we don't have date :(
        elif filter_sort_date == "date":
            books = books.order_by(Book.id)
        elif filter_sort_date == "!date":
            books = books.order_by(Book.id.desc())

    # default sort by author name
    if not filters.get("sort"):
        books = books.order_by(Book.author, Book.title)

    # process search query for tags, author and title
    for filter_tag_name in filters.get("tag", []):
        if filter_tag_name.startswith("!"):
            negated_tag = filter_tag_name[1:]
            books = books.filter(~Book.tags.any(Tag.name == negated_tag))
        else:
            books = books.filter(Book.tags.any(Tag.name == filter_tag_name))

    for filter_author_name in filters.get("author", []):
        if filter_author_name.startswith("!"):
            negated_author = filter_author_name[1:]
            books = books.filter(~Book.author.ilike(f"%{negated_author}%"))
        else:
            books = books.filter(Book.author.ilike(f"%{filter_author_name}%"))

    for filter_title_name in filters.get("title", []):
        if filter_title_name.startswith("!"):
            negated_title = filter_title_name[1:]
            books = books.filter(~Book.title.ilike(f"%{negated_title}%"))
        else:
            books = books.filter(Book.title.ilike(f"%{filter_title_name}%"))

    books = books.all()

    user_progresses = BookProgress.query.filter_by(user_id=current_user.id)
    user_progresses = {up.book_id: up for up in user_progresses}
    unread_books = []
    finished_books = []
    in_progress_books = []
    now = datetime.datetime.utcnow()

    # sort book list into in progress, unread and finished books
    for book in books:
        if not book.id in user_progresses:
            unread_books.append(book)
        else:
            book_progress = user_progresses[book.id]
            if book_progress.chapter_index + 1 >= book.chapters_count:
                finished_books.append(book)
            else:
                in_progress_books.append(
                    (now - book_progress.updated_datetime, book, book_progress)
                )

    # most recently read first
    in_progress_books = sorted(in_progress_books, key=lambda bb: bb[0], reverse=True)

    layout = session.get("layout", "list")
    if layout not in INDEX_TEMPLATES:
        layout = "list"
    return render_template(
        INDEX_TEMPLATES[layout],
        unread_books=unread_books,
        finished_books=finished_books,
        in_progress_books=in_progress_books,
        humanize=humanize,
        q=q,
        layout=layout,
    )


@app.route("/book/<int:book_id>/chapter/<int:chapter_index>")
@login_required
def read_chapter(book_id, chapter_index):
    book = Book.query.get_or_404(book_id)
    chapter = Chapter.query.filter_by(
        book_id=book_id, index=chapter_index
    ).first_or_404()

    # Save progress
    progress = BookProgress.query.filter_by(
        book_id=book_id, user_id=current_user.id
    ).first()
    if not progress:
        progress = BookProgress(
            book_id=book_id, user_id=current_user.id, chapter_index=chapter_index
        )
        db.session.add(progress)
    else:
        if chapter_index != progress.chapter_index:
            progress.paragraph_index = 0
        progress.chapter_index = chapter_index
        progress.updated_datetime = datetime.datetime.utcnow()
    db.session.commit()

    total_chapters = Chapter.query.filter_by(book_id=book_id).count()
    return render_template(
        "chapter.jinja2",
        title=book.title,
        chapter=chapter,
        content=chapter.content,
        book_id=book_id,
        book_progress=progress,
        book=book,
        chapter_index=chapter_index,
        total_chapters=total_chapters,
    )


@app.route("/book_images/<int:book_id>/<path:filename>")
@login_required
def book_image(book_id, filename):
    Book.query.get_or_404(book_id)
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type or not mime_type.startswith("image/"):
        abort(404)
    return send_from_directory(
        pathlib.Path("instance") / "images" / str(book_id), filename
    )


@app.route("/remove_progress/<int:book_progress_id>", methods=["POST"])
@login_required
def remove_progress(book_progress_id):
    book_progress = BookProgress.query.get_or_404(book_progress_id)
    if book_progress.user_id != current_user.id:
        abort(403)
    db.session.delete(book_progress)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/book/<int:book_id>")
@login_required
def continue_reading(book_id):
    progress = BookProgress.query.filter_by(
        book_id=book_id, user_id=current_user.id
    ).first()
    chapter_index = progress.chapter_index if progress else 0
    return redirect(
        url_for("read_chapter", book_id=book_id, chapter_index=chapter_index)
    )


_load_trigger_lock = threading.Lock()
_last_load_trigger = 0.0
LOAD_TRIGGER_INTERVAL = 60


@app.route("/trigger_load_books", methods=["POST"])
@login_required
def trigger_load_books():
    global _last_load_trigger
    with _load_trigger_lock:
        now = time.monotonic()
        if now - _last_load_trigger >= LOAD_TRIGGER_INTERVAL:
            load_books_event.set()
            _last_load_trigger = now
    return redirect(url_for("index"))


def run_app(debug=False, interface="127.0.0.1"):
    def load_books_thread():
        while True:
            with app.app_context():
                load_books()
            load_books_event.wait(timeout=60 * 60)
            load_books_event.clear()

    if debug:
        # The Werkzeug debugger is remote code execution if it ever answers a
        # non-loopback request; require an explicit opt-in for that.
        if interface not in {"127.0.0.1", "localhost", "::1"} and os.environ.get(
            "CATREADS_ALLOW_REMOTE_DEBUG"
        ) != "1":
            raise SystemExit(
                "Refusing to run the debug server on a non-loopback interface"
            )
        app.run(host=interface, port=5438, debug=True)
    else:
        threading.Thread(target=load_books_thread, daemon=True).start()
        waitress.serve(app, host=interface, port=5438)


def process_cover(book_id):
    with app.app_context():
        save_cover_image(book_id)


def process_covers():
    with app.app_context():
        for book in Book.query.all():
            process_cover(book.id)


def chapter_text(content):
    text = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def remap_chapter_index(old_content, new_texts):
    """Find the new spine index of an old chapter, by visible text.

    new_texts is a list of (index, text) pairs. The extraction pipeline can
    change markup between imports, so match on stripped text, not markup.
    """
    if old_content is None:
        return None
    old_text = chapter_text(old_content)
    if not old_text:
        return None
    for index, text in new_texts:
        if text == old_text:
            return index
    prefix = old_text[:200]
    for index, text in new_texts:
        if text.startswith(prefix):
            return index
    return None


def reimport_book(book_id):
    """Re-extract a book's chapters in spine order, preserving reading progress."""
    with app.app_context():
        book = Book.query.get_or_404(book_id)
        book_path = os.path.join(BOOKS_DIR, book.filename)
        book_epub = epub.read_epub(book_path, {"ignore_ncx": True})
        new_chapters = extract_chapters(book_epub, book.id)
        new_texts = [(c.index, chapter_text(c.content)) for c in new_chapters]

        old_content_by_index = {c.index: c.content for c in book.chapters}
        remapped = 0
        for p in BookProgress.query.filter_by(book_id=book.id).all():
            new_index = remap_chapter_index(
                old_content_by_index.get(p.chapter_index), new_texts
            )
            if new_index is None:
                # best effort: keep the old position if it still exists
                new_index = min(p.chapter_index, len(new_chapters) - 1)
            if new_index != p.chapter_index:
                p.chapter_index = new_index
                p.paragraph_index = 0
                remapped += 1

        for c in list(book.chapters):
            db.session.delete(c)
        for c in new_chapters:
            c.book_id = book.id
            db.session.add(c)
        book.chapters_count = len(new_chapters)
        db.session.commit()
        return (
            f"book {book_id} ({book.title!r}): {len(new_chapters)} chapters, "
            f"{remapped} progress row(s) remapped"
        )


def reimport_all():
    failures = []
    with app.app_context():
        for book in Book.query.order_by(Book.id).all():
            try:
                print(reimport_book(book.id))
            except Exception as e:
                failures.append((book.id, book.filename))
                print(f"FAILED book {book.id} ({book.filename}): {e}")
    if failures:
        print(f"{len(failures)} book(s) failed: {[bid for bid, _ in failures]}")


def attr_join(xs, attr=None, sep=","):
    return sep.join([getattr(x, attr) for x in xs])


def show_books():
    ret = []
    with app.app_context():
        for book in Book.query.order_by(Book.title).all():
            book_tags_str = attr_join(book.tags, "name")
            if not book_tags_str:
                ret.append(
                    [
                        book.id,
                        book.is_hidden,
                        book.author,
                        book.title,
                        book_tags_str,
                    ]
                )
    print(tabulate.tabulate(ret, tablefmt='orgtbl'))


if __name__ == "__main__":
    argh.dispatch_commands(
        [
            run_app,
            process_cover,
            process_covers,
            show_books,
            add_tags,
            reimport_book,
            reimport_all,
        ]
    )
