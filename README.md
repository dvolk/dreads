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

## Public deployment / security notes

The app can be the only authentication boundary. It now includes:

* CSRF tokens on every state-changing request (login, tags, hide, progress, logout).
* Login throttling (10 failures per IP+username and 30 per IP per 15 minutes).
* `HttpOnly` / `SameSite=Lax` session cookies; set `CATREADS_HTTPS=1` to add `Secure`.
* 400-day sessions, refreshed on every visit (the practical browser maximum), so an actively used login does not expire.
* A strict CSP, `nosniff`, `DENY` framing, no-referrer, and no-index headers.
* EPUB content is still sanitized with bleach; the CSP is a second line of defence.
* Book covers require a session, so the library is not enumerable from `/static/`.
* Unknown usernames take the same time to reject as wrong passwords.
* A weak or missing `CATREADS_SECRET_KEY` is ignored; a persistent 0600 key is
  generated under `instance/secret_key` instead.

Environment variables:

| Variable | Purpose |
| --- | --- |
| `CATREADS_SECRET_KEY` | Session signing key. Use at least 32 random characters. If absent or weak, `instance/secret_key` is used. |
| `CATREADS_BEHIND_PROXY=1` | Trust one reverse-proxy hop for `X-Forwarded-For` / `X-Forwarded-Proto`. Required behind nginx, otherwise Waitress clears those headers, all clients look like `127.0.0.1`, and rate limiting is shared. |
| `CATREADS_TRUSTED_PROXY` | Address of the proxy Waitress should trust. Default `127.0.0.1`; use `::1` if nginx connects over IPv6. |
| `CATREADS_HTTPS=1` | Mark the session cookie `Secure`. Required when TLS terminates at the proxy. |

The proxy must terminate TLS, bind the app to loopback, and pass the real client
address and scheme:

```nginx
location / {
    proxy_pass http://127.0.0.1:5438;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If you are moving from an IP allowlist to a public deployment, remove the
`include .../allowed-ips.conf;` and `deny all;` lines from the vhost. Keep the
redirect from HTTP to HTTPS.

A hardened systemd unit should keep the secret out of the unit file and make the
writable data private:

```ini
[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/stuff/code/catreads
Environment=CATREADS_BEHIND_PROXY=1
Environment=CATREADS_HTTPS=1
EnvironmentFile=/home/ubuntu/stuff/code/catreads/.env.key
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/home/ubuntu/stuff/code/catreads/instance /home/ubuntu/stuff/code/catreads/static
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
Restart=on-failure
```

`EnvironmentFile` should contain `CATREADS_SECRET_KEY=<32+ random chars>` and be
mode `0600`.

Rate limits use Flask-Limiter's in-memory storage, which is correct for the
single waitress process this app starts. If you later run multiple workers,
point Flask-Limiter at Redis or another shared backend.

Any authenticated user can tag, hide, or trigger a library reload, and hidden
books are shared. If accounts will be handed to untrusted people, add an admin
role for those routes before opening the instance.

## Other commands

    env/bin/python app.py add-tags <book_id> "tag1, tag2"   # tag a book
    env/bin/python app.py reimport-all                      # re-extract chapters in reading order
    env/bin/python app.py show-books                        # list books with no tags
    env/bin/python test_app.py                              # smoke test the routes
