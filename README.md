# dreads - web (epub) ebook reader

<table>
<tr>
<th>Book index - day theme</th>
<th>Chapter view - night theme</th>
</tr>
<tr>
<td><img src="https://i.imgur.com/8AZpvDC.png"></td>
<td><img src="https://i.imgur.com/zTPNUaD.png"></td>
</tr>
</table>

# Features

* Automatically add books from a directory.
* Show in-progress and finished books.
* Remembers which chapter and paragraph you were on.
* Change entire app zoom per-session.
* Add left/right page margins per-session (0 = full width, 0.5 = content half the page).
* Change the background color per-session.
* Two book index layouts per-session: a list with large covers and titles, or a compact cover grid (title on hover).
* Multi-user with shared books and separate reading progress.
* Renders EPUB content: inline images, links between chapters, headings, tables.

## Installation

    git clone https://github.com/dvolk/dreads
    cd dreads
    python3 -m venv env
    env/bin/pip install -r requirements.txt
    env/bin/flask db upgrade

## Add a user

Enter the flask shell

    env/bin/flask shell

and run the following commands

    u = User(username="your_username")
    u.set_password("your_password")
    db.session.add(u)
    db.session.commit()

## Add books

Copy epub ebooks to ./epub

## Run the app

    env/bin/python app.py run-app

and browse to http://127.0.0.1:5438

## Other commands

    env/bin/python app.py add-tags <book_id> "tag1, tag2"   # tag a book
    env/bin/python app.py reimport-all                      # re-extract chapters in reading order
    env/bin/python app.py show-books                        # list books with no tags
    env/bin/python test_app.py                              # smoke test the routes
