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
* Change the background color continuosly from black #000 to white #fff per-session.

## Installation


    git clone https://github.com/dvolk/dreads
    cd dreads
    python3 -m venv env
    env/bin/pip install -r requirements.txt
    env/bin/flask db upgrade

## Running

    env/bin/python app.py

## Add books

To add books, put them in the `epub/` directory and restart the application.

Book contents is saved in the database, so the epubs can be removed after they've been loaded into the app.
