from flask import Flask, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from ebooklib import epub
import ebooklib
from bleach import clean, sanitizer
import os

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Database Models
class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, unique=True, nullable=False)
    title = db.Column(db.String, nullable=False)
    chapters = db.relationship("Chapter", backref="book", lazy=True)
    progress = db.relationship("BookProgress", backref="book", uselist=False)


class Chapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String, nullable=True)
    content = db.Column(db.Text, nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)


class BookProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chapter_index = db.Column(db.Integer, nullable=False)
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id"), nullable=False, unique=True
    )


BOOKS_DIR = "./epub"


def load_books():
    existing_filenames = {book.filename for book in Book.query.all()}
    for filename in os.listdir(BOOKS_DIR):
        if filename.endswith(".epub") and filename not in existing_filenames:
            print(f"processing {filename}")
            book_path = os.path.join(BOOKS_DIR, filename)
            book_epub = epub.read_epub(book_path)
            title = (
                book_epub.get_metadata("DC", "title")[0][0]
                if book_epub.get_metadata("DC", "title")
                else "Untitled"
            )

            # Create a new Book entry
            new_book = Book(filename=filename, title=title)
            db.session.add(new_book)
            db.session.commit()

            # Extract chapters
            chapters = [
                item
                for item in book_epub.get_items()
                if item.get_type() == ebooklib.ITEM_DOCUMENT
            ]
            allowed_tags = list(sanitizer.ALLOWED_TAGS) + ["p", "img"]
            for index, chapter in enumerate(chapters):
                content = chapter.get_content().decode()
                clean_content = clean(
                    content,
                    tags=allowed_tags,
                    strip=True,
                )
                new_chapter = Chapter(
                    index=index,
                    title=f"Chapter {index + 1}",
                    content=clean_content,
                    book_id=new_book.id,
                )
                db.session.add(new_chapter)
            db.session.commit()


@app.route("/")
def index():
    load_books()  # Add new books to the database if they are not already present
    books = Book.query.all()
    return render_template("index.jinja2", books=books)


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
    db.session.commit()

    total_chapters = Chapter.query.filter_by(book_id=book_id).count()
    return render_template(
        "chapter.jinja2",
        title=book.title,
        content=chapter.content,
        book_id=book_id,
        chapter_index=chapter_index,
        total_chapters=total_chapters,
    )


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
        db.create_all()
        load_books()
    app.run(port=5438, debug=True)
