# Design Decisions

This document explains the main design choices in the Smart QR Attendance
System, what each one protects against, and the known limitations. It is
written to support the project defence.

---

## 1. Architecture

- **A server-rendered Flask application, not a single-page app with an
  API.** Every permission check happens on the server, and pages work on any
  phone browser without installing anything. It is also a manageable scope
  for one developer.
- **An application factory (`create_app`) with blueprints for each area**
  (`auth`, `admin`, `courses`, `attendance`, `reports`). Tests can build an
  isolated app with its own in-memory database, and each blueprint can be
  read on its own.
- **SQLite**, because a single department runs one server and SQLite needs
  no separate database service. Two settings are added to every connection:
  - `foreign_keys=ON`. SQLite ignores foreign keys unless this is set, so
    without it orphaned rows would be possible.
  - **WAL mode** with a 5-second `busy_timeout`. When I measured commits in
    the project folder on the development machine, WAL cut them from about
    380 ms to about 200 ms. It also lets pages read the database while a
    write is in progress. When a whole class submits at once, writes queue
    instead of failing with "database is locked".
- **Times are stored in UTC and shown in West Africa Time (UTC+1).** Nigeria
  does not use daylight saving, so a fixed offset is exact and needs no
  timezone database (which Windows does not ship).

## 2. Data model

| Table | Key rules enforced by the database |
|---|---|
| `users` | Unique email; unique student number; `role` must be admin, lecturer or student (CHECK constraint) |
| `courses` | Unique course code; foreign key to the lecturer |
| `enrollments` | `UNIQUE(student_id, course_id)` |
| `attendance_sessions` | Unique `qr_token_hash`; `expires_at`; `is_active` |
| `attendance_records` | **`UNIQUE(session_id, student_id)`**; `status IN ('present')` |

- **Absence is not stored.** A student is absent from a session when they
  have no record for it. This avoids creating rows for every student at
  every session, and a stored "absent" can never contradict a stored
  "present".
- **History is never deleted.** A course with sessions or enrolments cannot
  be deleted. A student with attendance in a course cannot be removed from
  it. This keeps registers accurate.
- **Roles cannot be changed after an account is created.** Otherwise a
  lecturer who owns courses could become a student, leaving those courses
  with an invalid lecturer.

## 3. QR attendance protocol

1. The lecturer starts a session with a chosen duration (5 to 60 minutes).
   Only one session per course can be open at a time.
2. The server generates the token with `secrets.token_urlsafe(32)`: 256 bits
   from the operating system's cryptographically secure random generator.
3. **Only the SHA-256 hash of the token is stored.** Someone who obtains a
   copy of the database cannot rebuild a working QR code. A fast hash is
   acceptable here (unlike for passwords) because guessing one value out of
   2^256 is not possible, so a slow hash adds no protection.
4. The QR code encodes `https://<server>/attend/<token>`. The lecturer's page
   keeps the raw token only in the lecturer's own signed session cookie,
   which lets the QR code be shown again after a page refresh.
5. When a student opens the link:
   - Login is required. The scan page is remembered in the session, not in a
     `?next=` URL parameter, so the token does not appear in login URLs.
   - **Opening the page records nothing.** Attendance is recorded only by a
     POST request with a CSRF token. This stops link-preview bots and
     prefetching from marking attendance.
   - The server checks each of the following, using its own clock, in this
     order: token exists (404), session not closed (410), not expired (410),
     student enrolled (403), not already recorded (409). Then it inserts the
     record (201).
6. **Duplicates are blocked at two levels.** The application checks first,
   and `UNIQUE(session_id, student_id)` blocks any race the check misses,
   such as two taps in the same millisecond. The resulting `IntegrityError`
   is caught and shown as "already recorded".
7. **Regenerating** replaces the stored hash, so the old code stops working
   immediately. **Closing** sets `is_active = False` and moves `expires_at`
   to the closing time, so the database records when the session really
   ended.

## 4. Attendance calculation (`reports.py`)

1. Only finished sessions count. Otherwise a session still in progress would
   lower every percentage during class.
2. A session counts for a student only if it started after they enrolled.
   The exception is a student who enrolled during a session and then
   attended it; that session counts as present.
3. Percentage = attended ÷ sessions that count for that student, rounded to
   one decimal place. With no counted sessions it shows "—", not a
   misleading 0%.
4. Date filters are inclusive and use the **local (WAT) date** of the
   session start.

`test_reports.py` checks these rules against figures worked out by hand (the
calculation is in its docstring). I also confirmed the tests can fail: when
I deliberately broke rule 2, 7 tests failed.

## 5. Security measures

| Threat | Measure |
|---|---|
| Stolen password database | Passwords hashed with scrypt (Werkzeug default); QR tokens stored only as hashes |
| Forged session cookies | `SECRET_KEY` required in production. The app will not start with a missing key, and there is no default key |
| Cross-site request forgery | Flask-WTF `CSRFProtect` on every POST, including logout; tested with CSRF switched on |
| Users opening pages for another role, or another lecturer's course | `role_required()` on every route, plus ownership checks (`get_owned_course`, `get_owned_session`, `get_viewable_course`) that return 403. Tested for each role |
| Open redirect after login | `safe_next_url()` accepts only paths on this site; `//evil` and `https://evil` are rejected |
| Clickjacking the "Mark me present" button | `X-Frame-Options: DENY` |
| Tokens leaking through caches or the Referer header | `Cache-Control: no-store` on token pages; `Referrer-Policy: same-origin` |
| Script injection (XSS) | Jinja2 escapes all output automatically; no user content is marked safe |
| CSV / spreadsheet formula injection | Cells starting with `= + - @` are prefixed with `'` |
| Revealing which emails have accounts | Login gives the same message for an unknown email and a wrong password |
| Session hijacking | `HttpOnly` and `SameSite=Lax` cookies; `Secure` can be switched on for HTTPS |
| Data left behind on a shared computer | Logout clears the whole session, including any QR tokens a lecturer was displaying |
| Collecting more personal data than needed | CSV export leaves out email addresses; sample data is fictitious with `@example.com` addresses |

## 6. Known limitations and future work

- **Proxy attendance.** A student can still forward a photo of the QR code to
  a friend outside the room while the code is valid. Short durations and
  regenerating the code reduce this but do not stop it. Possible additions:
  a code that changes automatically every 30 seconds, location checks, or
  linking an account to one device.
- **No login rate limiting.** A library such as Flask-Limiter would add it.
- **Login timing.** Login skips the password check when the email is
  unknown, so response times could reveal which emails have accounts.
- **No self-service password reset by email**, since that needs a mail
  server. Admins reset passwords instead.
- **Accounts cannot be deactivated** without a schema change (an `is_active`
  column). This was left out to avoid altering the existing database.
- **SQLite allows one write at a time.** This is fine for one department; a
  whole university would need PostgreSQL. SQLAlchemy makes that switch
  mostly a configuration change.
- **No schema migrations.** Adding Flask-Migrate (Alembic) would be the
  first step before any schema change.
