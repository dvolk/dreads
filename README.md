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
* Night theme with #000 background for OLED screens / low-light reading.
* Remembers which chapter you were on.

## Installation


    git clone https://github.com/dvolk/dreads
    cd dreads
    python3 -m venv env
    env/bin/pip install -r requirements.txt
    env/bin/flask db upgrade

## Running

    python3 app.py

## Add books

To add books, put them in the `epub/` directory and restart the application.

Book contents is saved in the database, so the epubs can be removed after they've been loaded into the app.

## Authentication

Dreads doesn't provide any authentication.

If you want to run a publically accessible dreads, you can configure your web server to use basic authentication on the dreads domain.
