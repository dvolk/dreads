import datetime
import json
import os
import secrets
import threading
import time

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

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = secrets.token_urlsafe(64)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = "login"
load_books_event = threading.Event()

# Database Models
class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, unique=True, nullable=False)
    author = db.Column(db.String, unique=False, index=True, nullable=False)
    title = db.Column(db.String, nullable=False)
    chapters_count = db.Column(db.Integer, nullable=False)
    chapters = db.relationship("Chapter", backref="book", lazy=True)
    progresses = db.relationship("BookProgress", backref="book")


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


def load_books():
    existing_filenames = {book.filename for book in Book.query.all()}
    for filename in tqdm(os.listdir(BOOKS_DIR)):
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


@app.route("/")
@login_required
def index():
    books = Book.query.order_by(Book.author, Book.title).all()
    user_progresses = BookProgress.query.filter_by(user_id=current_user.id)
    user_progresses = {up.book_id: up for up in user_progresses}
    unread_books = []
    finished_books = []
    in_progress_books = []
    now = datetime.datetime.utcnow()

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

    in_progress_books = sorted(in_progress_books, key=lambda bb: bb[0])

    return render_template(
        "index.jinja2",
        unread_books=unread_books,
        finished_books=finished_books,
        in_progress_books=in_progress_books,
        humanize=humanize,
    )


@app.route("/book/<int:book_id>/chapter/<int:chapter_index>")
@login_required
def read_chapter(book_id, chapter_index):
    book = Book.query.get_or_404(book_id)
    chapter = Chapter.query.filter_by(
        book_id=book_id, index=chapter_index
    ).first_or_404()

    # Save progress
    progress = BookProgress.query.filter_by(book_id=book_id).first()
    if not progress:
        progress = BookProgress(
            book_id=book_id, user_id=current_user.id, chapter_index=chapter_index
        )
        db.session.add(progress)
    else:
        progress.chapter_index = chapter_index
        progress.updated_datetime = datetime.datetime.utcnow()
    db.session.commit()

    total_chapters = Chapter.query.filter_by(book_id=book_id).count()
    return render_template(
        "chapter.jinja2",
        title=book.title,
        content=chapter.content,
        book_id=book_id,
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


if __name__ == "__main__":

    def load_books_thread():
        while True:
            with app.app_context():
                load_books()
            load_books_event.wait(timeout=60 * 60)
            load_books_event.clear()

    threading.Thread(target=load_books_thread, daemon=True).start()
    app.run(port=5438)
