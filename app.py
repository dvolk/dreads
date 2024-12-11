import datetime
import json
import os
import secrets
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
from flask import Flask, redirect, render_template, request, session, url_for, abort
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from tqdm import tqdm
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
import waitress
import tabulate

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get(
    "CATREADS_SECRET_KEY", secrets.token_urlsafe(64)
)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 99999999999
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = "login"
load_books_event = threading.Event()

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


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        maybe_password = request.form.get("password")

        user = User.query.filter_by(username=username).first_or_404()

        if user.check_password(maybe_password):
            print("okay")
            login_user(user)
            return redirect(url_for("index"))
        else:
            print("wrong password")
            abort(404)
    if request.method == "GET":
        return render_template("login.jinja2")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@login_manager.user_loader
def load_user(user_id):
    """
    This is called by flask-login on every request to load the user
    """
    return User.query.filter_by(id=int(user_id)).first()


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
        cover_path.symlink_to("no-cover.png")
        print(f"No cover image found for {book_path}.")


def load_books():
    existing_filenames = {book.filename for book in Book.query.all()}
    for filename in tqdm(os.listdir(BOOKS_DIR)):
        try:
            if filename.endswith(".epub") and filename not in existing_filenames:
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

                # Extract chapters
                chapters = [
                    item
                    for item in book_epub.get_items()
                    if item.get_type() == ebooklib.ITEM_DOCUMENT
                ]
                processed_chapters = []
                allowed_tags = list(sanitizer.ALLOWED_TAGS) + ["p", "img"]
                for index, chapter in enumerate(chapters):
                    try:
                        content = chapter.get_content().decode()
                    except Exception:
                        content = chapter.get_content()
                    clean_content = clean(
                        content,
                        tags=allowed_tags,
                        strip=True,
                    )
                    new_chapter = Chapter(
                        index=index,
                        title=f"Chapter {index + 1}",
                        content=clean_content,
                    )
                    processed_chapters.append(new_chapter)
                # Create a new Book entry
                new_book = Book(
                    filename=filename,
                    title=title,
                    author=author,
                    chapters=processed_chapters,
                    chapters_count=len(processed_chapters),
                )
                db.session.add(new_book)
                db.session.commit()
        except Exception as e:
            print(filename)
            print(str(e))


@app.route("/apply_settings")
@login_required
def apply_settings():
    session.permanent = True
    session["zoom"] = request.args.get("zoom", 1)
    session["color"] = request.args.get("color", "#000000")
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
    data = request.get_json()
    book_id = data.get("book_id")
    chapter_index = data.get("chapter_index")
    paragraph_index = data.get("paragraph_index")
    if book_id is None or chapter_index is None or paragraph_index is None:
        return json.dumps({"error": "Invalid data"}), 400

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
    return json.dumps({"status": "success"})


@app.template_filter("add_paragraph_ids")
def add_paragraph_ids(content):
    soup = BeautifulSoup(content, "html.parser")
    for idx, p in enumerate(soup.find_all("p")):
        p["id"] = f"paragraph-{idx}"
    return soup.prettify()


@app.context_processor
def inject_globals():
    return {
        "get_contrast_color": get_contrast_color,
        "add_paragraph_ids": add_paragraph_ids,
    }


@app.route("/hide/<int:book_id>")
def hide(book_id):
    book = Book.query.get_or_404(book_id)
    book.is_hidden = True
    for p in BookProgress.query.filter_by(book_id=book_id):
        db.session.delete(p)
    db.session.commit()
    return redirect("index")


def add_tags(book_id, tag_names):
    with app.app_context():
        book = Book.query.filter_by(id=book_id).first()
        tag_names = [tn.strip() for tn in tag_names.split(",")]
        # go through user-supplied tags and add tags to books, creating
        # new tags if they don't already exist
        book.tags = []
        for tag_name in tag_names:
            existing_tag = Tag.query.filter_by(name=tag_name).first()
            if existing_tag and existing_tag not in list(book.tags):
                book.tags.append(existing_tag)
            elif not existing_tag:
                book.tags.append(Tag(name=tag_name))
        db.session.commit()


@app.route("/add_tags", methods=["POST"])
@login_required
def add_tags_route():
    book_id = request.form.get("book_id")
    Book.query.get_or_404(book_id)
    tag_names = request.form.get("tag_names", "")
    add_tags(book_id, tag_names)
    return redirect(url_for("continue_reading", book_id=book_id))


def parse_query(text, default_key="title"):
    # parse a search query, like title:"my title" tag:mytag into
    # { 'title': ['my title'], 'tag': ['mytag'] }
    out = collections.defaultdict(list)
    try:
        tokens = shlex.split(text)
    except ValueError as e:
        logging.exception("Error parsing query: ")
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
    q = request.args.get("q", "")
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

    # sort by last read
    in_progress_books = sorted(in_progress_books, key=lambda bb: bb[0])

    return render_template(
        "index.jinja2",
        unread_books=unread_books,
        finished_books=finished_books,
        in_progress_books=in_progress_books,
        humanize=humanize,
        q=q,
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
            print(chapter_index, progress.chapter_index, progress.paragraph_index)
            print("resetting paragraph progress")
            progress.paragraph_index = 0
        progress.chapter_index = chapter_index
        progress.updated_datetime = datetime.datetime.utcnow()
    db.session.commit()

    total_chapters = Chapter.query.filter_by(book_id=book_id).count()
    return render_template(
        "chapter.jinja2",
        title=book.title,
        content=chapter.content,
        book_id=book_id,
        book_progress=progress,
        book=book,
        chapter_index=chapter_index,
        total_chapters=total_chapters,
    )


@app.route("/remove_progress/<int:book_progress_id>")
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


@app.route("/trigger_load_books")
@login_required
def trigger_load_books():
    load_books_event.set()
    return redirect(url_for("index"))


def run_app(debug=False):
    def load_books_thread():
        while True:
            with app.app_context():
                load_books()
            load_books_event.wait(timeout=60 * 60)
            load_books_event.clear()

    if debug:
        app.run(port=5438, debug=True)
    else:
        threading.Thread(target=load_books_thread, daemon=True).start()
        waitress.serve(app, port=5438)


def process_cover(book_id):
    with app.app_context():
        save_cover_image(book_id)


def process_covers():
    with app.app_context():
        for book in Book.query.all():
            process_cover(book.id)


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
                        book.author[:20],
                        book.title[:40],
                        book_tags_str,
                    ]
                )
    print(tabulate.tabulate(ret))


if __name__ == "__main__":
    argh.dispatch_commands(
        [
            run_app,
            process_cover,
            process_covers,
            show_books,
            add_tags,
        ]
    )
