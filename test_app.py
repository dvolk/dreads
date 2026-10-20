"""Smoke tests for the catreads app, using the Flask test client.

Creates a throwaway user (testbot), exercises the main routes, and always
restores the database to its pre-test state (tags, hidden flags, progress).

Important: HTTP requests are made with NO ambient app context, so each
request gets its own context and `g` (flask-login caches the user in `g`;
sharing one context across requests leaks the logged-in user between
requests and masks auth problems). ORM checks get their own short-lived
context blocks.

Run with: env/bin/python test_app.py
"""
import pathlib
import re

from flask import render_template
from sqlalchemy import text

from app import app, db, Book, BookProgress, Tag, User, limiter

TEST_USER = "testbot"
TEST_PASSWORD = "test-password-123"


def csrf_token(client):
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token")
    assert token, "CSRF token missing from session"
    return token


def cleanup(tags_snapshot, hidden_snapshot):
    testbots = User.query.filter_by(username=TEST_USER).all()
    for testbot in testbots:
        BookProgress.query.filter_by(user_id=testbot.id).delete()
    for testbot in testbots:
        db.session.delete(testbot)
    # restore tags via the ORM (keeps the book_tags association rows consistent)
    for book_id, tag_names in tags_snapshot.items():
        book = db.session.get(Book, book_id)
        if book:
            book.tags = [
                Tag.query.filter_by(name=tn).first() or Tag(name=tn)
                for tn in tag_names
            ]
    # drop tags left over from the test run
    for tag in list(Tag.query.all()):
        if not tag.books.count():
            db.session.delete(tag)
    # restore hidden flags
    for book_id, was_hidden in hidden_snapshot.items():
        book = db.session.get(Book, book_id)
        if book and book.is_hidden != was_hidden:
            book.is_hidden = was_hidden
    db.session.commit()


