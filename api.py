# Start the api server
# python api.py
# load a book to the database, book in folder ./books
# curl -X POST -H "Content-Type: application/json" -d '{"filename": "new_book.epub"}' http://127.0.0.1:5448/api/load_book

from flask import Flask, request, jsonify
from tqdm import tqdm
import os
from ebooklib import epub
import ebooklib  # Add missing import
from bleach import clean, sanitizer
from db_handler import db, add_book_to_db  # Import database-related functions

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///reader.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)  # Initialize the database with the app

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
            new_chapter = {
                "index": index,
                "title": f"Chapter {index + 1}",
                "content": clean_content,
            }
            processed_chapters.append(new_chapter)

        # Create a new Book entry
        new_book_data = {
            "filename": filename,
            "title": title,
            "author": author,
            "chapters": processed_chapters,
            "chapters_count": len(processed_chapters),
        }

        # Add book to the database
        with app.app_context():
            add_book_to_db(new_book_data)

        return jsonify({"message": "Book loaded successfully"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5448)
