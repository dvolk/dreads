"""Catread."""

from pathlib import Path

from ebooklib import epub
from bs4 import BeautifulSoup
import flask
import flask_mongoengine


app = flask.Flask(__name__)
app.secret_key = "secret"
app.config["MONGODB_DB"] = "catread-1"
db = flask_mongoengine.MongoEngine(app)


def get_theme(theme_name):
    """Toggle between light and dark themes."""
    if theme_name == "dark":
        color1 = "w3-gray"
        color2 = "w3-black"
    else:
        color1 = "w3-khaki"
        color2 = "w3-pale-yellow"
    return color1, color2


class Book(db.Document):
    """Book class."""

    title = db.StringField()
    filename = db.StringField()
    author = db.StringField()
    last_part = db.IntField(default=1)
    part_count = db.IntField(default=0)

header = """
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://www.w3schools.com/w3css/4/w3.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
<style>
 body, h1, h2, h3, h4, h5, h6 {
  font-family: Arial, Helvetica, sans-serif;
}
a { margin-right: 5px; text-decoration: none }
a.current { font-weight: bold; }
</style>
<title>{{ title }}</title>
</head>
"""

footer = """
</html>
"""

view_template = (
    header
    + """
<body>
<div class="w3-container {{ color1 }}">
<p>
{{ book.last_part }} / {{ book.part_count }}
&nbsp;&nbsp;
<a href="/view/{{ book.id }}/{{ book.last_part - 1 }}?theme={{ theme }}">Previous</a>
&nbsp;&nbsp;
<a href="/view/{{ book.id }}/{{ book.last_part + 1 }}?theme={{ theme }}">Next</a>
<a style="float:right" href="/?theme={{ theme }}">Exit</a>
</p>
</div>
<div class="w3-container {{ color2 }}">
{% for p in ps %}
<p>{{ p }}</p>
{% endfor %}
</div>
<div class="w3-container {{ color1 }}">
<p>
<a href="/view/{{ book.id }}/{{ book.last_part + 1 }}?theme={{ theme }}">Next</a>
<a style="float:right" href="/?theme={{ theme }}">Exit</a>
</p>
</div>
</body>
"""
    + footer
)

index_template = (
    header
    + """
<div class="w3-container {{ color2 }}">
<p>
{% if theme == "dark" %}
<a href="/?theme=light" style="margin-top: 10px; float: right" class="w3-btn w3-blue w3-round"><i class="fa fa-fw fa-cog"></i> Light Theme</a>
{% else %}
<a href="/?theme=dark" style="margin-top: 10px; float: right" class="w3-btn w3-blue w3-round"><i class="fa fa-fw fa-cog"></i> Dark Theme</a>
{% endif %}
</p>
<h1>Books</h1>
</div>
<div class="w3-container {{ color2 }}">
<p>
{% for book in books %}
<h4><a href="/view/{{ book.id }}/{{ book.last_part }}?theme={{ theme }}">{{ book.title }}</a><br/><small>{{ book.author }}</small></h4>
{% endfor %}
</p>
</div>
</div>
"""
    + footer
)


@app.route("/")
def index():
    """Index."""
    reload_files()
    theme = flask.request.args.get("theme")
    color1, color2 = get_theme(theme)
    books = Book.objects().order_by("title")

    return flask.render_template_string(
        index_template,
        books=books,
        title="Catreads",
        color1=color1,
        color2=color2,
        theme=theme,
    )


def reload_files():
    """Reload epubs from disk."""
    disk_books = list(Path("./epub").glob("*.epub"))
    disk_books_filenames = [x.name for x in disk_books]
    db_books = Book.objects().values_list("filename")
    for disk_book in disk_books:
        if disk_book.name not in db_books:
            print(f"adding {disk_book}")
            book = epub.read_epub(disk_book)
            title = book.title
            authors = book.get_metadata("DC", "creator")[0][0]
            b = Book(title=title, filename=disk_book.name, author=authors)
            b.save()
    # remove missing books from database
    books_to_remove = list()
    for db_book in db_books:
        if db_book not in disk_books_filenames:
            print(f"removing missing book: {db_book}")
            books_to_remove.append(db_book)
    for book_to_remove in books_to_remove:
        print(Book.objects(filename=book_to_remove).delete())


@app.route("/view/<book_id>/<ch>")
def view(book_id, ch):
    """View."""
    theme = flask.request.args.get("theme")
    color1, color2 = get_theme(theme)
    ch = int(ch)
    book = Book.objects(id=book_id).first()
    book_content = epub.read_epub("epub/" + book.filename)
    chapters = list(book_content.get_items())
    chapter = chapters[ch]
    html = chapter.get_content()
    soup = BeautifulSoup(html, features="lxml")
    ps = list()
    for p in soup.find_all("p"):
        ps.append(p.get_text())

    book.last_part = ch
    book.part_count = len(chapters)
    book.save()

    return flask.render_template_string(
        view_template,
        book=book,
        title=book.title,
        ps=ps,
        color1=color1,
        color2=color2,
        theme=theme,
    )


if __name__ == "__main__":
    app.run(port=5438)