def run_tests(client, book_id, user_id):
    # --- login ---
    r = client.get("/login")
    assert r.status_code == 200, f"login page: {r.status_code}"
    assert client.head("/login").status_code == 200, "HEAD /login should be 200"
    token = csrf_token(client)
    assert b'name="csrf_token"' in r.data, "login form should carry a CSRF token"
    assert "catreads_session=" in r.headers.get("Set-Cookie", ""), (
        "login should set the session cookie"
    )
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie, "session cookie should be HttpOnly"
    assert "SameSite=Lax" in cookie, "session cookie should be SameSite=Lax"

    # login without the CSRF token is rejected
    r = client.post(
        "/login", data={"username": TEST_USER, "password": TEST_PASSWORD}
    )
    assert r.status_code == 400, f"CSRF-less login should be 400, got {r.status_code}"

    r = client.post(
        "/login",
        data={
            "username": TEST_USER,
            "password": "wrong",
            "csrf_token": token,
        },
    )
    assert r.status_code == 401, f"bad login should be 401, got {r.status_code}"
    assert b"Wrong username or password" in r.data, "login error message missing"

    r = client.post(
        "/login",
        data={
            "username": TEST_USER,
            "password": TEST_PASSWORD,
            "csrf_token": token,
        },
    )
    assert r.status_code in (302, 303), f"login should redirect, got {r.status_code}"
    assert app.config["PERMANENT_SESSION_LIFETIME"].days == 400, (
        "sessions should use the longest practical lifetime"
    )
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True, (
        "active sessions should slide forward"
    )
    assert "Expires=" in r.headers.get("Set-Cookie", ""), (
        "the logged-in session cookie should be persistent"
    )

    # --- index ---
    r = client.get("/")
    assert r.status_code == 200, f"index: {r.status_code}"
    assert b"Settings" in r.data
    token = csrf_token(client)
    assert b'<meta name="csrf-token"' in r.data, "page should expose a CSRF token"
    assert b'nonce="' in r.data, "inline scripts should carry a CSP nonce"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert "default-src 'self'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("Cache-Control") == "no-store"
    assert r.headers.get("X-Robots-Tag", "").startswith("noindex")

    # --- .jinja2 templates must autoescape, not just .html ---
    with app.test_request_context("/login"):
        rendered = render_template(
            "login.jinja2", error="<script>alert(1)</script>"
        )
    assert "<script>alert(1)</script>" not in rendered, (
        "template variables were rendered as raw HTML"
    )
    assert "&lt;script&gt;" in rendered, "template variables should be escaped"

    # --- search: malformed query must not 500 (was: NameError 'logging') ---
    r = client.get('/?q=title:"unterminated')
    assert r.status_code == 200, f"malformed query: {r.status_code}"

    # --- search: author filter ---
    r = client.get("/?q=author:asimov")
    assert r.status_code == 200, f"author search: {r.status_code}"

    # --- settings ---
    r = client.get("/apply_settings?zoom=1.3&color=%23123456", follow_redirects=True)
    assert r.status_code == 200, f"settings: {r.status_code}"
    assert b"zoom: 1.3" in r.data, "zoom setting should be applied"
    assert b"#123456" in r.data, "color setting should be applied"
    # garbage values fall back to defaults without breaking the page
    r = client.get("/apply_settings?zoom=abc&color=notacolor", follow_redirects=True)
    assert r.status_code == 200, f"garbage settings: {r.status_code}"
    # NaN/inf must not make it into the CSS
    r = client.get("/apply_settings?zoom=nan&margin=nan", follow_redirects=True)
    assert r.status_code == 200, f"nan settings: {r.status_code}"
    assert b"zoom: nan" not in r.data, "NaN zoom should be rejected"
    assert b"max-width: 100%" in r.data, "NaN margin should mean no margin"
    r = client.get("/apply_settings?zoom=inf&margin=inf", follow_redirects=True)
    assert r.status_code == 200, f"inf settings: {r.status_code}"
    assert b"zoom: inf" not in r.data, "infinite zoom should be rejected"
    assert b"max-width: 100%" in r.data, "infinite margin should mean no margin"
    r = client.get("/apply_settings?zoom=1&color=%23000000", follow_redirects=True)
    assert r.status_code == 200
    # both sliders step by 0.05
    assert b'name="zoom" type="range" min="0.5" max="1.5" step="0.05"' in r.data, (
        "zoom slider should step by 0.05"
    )
    assert b'name="margin" type="range" min="0" max="0.9" step="0.05"' in r.data, (
        "margin slider should step by 0.05"
    )
    r = client.get("/apply_settings?zoom=1.25&color=%23000000", follow_redirects=True)
    assert b"zoom: 1.25" in r.data, "half-step zoom values should be accepted"

    # --- index layout: grid shows covers without titles ---
    r = client.get(
        "/apply_settings?zoom=1&color=%23000000&layout=grid", follow_redirects=True
    )
    assert r.status_code == 200, f"grid layout: {r.status_code}"
    html = r.data.decode(errors="ignore")
    assert 'class="columns compact"' in html, "grid layout should use compact columns"
    assert 'class="grid-cover"' in html, "grid layout should render covers"
    assert 'data-title="' in html, "grid covers should carry a hover label"
    assert "<h2>" not in html, "grid layout should not show author headings"
    assert 'id="hover-tip"' in html, "grid layout should have the text tooltip"
    assert 'name="layout"' in html, "layout switch should be in the settings form"
    assert '<option value="grid" selected>' in html, "grid should be the selected layout"
    # --- a settings submit without a layout keeps the previous choice ---
    r = client.get("/apply_settings?zoom=1&color=%23000000", follow_redirects=True)
    assert b'columns compact' in r.data, "layout should persist across settings submits"
    # --- an unknown layout falls back to the list template ---
    r = client.get("/apply_settings?layout=notalayout", follow_redirects=True)
    assert r.status_code == 200
    assert b"columns compact" not in r.data, "unknown layout should fall back to list"
    assert b'class="index-cover"' in r.data, "list layout should still render covers"

    # --- side margins: 0.5 leaves the content half the page wide ---
    r = client.get("/apply_settings?margin=0.5", follow_redirects=True)
    assert r.status_code == 200, f"margin setting: {r.status_code}"
    assert b"max-width: 50%" in r.data, "margin 0.5 should halve the content width"
    assert b"html { background-color: black; }" in r.data, (
        "gutters should be the opposite of the text colour"
    )
    assert b"padding: 10px" in r.data, "content should be padded off the gutters"
    # half-size margin steps still land on whole percentages
    r = client.get("/apply_settings?margin=0.35", follow_redirects=True)
    assert b"max-width: 65%" in r.data, "0.05 margin steps should take effect"
    # the gutter colour follows the text colour, not the page colour
    r = client.get("/apply_settings?color=%23ffffff", follow_redirects=True)
    assert b"html { background-color: white; }" in r.data, (
        "white page has black text, so gutters should be white"
    )
    r = client.get("/apply_settings?color=%231a1a2e", follow_redirects=True)
    assert b"html { background-color: black; }" in r.data, (
        "dark blue page has white text, so gutters should be black"
    )
    r = client.get("/apply_settings?margin=0.5&color=%23000000", follow_redirects=True)
    r = client.get(f"/book/{book_id}/chapter/0")
    assert b"max-width: 50%" in r.data, "chapter page should honour the margin"
    # the login page is standalone and stays full width
    assert b"max-width" not in client.get("/login").data, "login should have no margin"
    # out of range and garbage values stay sane
    r = client.get("/apply_settings?margin=5", follow_redirects=True)
    assert b"max-width: 10%" in r.data, "margin should clamp at 0.9 (content 10% wide)"
    r = client.get("/apply_settings?margin=-1", follow_redirects=True)
    assert b"max-width: 100%" in r.data, "negative margin should clamp at 0"
    r = client.get("/apply_settings?margin=abc", follow_redirects=True)
    assert b"max-width: 100%" in r.data, "garbage margin should mean no margin"

    # --- chapter page (chapter 0 must render with a real title) ---
    r = client.get(f"/book/{book_id}/chapter/0")
    assert r.status_code == 200, f"chapter 0: {r.status_code}"
    html = r.data.decode(errors="ignore")
    assert "Chapter 1" in html, "chapter 0 should display as Chapter 1"
    assert "<title>Ebook list</title>" not in html, "chapter-0 page title was wrong"

    # --- a chapter with paragraphs gets paragraph ids injected ---
    with app.app_context():
        row = db.session.execute(
            text(
                'SELECT book_id, "index" FROM chapter '
                "WHERE content LIKE '%<p%' LIMIT 1"
            )
        ).first()
    assert row is not None, "expected at least one chapter with paragraphs"
    pbook_id, pindex = row
    r = client.get(f"/book/{pbook_id}/chapter/{pindex}")
    assert r.status_code == 200, f"paragraph chapter: {r.status_code}"
    assert "paragraph-0" in r.data.decode(errors="ignore"), (
        "paragraph ids should be injected"
    )

    # --- inline images: rewritten to served URLs, and the route works ---
    with app.app_context():
        row = db.session.execute(
            text(
                "SELECT book_id, content FROM chapter "
                "WHERE content LIKE '%/book_images/%' LIMIT 1"
            )
        ).first()
    assert row is not None, "expected at least one chapter with an inline image"
    content = row[1]
    img_src = re.search(r'<img[^>]+src="(/book_images/\d+/[^"]+)"', content).group(1)
    r = client.get(img_src)
    assert r.status_code == 200, f"image route: {r.status_code}"
    assert r.content_type.startswith("image/"), f"content type: {r.content_type}"
    anon = app.test_client()
    r = anon.get(img_src)
    assert r.status_code in (302, 303), "anonymous image access should redirect"
    assert "login" in r.headers.get("Location", "").lower()
    assert client.get("/book_images/99999999/x.jpg").status_code == 404
    assert client.get(f"/book_images/{book_id}/evil.html").status_code == 404, (
        "the image route must not serve non-image files"
    )

    # --- internal links: rewritten to chapter URLs that actually load ---
    with app.app_context():
        row = db.session.execute(
            text(
                "SELECT book_id, content FROM chapter "
                "WHERE content LIKE '%/book/%/chapter/%' LIMIT 1"
            )
        ).first()
        dead = db.session.execute(
            text("SELECT COUNT(*) FROM chapter WHERE content LIKE '%.xhtml%'")
        ).scalar()
    assert row is not None, "expected at least one chapter with an internal link"
    target = re.search(r'href="(/book/\d+/chapter/\d+)"', row[1]).group(1)
    r = client.get(target)
    assert r.status_code == 200, f"internal link target: {r.status_code}"
    assert dead == 0, f"{dead} chapters still reference .xhtml files"

    # --- headings survive extraction somewhere in the library ---
    with app.app_context():
        has_heading = db.session.execute(
            text(
                "SELECT COUNT(*) FROM chapter "
                "WHERE content LIKE '%<h1%' OR content LIKE '%<h2%'"
                " OR content LIKE '%<h3%'"
            )
        ).scalar()
    assert has_heading > 0, "expected headings to survive extraction"

    # --- progress update ---
    token = csrf_token(client)
    progress_headers = {"X-CSRF-Token": token}
    # missing CSRF token
    r = client.post(
        "/update_progress",
        json={"book_id": book_id, "chapter_index": 0, "paragraph_index": 2},
    )
    assert r.status_code == 400, f"CSRF-less progress should be 400, got {r.status_code}"
    # malformed JSON body
    r = client.post(
        "/update_progress",
        data="not json",
        content_type="application/json",
        headers=progress_headers,
    )
    assert r.status_code == 400, f"malformed progress JSON: {r.status_code}"
    # unknown book / chapter must not create orphan progress rows
    r = client.post(
        "/update_progress",
        json={"book_id": 9999999, "chapter_index": 0, "paragraph_index": 0},
        headers=progress_headers,
    )
    assert r.status_code == 404, f"unknown book progress: {r.status_code}"
    r = client.post(
        "/update_progress",
        json={"book_id": book_id, "chapter_index": 999999, "paragraph_index": 0},
        headers=progress_headers,
    )
    assert r.status_code == 400, f"unknown chapter progress: {r.status_code}"
    # non-integer values are rejected
    r = client.post(
        "/update_progress",
        json={"book_id": str(book_id), "chapter_index": 0, "paragraph_index": 0},
        headers=progress_headers,
    )
    assert r.status_code == 400, f"string progress values: {r.status_code}"

    r = client.post(
        "/update_progress",
        json={"book_id": book_id, "chapter_index": 0, "paragraph_index": 2},
        headers=progress_headers,
    )
    assert r.status_code == 200, f"update_progress: {r.status_code}"
    assert b"success" in r.data, f"update_progress body: {r.data[:100]}"

    # --- progress persists ---
    with app.app_context():
        p = BookProgress.query.filter_by(
            book_id=book_id, user_id=user_id
        ).first()
        assert p is not None, "progress row should exist"
        assert p.paragraph_index == 2, (
            f"paragraph_index is {p.paragraph_index}, want 2"
        )

    # --- add_tags with empty input must not create an empty tag ---
    token = csrf_token(client)
    r = client.post(
        "/add_tags",
        data={"book_id": book_id, "tag_names": "", "csrf_token": token},
    )
    assert r.status_code in (302, 303), f"add_tags empty: {r.status_code}"
    with app.app_context():
        assert not Tag.query.filter_by(name="").first(), "empty tag was created!"

    # --- add_tags with real tags ---
    r = client.post(
        "/add_tags",
        data={
            "book_id": book_id,
            "tag_names": "testtag, sci-fi",
            "csrf_token": token,
        },
    )
    assert r.status_code in (302, 303), f"add_tags: {r.status_code}"
    with app.app_context():
        tags = {t.name for t in db.session.get(Book, book_id).tags}
    assert {"testtag", "sci-fi"} <= tags, f"tags not set: {tags}"

    # --- search by tag (book must be visible) ---
    with app.app_context():
        db.session.get(Book, book_id).is_hidden = False
        db.session.commit()
    book_title = None
    with app.app_context():
        book_title = db.session.get(Book, book_id).title
    r = client.get("/?q=tag:testtag")
    assert r.status_code == 200, f"tag search: {r.status_code}"
    assert book_title.encode() in r.data, "tag search should find the book"

    # --- state changes are POST-only, so a GET cannot be CSRF'd ---
    assert client.get(f"/hide/{book_id}").status_code == 405
    assert client.get("/logout").status_code == 405
    assert client.get("/trigger_load_books").status_code == 405

    # --- hide requires login, and CSRF is checked first ---
    anon = app.test_client()
    r = anon.get(f"/hide/{book_id}")
    assert r.status_code == 405, f"anon GET hide should be 405, got {r.status_code}"
    anon.get("/login")
    anon_token = csrf_token(anon)
    r = anon.post(f"/hide/{book_id}", data={})
    assert r.status_code == 400, f"CSRF-less hide should be 400, got {r.status_code}"
    r = anon.post(f"/hide/{book_id}", data={"csrf_token": anon_token})
    assert r.status_code in (302, 303), f"anon hide should redirect, got {r.status_code}"
    assert "login" in r.headers.get("Location", "").lower(), (
        f"hide should redirect to login, got {r.headers.get('Location')}"
    )

    # --- hide works when logged in, removes current user's progress ---
    token = csrf_token(client)
    r = client.post(
        f"/hide/{book_id}",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200, f"hide: {r.status_code}"
    with app.app_context():
        assert db.session.get(Book, book_id).is_hidden is True, "book should be hidden"
        assert not BookProgress.query.filter_by(
            book_id=book_id, user_id=user_id
        ).first(), "current user's progress should be removed on hide"

    # --- covers are not readable without a session ---
    cover_path = pathlib.Path("static") / f"cover-{book_id}.jpg"
    if cover_path.exists():
        cover_url = f"/static/cover-{book_id}.jpg"
        assert anon.get(cover_url).status_code == 404, (
            "anonymous cover access should be hidden"
        )
        assert client.get(cover_url).status_code == 200, (
            "authenticated cover access should work"
        )

    # --- crawlers are told to stay out, on both the app and the headers ---
    r = anon.get("/robots.txt")
    assert r.status_code == 200, f"robots.txt: {r.status_code}"
    assert b"Disallow: /" in r.data
    assert "noindex" in r.headers.get("X-Robots-Tag", "")

    # --- 404s ---
    assert client.get("/book/9999999/chapter/0").status_code == 404, (
        "missing book should 404"
    )
    assert client.get(f"/book/{book_id}/chapter/999999").status_code == 404, (
        "missing chapter should 404"
    )

    # --- login brute force is throttled ---
    rl = app.test_client()
    rl.get("/login")
    rl_token = csrf_token(rl)
    for _ in range(10):
        r = rl.post(
            "/login",
            data={
                "username": "ratelimit-probe",
                "password": "wrong",
                "csrf_token": rl_token,
            },
        )
        assert r.status_code == 401, f"probe login should be 401, got {r.status_code}"
    r = rl.post(
        "/login",
        data={
            "username": "ratelimit-probe",
            "password": "wrong",
            "csrf_token": rl_token,
        },
    )
    assert r.status_code == 429, f"11th probe login should be 429, got {r.status_code}"
    assert "Retry-After" in r.headers, "429 should tell the client when to retry"
    limiter.reset()

    # --- logout is POST-only and clears the session ---
    token = csrf_token(client)
    r = client.post("/logout", data={"csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200, f"logout: {r.status_code}"
    assert b"log in" in r.data.lower(), "should land on the login page"


def main():
    with app.app_context():
        # Recover cleanly from an earlier interrupted run, which can leave
        # duplicate testbot rows behind.
        for existing in User.query.filter_by(username=TEST_USER).all():
            BookProgress.query.filter_by(user_id=existing.id).delete()
            db.session.delete(existing)
        db.session.commit()

        tags_snapshot = {b.id: [t.name for t in b.tags] for b in Book.query.all()}
        hidden_snapshot = {b.id: b.is_hidden for b in Book.query.all()}
        book = Book.query.first()
        book_id = book.id
        user = User(username=TEST_USER)
        user.set_password(TEST_PASSWORD)
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    try:
        run_tests(app.test_client(), book_id, user_id)
    finally:
        with app.app_context():
            cleanup(tags_snapshot, hidden_snapshot)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
