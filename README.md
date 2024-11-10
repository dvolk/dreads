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
* Change the background color per-session.
* Multi-user with shared books and separate reading progress.

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

    env/bin/python app.py

and browse to http://127.0.0.1:5438
