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

You will need mongodb:

    apt install mongodb-server

No further database configuration is needed.

    git clone https://github.com/dvolk/dreads
    cd dreads
    virtualenv env
    source env/bin/activate
    pip3 install -r requirements.txt

## Running

    python3 main.py

## Add books

To add books, put them in the `epub/` directory and refresh the index.

## Authentication

Dreads doesn't provide any authentication.

If you want to run a publically accessible dreads, you can configure your web server to use basic authentication on the dreads domain.
