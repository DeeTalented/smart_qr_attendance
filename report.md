# Smart QR Attendance System for Educational Institutions

**Draft text for Chapter 4 (Implementation, Testing and Results) and Chapter 5 (Summary, Conclusion and Recommendations)**

> **How to use this draft.** It describes only what was actually built, measured and
> tested in this project. Check every statement against your own understanding before
> you submit it. Edit the wording into your own voice, match the objective numbers in
> Section 4.9 to the ones in your Chapter 1, and renumber figures and tables to fit
> your report template. Figure numbers (Figure 4.1 to 4.32) follow the screenshot set
> and its `CAPTIONS.md`. Text in *[square brackets and italics]* is a placeholder for
> you to complete.

---

# CHAPTER FOUR: SYSTEM IMPLEMENTATION, TESTING AND RESULTS

## 4.1 Introduction

This chapter describes how the Smart QR Attendance System designed in Chapter Three was implemented, tested and evaluated. It covers the development environment, how the system and database were implemented, each functional module with screenshots of the working system, the security measures, the testing strategy and its results, the defects found and corrected, and an evaluation of the system against the project objectives.

## 4.2 Development Environment and Tools

The system was developed and tested on a personal computer running Windows 10 Pro, using the tools in Table 4.1.

**Table 4.1: Development tools and libraries**

| Component | Tool / Library | Version | Purpose |
|---|---|---|---|
| Programming language | Python | 3.12.1 | Application logic |
| Web framework | Flask | 3.1.3 | Routing, request handling, templating |
| Object-relational mapper | Flask-SQLAlchemy / SQLAlchemy | 3.1.1 / 2.1.4 | Database models and queries |
| Database | SQLite | (bundled with Python) | Persistent storage |
| Authentication | Flask-Login | 0.6.3 | User sessions and login management |
| Forms and CSRF protection | Flask-WTF / WTForms | 1.3.0 / 3.2.2 | Form validation and CSRF tokens |
| Email validation | email-validator | 2.3.0 | Validating email address format |
| QR code generation | qrcode (with Pillow) | 8.2 | Generating QR codes as SVG images |
| Password hashing | Werkzeug (scrypt) | 3.1.9 | Secure password storage |
| Testing | Python `unittest` | (standard library) | Automated tests |
| Templating and interface | Jinja2, HTML5, CSS3 | 3.1.6 | Server-rendered pages, responsive design |

All direct dependencies are pinned in `requirements.txt`. The complete environment, including indirect dependencies, is pinned in `requirements-lock.txt`, so the system can be installed again with identical versions.

## 4.3 System Implementation

### 4.3.1 Architecture

The system is a server-rendered web application built with Flask's application-factory pattern (`create_app`). Each functional area is a separate module (a Flask "blueprint"), as shown in Table 4.2. A server-rendered design was chosen over a separate front-end and API because every permission check then runs on the server, and students need only a phone browser: there is nothing to install.

**Table 4.2: Application modules**

| Module (file) | Responsibility |
|---|---|
| `app.py` | Application factory, configuration, dashboards, security headers, error pages |
| `extensions.py` | Shared database, login and CSRF objects; SQLite connection settings |
| `models.py` | Database models: users, courses, enrolments, attendance sessions and records |
| `auth.py` | Login, logout, password change, safe redirect after login |
| `admin.py` | User management (administrator only) |
| `courses.py` | Course and enrolment management |
| `attendance.py` | QR attendance sessions (lecturer) and QR scanning (student) |
| `reports.py` | Attendance register calculation, CSV export, student summaries |
| `decorators.py` | The `role_required` access-control decorator |
| `init_db.py` | Database creation and fictitious sample data |

### 4.3.2 Database Implementation

The database was implemented in SQLite through SQLAlchemy models. It has five tables, and important rules are enforced by the database itself rather than only by application code (Table 4.3).

**Table 4.3: Database tables and the rules the database enforces**

