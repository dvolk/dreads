# db_handler.py

import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

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

# Function to add a book to the database
def add_book_to_db(book_data):
    new_book = Book(
        filename=book_data['filename'],
        title=book_data['title'],
        author=book_data['author'],
        chapters_count=book_data['chapters_count'],
        chapters=[
            Chapter(
                index=chapter['index'],
                title=chapter['title'],
                content=chapter['content']
            ) for chapter in book_data['chapters']
        ]
    )
    db.session.add(new_book)
    db.session.commit()
