# Deploying Smart QR Attendance (GitHub + PythonAnywhere)

GitHub stores the code. PythonAnywhere runs it at a public HTTPS address,
`https://<your-username>.pythonanywhere.com`, and keeps the SQLite database
between restarts. Both have free plans.

> In the commands below, replace `<your-username>` with your **PythonAnywhere**
> username and `<github-username>` with your **GitHub** username.

---

## Part A: Put the code on GitHub (on your Windows laptop)

### A1. Install Git (once)
Download and install **Git for Windows** from <https://git-scm.com/download/win>.
The default options are fine. Then open a new PowerShell window and tell Git who
you are:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### A2. Create an empty repository on GitHub
On <https://github.com/new>:
- **Repository name:** `smart_qr_attendance`
- **Visibility:** **Private** is recommended for a final-year project. You can give
  your supervisor access under *Settings → Collaborators*.
- Do **not** add a README, `.gitignore` or licence. The project already has them.

### A3. Commit and push
In PowerShell, in the project folder:

```powershell
git init
git add .
git status
```

**Check the `git status` list before going further.** It must **not** contain
`instance/`, `deploy/`, `venv/`, `backups/` or any `.db` file. The `.gitignore`
file already excludes them. If any of them appear, stop and fix this first.

```powershell
git commit -m "Smart QR Attendance System"
git branch -M main
git remote add origin https://github.com/<github-username>/smart_qr_attendance.git
git push -u origin main
```

The first push opens a browser window so you can sign in to GitHub.

---

## Part B: Run it on PythonAnywhere

### B1. Create an account
Sign up for a free **Beginner** account at <https://www.pythonanywhere.com>.

### B2. Get the code
Open **Consoles → Bash** and run:

```bash
git clone https://github.com/<github-username>/smart_qr_attendance.git
```

For a **private** repository, Git asks for a username and password. Enter your
GitHub username, and for the password paste a **personal access token** (GitHub →
*Settings → Developer settings → Fine-grained tokens*, read-only access to this
repository). Your normal GitHub password will not work.

### B3. Install the dependencies
Still in the Bash console:

```bash
mkvirtualenv --python=python3.12 smartqr
pip install -r ~/smart_qr_attendance/requirements.txt
mkdir -p ~/smart_qr_attendance/instance
```

If Python 3.12 is not offered, use the newest version available that is 3.10 or
higher, and use the same version in step B5.

### B4. Upload the demo database
In the **Files** tab, open `/home/<your-username>/smart_qr_attendance/instance/`
and upload **`deploy/attendance.db`** from your laptop.

This is a copy of your demo data in which every account has a **new** password.
The password is recorded in `deploy/DEMO_CREDENTIALS.txt` on your laptop. Keep that
file private and never commit it.

*For an empty system instead:* skip the upload and run
`cd ~/smart_qr_attendance && python init_db.py` in the console, then create the
first administrator account as described in the README.

### B5. Create the web app
In the **Web** tab:
1. **Add a new web app → Manual configuration → Python 3.12** (the same version as B3).
2. **Virtualenv:** `/home/<your-username>/.virtualenvs/smartqr`
3. **Source code:** `/home/<your-username>/smart_qr_attendance`
4. **Static files:** URL `/static/` → Directory `/home/<your-username>/smart_qr_attendance/static`
5. **Security:** switch **Force HTTPS** on.

### B6. Configure the WSGI file
On the Web tab, click the **WSGI configuration file** link. Delete everything in
it and paste the following:

```python
import os
import sys

project = "/home/<your-username>/smart_qr_attendance"
if project not in sys.path:
    sys.path.insert(0, project)

# Secrets live here on the server, never in GitHub.
os.environ["SECRET_KEY"] = "<paste a long random key - see below>"
os.environ["SESSION_COOKIE_SECURE"] = "1"          # the site is served over HTTPS
os.environ["SQLITE_JOURNAL_MODE"] = "DELETE"       # WAL is unsafe on network storage
os.environ["PUBLIC_BASE_URL"] = "https://<your-username>.pythonanywhere.com"

from wsgi import application  # noqa: E402
```

To make the secret key, run this once in the Bash console and paste the output
between the quotes:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### B7. Start it
Click **Reload** on the Web tab, then open
`https://<your-username>.pythonanywhere.com`. Sign in as `admin@example.com`
with the password in `deploy/DEMO_CREDENTIALS.txt`.

**Quick check:** sign in as a lecturer and start a session, then scan the QR code
with your phone **on mobile data** (not your Wi-Fi). The phone should open the
attendance page on your PythonAnywhere address.

If something goes wrong, the **error log** link on the Web tab shows the cause.

---

## Part C: Day-to-day

| Task | How |
|---|---|
| **Update the site after changing the code** | Laptop: `git add .`, then `git commit -m "..."`, then `git push`. PythonAnywhere Bash: `cd ~/smart_qr_attendance && git pull`, then click **Reload**. |
| **Back up the data** | Files tab → `instance/attendance.db` → download. |
| **Keep the free site running** | Free web apps are disabled after three months unless you log in and click **"Run until 3 months from today"** on the Web tab. Do this before your defence. |
| **Reset a user's password** | Sign in as admin → Users → Reset password. |
| **Change the shared demo password** | Reset each account's password, or rebuild `deploy/attendance.db` on your laptop and upload it again. |

## Security checklist

- [ ] The repository is private (or contains nothing secret, which `.gitignore` ensures).
- [ ] `SECRET_KEY` is a new random value that appears only in the PythonAnywhere WSGI file.
- [ ] **Force HTTPS** is on and `SESSION_COOKIE_SECURE` is `1`.
- [ ] The demo password is shared only with your supervisor and examiners.
- [ ] Debug mode is off. It is off unless `FLASK_DEBUG=1`, which you should never set on the server.