| Table | Purpose | Rules enforced by the database |
|---|---|---|
| `users` | Administrator, lecturer and student accounts | Unique email; unique student number; role restricted to `admin`, `lecturer` or `student` (CHECK constraint) |
| `courses` | Courses and their assigned lecturer | Unique course code; foreign key to the lecturer |
| `enrollments` | Links students to courses | Unique (student, course) pair |
| `attendance_sessions` | One attendance-taking event per class | Unique QR token hash; expiry time; open/closed flag |
| `attendance_records` | A student's attendance for a session | **Unique (session, student) pair**; status restricted to `present` |

Three implementation decisions are worth noting:

1. **Foreign keys are switched on for every connection.** SQLite ignores foreign keys by default, so a connection hook sets `PRAGMA foreign_keys=ON` to stop orphaned records from being created.
2. **Write-ahead logging (WAL) is enabled.** On the development machine, each write took about 380 ms in SQLite's default mode and about 200 ms in WAL mode. WAL also lets pages read the database while attendance is being written. A 5-second busy timeout makes simultaneous submissions wait their turn instead of failing.
3. **Absence is not stored.** A student is absent from a session when no record exists for them. This avoids creating rows for every student at every session, and a stored "absent" can never contradict a stored "present".

All timestamps are stored in Coordinated Universal Time (UTC) and displayed in West Africa Time (UTC+1). Nigeria does not use daylight saving time, so a fixed one-hour offset is exact.

### 4.3.3 QR Attendance Protocol

The QR attendance process is the core of the system and works as follows:

1. **Starting a session.** The lecturer chooses a duration (5, 10, 15, 30 or 60 minutes) and starts a session for a course they own. Only one session per course may be open at a time.
2. **Generating the token.** The server generates a random token with `secrets.token_urlsafe(32)`, which uses the operating system's cryptographically secure random number generator and gives 256 bits of randomness. **Only the SHA-256 hash of the token is stored.** Someone who obtains a copy of the database therefore cannot reconstruct a working QR code. A fast hash is acceptable here, unlike for passwords, because guessing one value out of 2²⁵⁶ is not feasible.
3. **Displaying the QR code.** The QR code encodes the address `/attend/<token>`. It is generated on the server as an SVG image and shown with a live countdown and a present count that updates as students sign in.
4. **Scanning.** The student scans the code with their phone camera and signs in if necessary. Opening the link only shows a confirmation page. **Attendance is recorded only when the student submits a form protected by a CSRF token**, so a link-preview service or browser prefetch cannot mark attendance.
5. **Server-side checks.** Each submission is checked on the server, in order, against the server's own clock (Table 4.4).
6. **Preventing duplicates at two levels.** The application first checks for an existing record. The database's unique (session, student) constraint then blocks any race the check misses, such as two taps a few milliseconds apart. The resulting database error is caught and the student is told the attendance is already recorded.
7. **Regenerating and closing.** Regenerating the code replaces the stored hash, so the old code stops working immediately. Closing the session stops attendance and records the actual end time.

**Table 4.4: Server-side checks on each attendance submission**

| Order | Check | Result if it fails | HTTP status |
|---|---|---|---|
| 1 | The token matches a session | "This QR code is not valid" | 404 |
| 2 | The session has not been closed | "The lecturer has closed this attendance session" | 410 |
| 3 | The session has not expired | "This attendance session has expired" | 410 |
| 4 | The student is enrolled in the course | "You are not enrolled in this course" | 403 |
| 5 | Attendance is not already recorded | "Already recorded" | 409 |
| — | All checks pass | Attendance recorded | 201 |

### 4.3.4 Attendance Calculation

Registers and percentages follow four rules, implemented in one place (`reports.py`) so they are easy to explain and test:

1. **Only finished sessions are counted.** Otherwise a session still in progress would lower every student's percentage during class.
2. **A session counts for a student only if it started after they enrolled**, because they could not have attended before then. The exception is a student who enrolled during a session and then attended it; that session counts as present.
3. **Percentage = sessions attended ÷ sessions that count for that student**, rounded to one decimal place. When no sessions count yet, "—" is shown instead of a misleading 0%.
4. **Date filters are inclusive and use the local (West Africa Time) date** on which each session started.

Students below a configurable threshold, set to 75%, are highlighted on registers and warned on their own pages.

