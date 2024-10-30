import collections
import datetime
import os

from flask import Flask, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from ebooklib import epub
import ebooklib
from bleach import clean, sanitizer
from tqdm import tqdm
import humanize

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Database Models
class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, unique=True, nullable=False)
    author = db.Column(db.String, unique=False, index=True, nullable=False)
    title = db.Column(db.String, nullable=False)
    chapters_count = db.Column(db.Integer, nullable=False)
    chapters = db.relationship("Chapter", backref="book", lazy=True)
    progress = db.relationship("BookProgress", backref="book", uselist=False)


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
    updated_datetime = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id"), index=True, nullable=False, unique=True
    )


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


@app.route("/")
def index():
    books = Book.query.order_by(Book.author, Book.title).all()
    unread_books = []
    finished_books = []
    in_progress_books = []
    now = datetime.datetime.utcnow()

    for book in books:
        if not book.progress:
            unread_books.append(book)
        if book.progress:
            if book.progress.chapter_index + 1 >= book.chapters_count:
                finished_books.append(book)
            else:
                in_progress_books.append((now - book.progress.updated_datetime, book))

    in_progress_books = sorted(in_progress_books, key=lambda bb: bb[0])

    return render_template(
        "index.jinja2",
        unread_books=unread_books,
        finished_books=finished_books,
        in_progress_books=in_progress_books,
        humanize=humanize,
    )


@app.route("/book/<int:book_id>/chapter/<int:chapter_index>")
def read_chapter(book_id, chapter_index):
    book = Book.query.get_or_404(book_id)
    chapter = Chapter.query.filter_by(
        book_id=book_id, index=chapter_index
    ).first_or_404()

    # Save progress
    progress = BookProgress.query.filter_by(book_id=book_id).first()
    if not progress:
        progress = BookProgress(book_id=book_id, chapter_index=chapter_index)
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
def remove_progress(book_progress_id):
    book_progress = BookProgress.query.get_or_404(book_progress_id)
    book = book_progress.book
    book.progress = None
    db.session.commit()


@app.route("/book/<int:book_id>")
def continue_reading(book_id):
    progress = BookProgress.query.filter_by(book_id=book_id).first()
    chapter_index = progress.chapter_index if progress else 0
    return redirect(
        url_for("read_chapter", book_id=book_id, chapter_index=chapter_index)
    )


if __name__ == "__main__":
    # Create the database tables and load books from disk
    with app.app_context():
        load_books()
    app.run(port=5438, debug=True)
