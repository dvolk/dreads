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


header = """
<html>
<head>
<link rel="stylesheet" href="https://www.w3schools.com/w3css/4/w3.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
<style>
 body, h1, h2, h3, h4, h5, h6 {
  font-family: Arial, Helvetica, sans-serif;
}
a { margin-right: 5px; text-decoration: none }
a.current { font-weight: bold; }
</style>
</head>
"""

footer = """
</html>
"""

view_template = (
    header
    + """
<body>
<div class="w3-container {{color1}}">
<p>
{% for ch in chs %}
{% if ch == current_ch %}
<a class="current" href="/view/{{ book_id }}/{{ ch }}?theme={{ theme }}">&lt;{{ ch }}&gt;</a>
{% else %}
<a class="other" href="/view/{{ book_id }}/{{ ch }}?theme={{ theme }}">{{ ch }}</a>
{% endif %}
{% endfor %}
<a href="/?theme={{ theme }}">Index</a>
</p>
</div>
<div class="w3-container {{color2}} black">
{% for p in ps %}
<p>{{ p }}</p>
{% endfor %}
</div>
<div class="w3-container {{color1}}">
<p>
{% for ch in chs %}
{% if ch == current_ch %}
<a class="current" href="/view/{{ ch }}">&lt;{{ ch }}&gt;</a>
{% else %}
<a class="other" href="/view/{{ ch }}">{{ ch }}</a>
{% endif %}
{% endfor %}
</p>
</div>
</body>
"""
    + footer
)

index_template = (
    header
    + """
<div class="w3-container {{color2}}">
<a href="/reload" style="margin-top: 15px; float: right" class="w3-btn w3-green w3-round"><i class="fa fa-fw fa-refresh"></i> Reload</a>
<a href="/?theme=dark" style="margin-top: 15px; float: right" class="w3-btn w3-blue w3-round"><i class="fa fa-fw fa-cog"></i> Dark Theme</a>
<h1>Books</h1>
</div>
<div class="w3-container {{color2}}">
<p>
{% for book in books %}
<h4><a href="/view/{{ book.id }}/1?theme={{ theme }}">{{ book.title }}</a><br/><small>{{ book.author }}</small></h4>
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
    theme = flask.request.args.get("theme")
    color1, color2 = get_theme(theme)

    return flask.render_template_string(
        index_template,
        books=Book.objects().order_by("title"),
        color1=color1,
        color2=color2,
        theme=theme,
    )


def reload_files():
    """Reload epubs from disk."""
    disk_books = list(Path("./epub").glob("*.epub"))
    db_books = Book.objects().values_list("filename")
    print(db_books)
    for disk_book in disk_books:
        if disk_book.name not in db_books:
            print(f"adding {disk_book}")
            book = epub.read_epub(disk_book)
            title = book.title
            authors = book.get_metadata("DC", "creator")[0][0]
            b = Book(title=title, filename=disk_book.name, author=authors)
            b.save()


@app.route("/reload")
def reload():
    """Reload epubs and return to index."""
    reload_files()
    return flask.redirect("/")


@app.route("/view/<book_id>/<ch>")
def view(book_id, ch):
    """View."""
    theme = flask.request.args.get("theme")
    color1, color2 = get_theme(theme)
    ch = int(ch)
    book_filename = Book.objects(id=book_id).first().filename
    book = epub.read_epub("epub/" + book_filename)
    chapters = list(book.get_items())
    chapter = chapters[ch]
    html = chapter.get_content()
    soup = BeautifulSoup(html, features="lxml")
    ps = list()
    for p in soup.find_all("p"):
        ps.append(p.get_text())

    return flask.render_template_string(
        view_template,
        ps=ps,
        book_id=book_id,
        current_ch=ch,
        chs=list(range(len(chapters))),
        color1=color1,
        color2=color2,
        theme=theme,
    )


if __name__ == "__main__":
    app.run(port=5438, debug=True)