## 4.4 System Modules and Interface

This section presents the working system. All screenshots were produced by the real application using fictitious sample data (`@example.com` accounts). Phone screenshots show the 390-pixel width of a typical smartphone.

### 4.4.1 User Authentication

Every user signs in with an email address and password (Figure 4.1). A failed login gives the same generic message whether the email is unknown or the password is wrong (Figure 4.2), so the login page cannot be used to discover which email addresses have accounts. After login, users are sent to the dashboard for their role.

*[Insert Figure 4.1: Login page]*
*[Insert Figure 4.2: Failed login attempt showing a generic error message]*

### 4.4.2 Administrator Module

The administrator dashboard (Figure 4.3) summarises the numbers of students, lecturers, courses and sessions held. Administrators create and edit accounts and reset passwords (Figures 4.4 and 4.5). All input is validated on the server. Duplicate email addresses are rejected regardless of letter case, student accounts must have a unique student number (Figure 4.6), and passwords must be at least eight characters long and confirmed (Figure 4.7). Administrators can view every course and assign or reassign lecturers (Figures 4.8 and 4.9). A user's role cannot be changed after the account is created. Otherwise a lecturer who owns courses could become a student, leaving those courses with an invalid lecturer.

*[Insert Figures 4.3 to 4.9]*

### 4.4.3 Lecturer Module and Course Management

The lecturer dashboard (Figure 4.10) shows the lecturer's courses, enrolments, sessions held and any session currently open. Lecturers create and manage their own courses (Figures 4.11 and 4.12). The course page (Figure 4.13) is where students are enrolled and attendance sessions are started. Each session's present count is calculated using the same rule as the register: it counts only students who were enrolled when that session started. A lecturer can open only their own courses; requests for another lecturer's course are refused with HTTP 403.

*[Insert Figures 4.10 to 4.13]*

### 4.4.4 QR Attendance Session

When a session is started, the lecturer's screen shows the QR code, a countdown and the present count (Figure 4.14). The count updates while students sign in, and the list of students present grows (Figure 4.15). When the lecturer closes the session, the actual end time and the final list are kept (Figure 4.16).

*[Insert Figures 4.14 to 4.16]*

### 4.4.5 Recording Attendance on a Student's Phone

Scanning the code opens a confirmation page on the student's phone (Figure 4.17). Tapping "Mark me present" records attendance (Figure 4.18). The system's protections can be seen in Figures 4.19 to 4.22: a second attempt is rejected as a duplicate, an invalid or replaced code is rejected, an expired code is rejected, and a student not enrolled in the course is refused.

*[Insert Figures 4.17 to 4.22]*

### 4.4.6 Attendance Reports

The attendance register (Figure 4.23) shows every enrolled student against every counted session, with the number attended and a percentage for each student, and an average for the course. Rows below the 75% threshold are highlighted. The register can be filtered by date range (Figure 4.24) and exported as a CSV file that opens in spreadsheet software. The export leaves out email addresses, because the register does not need them. Any cell beginning with `=`, `+`, `-` or `@` is prefixed with an apostrophe, so text such as a crafted student name cannot run as a formula when the file is opened in Excel (CSV injection).

*[Insert Figures 4.23 and 4.24]*

### 4.4.7 Student Module

Students see their overall attendance and a progress bar for each course on their dashboard (Figures 4.25 and 4.26), and a full session-by-session history (Figure 4.27). A warning appears when a student falls below the minimum. Students can see only their own records.

*[Insert Figures 4.25 to 4.27]*

### 4.4.8 Security, Account Management and Interface

Every page is protected by server-side role checks. For example, a student who tries to open a lecturer page is refused (Figure 4.28). Users can change their own password after re-entering their current one (Figure 4.29). The interface adapts to phones and follows the device's light or dark theme (Figure 4.30). The QR code is always shown on a white background so that it can still be scanned in dark mode.

*[Insert Figures 4.28 to 4.30]*

## 4.5 Security Implementation

Table 4.5 summarises each threat the system was designed against and how it is addressed.

**Table 4.5: Security threats and countermeasures**

