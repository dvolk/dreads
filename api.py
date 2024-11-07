# Start the api server
# python api.py
# load a book to the database, book in folder ./books
# curl -X POST -H "Content-Type: application/json" -d '{"filename": "new_book.epub"}' http://127.0.0.1:5448/api/load_book

import datetime
from flask import Flask, request, jsonify
from tqdm import tqdm
import secrets
import os
from ebooklib import epub
import ebooklib
from bleach import clean, sanitizer
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = secrets.token_urlsafe(64)
db = SQLAlchemy(app)

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
    paragraph_index = db.Column(db.Integer, nullable=False, default=0)
    updated_datetime = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    book_id = db.Column(
        db.Integer, db.ForeignKey("book.id"), index=True, nullable=False, unique=True
    )

# Assuming BOOKS_DIR is defined
BOOKS_DIR = './books'

@app.route('/api/load_book', methods=['POST'])
def load_book():
    data = request.get_json()
    filename = data.get('filename')

    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    # Check if the file exists
    book_path = os.path.join(BOOKS_DIR, filename)
    if not os.path.exists(book_path):
        return jsonify({"error": "File not found"}), 404

    # Check if the book already exists in the database
    existing_filenames = {book.filename for book in Book.query.all()}
    if filename in existing_filenames:
        return jsonify({"message": "Book already loaded"}), 200

    # Load and process the book
    try:
        book_epub = epub.read_epub(book_path, {"ignore_ncx": True})
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

        return jsonify({"message": "Book loaded successfully"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5448)
