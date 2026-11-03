# Smart QR Attendance System

A web application for taking class attendance with time-limited QR codes.
It was built as a final-year Computer Science project at the University of
Ibadan.

- **Administrators** manage user accounts and courses, and can view every
  attendance register.
- **Lecturers** manage their own courses and enrolments, run QR attendance
  sessions, and view or export registers.
- **Students** scan the QR code shown in class to record attendance, and
  can see their own attendance percentages.

Built with Python, Flask, Flask-SQLAlchemy (SQLite), Flask-Login, Flask-WTF
and `qrcode`. The design choices, and the reasons for them, are explained in
[DESIGN.md](DESIGN.md).

---

## 1. Installation (Windows)

You need Python 3.12 or newer. The commands below are for PowerShell and are
run from the project folder.

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` pins the direct dependencies. To reproduce the exact
environment the tests were run in, install from `requirements-lock.txt`
instead.

On macOS or Linux, use `source venv/bin/activate` in place of
`venv\Scripts\Activate.ps1`.

## 2. Create the database

```powershell
python init_db.py            # creates instance/attendance.db (tables only)
python init_db.py --seed     # also adds fictitious sample data
```

`--seed` adds 3 staff accounts, 8 students, 3 courses and several weeks of
finished attendance sessions, so the registers have data to show.

- Every sample person is fictitious and uses an `@example.com` address.
- New sample accounts get a **random password, printed once**. To choose one
  yourself, use `--password "YourDemoPass1"` (at least 8 characters).
- Accounts that already exist are never changed. Running the script again
  does not create duplicates.
- The script refuses to seed when `SECRET_KEY` is set and `FLASK_DEBUG` is
  not `1`, since that looks like a production server. Use `--force` to
  override.

Sample logins: `admin@example.com`, `lecturer@example.com`,
`lecturer2@example.com`, `student@example.com`, and
`student02@example.com` to `student08@example.com`.

## 3. Run for development

```powershell
flask --app app run --debug
```

Open <http://127.0.0.1:5000>. In debug mode, a random development secret key
is created once and stored in `instance/dev-secret-key`. That file is not
committed, and each machine gets its own key. Delete the file to sign
everyone out.

In debug mode only, the lecturer's QR page also shows the scan link as text,
so you can test the student side without a phone. It is never shown in
production, because a link shared as text makes proxy attendance easier.

### Testing QR scanning with a phone

Phones cannot reach `localhost`, so the server must listen on your network:

```powershell
flask --app app run --debug --host 0.0.0.0
```

Then, on the lecturer's computer, open the app using its network address
(run `ipconfig` and look for the IPv4 address), for example
`http://192.168.0.10:5000`. QR codes use the address the lecturer's browser
is using. If they differ, set `PUBLIC_BASE_URL` (see below).

> **Warning:** with `--debug`, the Werkzeug debugger is reachable by anyone on
> that network. Only do this on a network you trust. Windows Firewall may ask
> you to allow Python.

## 4. Configuration

All settings are environment variables. In PowerShell, set one with
`$env:NAME = "value"`.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | **Required in production.** Signs session cookies and CSRF tokens. The app refuses to start without it unless `FLASK_DEBUG=1` (which uses `instance/dev-secret-key`). Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. Never commit it to the repository. |
| `FLASK_DEBUG` | `1` for development only. |
| `DATABASE_URL` | Optional. Defaults to `sqlite:///instance/attendance.db`. |
| `SESSION_COOKIE_SECURE` | Set to `1` when the site is served over HTTPS. |
| `PUBLIC_BASE_URL` | Optional address to put in QR codes, e.g. `http://192.168.0.10:5000`. |

The attendance warning threshold (75%) is `ATTENDANCE_THRESHOLD` in
`app.py`.

### Running in production

The `flask run` server is for development only. On Windows, a production WSGI
server such as Waitress can be used. It is not included in
`requirements.txt`.

```powershell
pip install waitress
$env:SECRET_KEY = "<your generated key>"
waitress-serve --listen=0.0.0.0:8000 --call app:create_app
```

Serve the app over HTTPS (for example, behind a reverse proxy) and set
`SESSION_COOKIE_SECURE=1`.

## 5. Running the tests

```powershell
python -m unittest discover -p "test_*.py"
```

Add `-v` to list each test. The tests use in-memory SQLite databases and
never touch `instance/attendance.db`.

| File | Covers |
|---|---|
| `test_auth.py` | Login, logout, role dashboards, access denied |
| `test_course_management.py` | Course CRUD, ownership, enrolment |
| `test_admin.py` | User management, admin course assignment, password change, CSRF |
| `test_attendance.py` | QR tokens: valid, invalid, expired, closed, regenerated; duplicates; enrolment checks; login redirect |
| `test_reports.py` | Register percentages against hand-calculated values, date filters, CSV export, access control |
| `test_database.py` | Models, constraints, session expiry |
| `test_config.py` | Secret-key rules, foreign keys, password hashing, logout method |
| `test_seed.py` | Sample data script |

## 6. How to use it

1. **Admin:** create lecturer and student accounts (**Users → Create user**).
   Create courses yourself (**Courses → Create course**), or let lecturers
   create their own.
2. **Lecturer:** open a course, enrol students, then choose a duration and
   **Start attendance session**. Display the QR code on the projector. The
   present count updates live. Use **Regenerate QR code** if the code may
   have been shared, and **Close session** when done.
3. **Student:** scan the code with the phone camera, sign in if asked, and
   tap **Mark me present**.
4. **Reports:** open **Register** for a course. Filter by date and use
   **Download CSV**. Students see their own figures under
   **My attendance**.

## 7. Backups (important)

The database uses SQLite's **write-ahead log (WAL)**. While the app is
running, recent changes may sit in `attendance.db-wal` instead of the main
file. To back up, either:

- stop the app, then copy `instance/attendance.db`; or
- copy `attendance.db`, `attendance.db-wal` and `attendance.db-shm`
  together.

## 8. Project structure

```
app.py            Application factory, config, dashboards, security headers
extensions.py     db / login / CSRF objects; SQLite connection settings
models.py         User, Course, Enrollment, AttendanceSession, AttendanceRecord
decorators.py     role_required()
auth.py           Login, logout, password change, safe redirects
admin.py          User management (admin only)
courses.py        Course and enrolment management
attendance.py     QR sessions (lecturer) and scanning (student)
reports.py        Register calculation, CSV export, student summary
forms.py          WTForms definitions and validation rules
init_db.py        Create tables; optional sample data
templates/        Jinja2 templates
static/           Stylesheet
test_*.py         Automated tests (unittest)
```