| Threat | Countermeasure |
|---|---|
| A stolen database exposing passwords | Passwords hashed with scrypt; QR tokens stored only as SHA-256 hashes |
| Forged session cookies | A secret key is required in production; the application refuses to start without one, and there is no built-in default key |
| Cross-site request forgery (CSRF) | A CSRF token on every state-changing form, including logout |
| Users accessing another role's pages, or a lecturer opening another lecturer's course | Role check on every route plus ownership checks, returning HTTP 403 |
| Open redirect after login (sending users to an attacker's site) | Only paths on the same site are accepted as the post-login destination |
| Clickjacking (hiding the "Mark me present" button inside another site) | `X-Frame-Options: DENY` header |
| QR tokens leaking through caches or the Referer header | `Cache-Control: no-store` on pages containing tokens; `Referrer-Policy: same-origin` |
| Script injection (cross-site scripting) | Automatic escaping of all output by the Jinja2 templates |
| CSV / spreadsheet formula injection | Dangerous leading characters are neutralised in exported cells |
| Revealing which email addresses have accounts | The same login error for an unknown email and a wrong password |
| Data left behind on a shared computer | Logout clears the entire session, including any QR tokens a lecturer was displaying |
| Collecting more personal data than needed | Email addresses are left out of the CSV export; sample data is fictitious |

## 4.6 Testing

### 4.6.1 Testing Strategy

The system was tested at three levels:

1. **Automated tests:** 98 tests written with Python's `unittest` framework. They exercise the models, the routes and the full request–response cycle through Flask's test client. Each test runs against its own in-memory database, so the tests are isolated from each other and never touch real data.
2. **Mutation checking:** defects were deliberately introduced into the report calculation to confirm that the tests detect them (Section 4.6.3).
3. **End-to-end walkthrough:** the complete workflow was carried out by hand in a web browser for all three roles, including the student flow at phone screen size (Section 4.7).

### 4.6.2 Automated Test Results

The full suite was run with `python -m unittest discover -p "test_*.py" -v`. **All 98 tests passed** (`Ran 98 tests in 7.730s — OK`). Table 4.6 breaks the tests down by area, and the verbatim output is shown in Figures 4.31 and 4.32.

**Table 4.6: Automated tests by area**

| Test file | Tests | Areas covered | Result |
|---|---|---|---|
| `test_auth.py` | 6 | Valid and invalid login, logout, role dashboards, access denied | 6 passed |
| `test_course_management.py` | 7 | Course creation, duplicate codes, ownership, enrolment | 7 passed |
| `test_admin.py` | 19 | User management, validation, password reset and change, admin course assignment, CSRF | 19 passed |
| `test_attendance.py` | 22 | Valid, invalid, expired, closed and regenerated QR tokens; duplicates; enrolment checks; login redirect; security headers; logout clearing tokens | 22 passed |
| `test_reports.py` | 22 | Register percentages against hand-calculated values, date filters (including time-zone boundaries), CSV export and injection protection, access control | 22 passed |
| `test_database.py` | 6 | Models, unique constraints, session expiry | 6 passed |
| `test_config.py` | 9 | Secret-key rules, foreign-key enforcement, password hashing, cookie settings, logout method | 9 passed |
| `test_seed.py` | 7 | Sample-data script: fictitious data, safe to run twice, existing accounts preserved | 7 passed |
| **Total** | **98** | | **98 passed, 0 failed** |

*[Insert Figure 4.31 and Figure 4.32: Automated test results]*

The report-accuracy tests compare the system's output with figures calculated by hand. In one scenario of four sessions and four students, the expected percentages of 100%, 50%, 0% and 100% (the last for a student who enrolled late and attended the only session that counted for them) and a course average of 62.5% were all reproduced exactly. That includes every cell of the exported CSV file.

### 4.6.3 Mutation Checking of the Tests

A passing test suite is only meaningful if the tests can fail. To check this, two defects were introduced temporarily, in memory only, and the report tests were run against each:

**Table 4.7: Mutation checking results**

| Deliberate defect | Tests that failed | Detected? |
|---|---|---|
| Counting sessions held before a student enrolled | 7 | Yes |
| Removing CSV formula-injection protection | 1 | Yes |

Both defects were detected, and the suite passed again once the original code was restored.

## 4.7 Defects Found and Corrected

Table 4.8 lists the significant defects found during the project and how each was resolved. Defects 1–5 were found when the inherited Phase 1–4 code was audited. When the audit began the application could not start, and none of the existing tests could run. Defects 6–9 were found in the end-to-end browser walkthrough after the main features were complete.

**Table 4.8: Defects found and corrected**

| # | Defect | Effect | Correction |
|---|---|---|---|
| 1 | Syntax error and missing function for the unenrol route (`courses.py`) | Application could not start | Route rewritten as a complete function |
| 2 | Wrong and missing imports (`role_required`, `LogoutForm`, an undefined `auth` name) | Application could not start | Imports corrected |
| 3 | Course module registered outside the application factory | Course pages missing in test applications | All modules registered inside `create_app` |
| 4 | Hard-coded development secret key and debug mode | Session cookies could be forged | Secret key required in production; debug mode controlled by the environment |
| 5 | SQLite foreign keys not enforced; stylesheet folder misnamed | Orphaned records possible; styles not loading | Foreign keys switched on for every connection; folder renamed |
| 6 | QR tokens survived logout in the browser session | Tokens could be extracted on a shared computer | Logout now clears the entire session |
| 7 | Course page "Present X / Y" counted students who enrolled later | Misleading totals | Uses the same rule as the register |
| 8 | Development server restart caused "400 Bad Request" on forms | Poor developer experience | One development key per machine, plus a friendly "please try again" page |
| 9 | Students were not told that a session in progress is not yet counted | Possible confusion | Explanatory notice added |

A regression test was added for each defect fixed during the walkthrough, so that it cannot silently return.

## 4.8 Performance Observations

On the development machine, a single attendance write took about 380 ms with SQLite's default journal mode and about 200 ms in WAL mode, which the system now uses. Because SQLite permits one write at a time, submissions from a whole class are processed one after another. Each submission waits up to five seconds for its turn rather than failing. *[If you carry out a timed classroom trial, record the class size, how long sign-in took, and any errors here.]* No formal load test with many simultaneous users was carried out, so the system's capacity is an estimate rather than a measured result.

## 4.9 Evaluation Against Objectives

> *Align these rows with the exact objectives stated in your Chapter 1.*

**Table 4.9: Evaluation of the system against the project objectives**

| Objective | How it was met | Evidence |
|---|---|---|
| Provide role-based access for administrators, lecturers and students | Separate dashboards and pages for each role; role and ownership checks on every route | Figures 4.3, 4.10, 4.25, 4.28; tests in `test_auth.py` and `test_admin.py` |
| Record attendance with time-limited QR codes | Random 256-bit tokens stored as hashes; expiry, closing and regeneration | Figures 4.14–4.22; `test_attendance.py` |
| Prevent duplicate and fraudulent attendance | Enrolment check, expiry check and a unique constraint in the database | Figures 4.19–4.22; 22 attendance tests |
| Produce attendance registers and reports | Register with percentages, date filter and CSV export | Figures 4.23–4.24; `test_reports.py` (hand-calculated values) |
| Secure the system | Hashed passwords, CSRF protection, security headers, safe redirects, secret-key enforcement | Table 4.5; `test_config.py` |
| Usable on students' phones | Responsive interface checked at phone width | Figures 4.17–4.22, 4.26 |

## 4.10 Summary

This chapter presented the implementation of the Smart QR Attendance System: its development environment, architecture, database, QR attendance protocol and calculation rules, and screenshots of each module. All 98 automated tests passed. Mutation checking showed that the tests detect real defects, and an end-to-end walkthrough found further defects, which were corrected and covered by new regression tests.

---

# CHAPTER FIVE: SUMMARY, CONCLUSION AND RECOMMENDATIONS

## 5.1 Summary

This project designed and implemented a web-based attendance system for educational institutions that replaces manual roll calls and paper sign-in sheets with time-limited QR codes. A lecturer starts an attendance session and displays a QR code in class. Students scan it with their phones and confirm their attendance after signing in. The server checks every submission against the session's validity, the student's enrolment and any existing record before storing it. Lecturers and administrators can view attendance registers with percentages, filter them by date and export them as CSV files. Students can follow their own attendance and are warned when they fall below the required minimum.

The project was built in phases: environment setup, database models, authentication and role-based dashboards, and course and enrolment management. These were followed by an audit of the existing code, repairs, administrator user management, QR attendance sessions, reporting, automated testing, documentation and a redesigned interface. The system was built with Python, Flask, SQLAlchemy and SQLite, and is supported by 98 automated tests, all of which pass.

## 5.2 Conclusion

The project achieved its aim of producing a working, secure and usable QR-based attendance system. The main conclusions are:

1. **Short-lived random tokens make QR attendance practical and resistant to simple abuse.** Storing only a hash of each token, requiring an explicit confirmation step and checking every submission on the server prevent invalid, expired, duplicate and non-enrolled submissions.
2. **Rules enforced by the database are more reliable than application checks alone.** The unique (session, student) constraint blocks duplicate attendance even under simultaneous submissions that an application-level check could miss.
3. **Clear, documented calculation rules make reports defensible.** Excluding sessions held before a student enrolled, and sessions still in progress, gives percentages that are fair to students and that can be checked by hand.
4. **Testing found defects that would otherwise have reached users.** The audit, the automated tests and the end-to-end walkthrough each found defects that the other methods had not, including a security weakness in how sessions were cleared on logout.

## 5.3 Contributions of the Project

- A complete, working attendance system with administrator, lecturer and student roles.
- A documented QR attendance protocol that combines random tokens, token hashing, expiry, regeneration, confirmation and database-level duplicate prevention.
- Clearly defined, tested rules for calculating attendance percentages, including late enrolment and sessions in progress.
- A suite of 98 automated tests, together with documentation (`README.md`, `DESIGN.md`) that allows the system to be installed, run and evaluated by others.

## 5.4 Limitations

1. **Proxy attendance is reduced but not eliminated.** A student can still forward a photo of the QR code to someone outside the room while the code is valid. Short session durations and regenerating the code limit this but do not stop it.
2. **No limit on login attempts.** Repeated password guesses are not slowed down or blocked.
3. **Login response time.** The password check is skipped when an email address is unknown, so the time a login takes could in principle reveal which email addresses have accounts.
4. **No self-service password reset by email.** This would need a mail server; administrators reset passwords instead.
5. **Accounts cannot be deactivated** without changing the database structure.
6. **SQLite allows only one write at a time.** This is adequate for a department, but not for a whole university, and no formal load test was carried out.
7. **Testing used fictitious data.** The system has not yet been trialled with a real class. *[Remove this item if you carry out a classroom trial, and report its results in Section 4.8.]*

## 5.5 Recommendations and Future Work

1. **Rotating QR codes:** change the code automatically every 20–30 seconds, so that a photographed code expires before it can be shared.
2. **Location or network checks:** accept attendance only from the classroom's Wi-Fi network or within a short distance of the lecturer, if students consent to this.
3. **Login rate limiting:** for example with Flask-Limiter, and make the login check take the same time for unknown and known email addresses.
4. **Email notifications and password reset:** for low-attendance warnings and self-service password recovery.
5. **A production database:** move to PostgreSQL for institution-wide use. Because the system uses SQLAlchemy, this is mostly a configuration change.
6. **Database migrations:** adopt Flask-Migrate (Alembic) so that future changes to the database structure, such as account deactivation, can be applied safely.
7. **Integration with institutional systems:** import courses and students from the university's records system instead of entering them by hand.
8. **Classroom trial and user study:** measure sign-in times with real classes and collect feedback from lecturers and students.

## 5.6 Final Remarks

The Smart QR Attendance System shows that a solo undergraduate project can deliver a secure, tested and usable replacement for manual attendance using widely available tools. Its design choices, especially storing tokens only as hashes, enforcing rules in the database and defining clear calculation rules, give a sound foundation for the improvements recommended above.
