# QueueCare (Flask + SQLite)

Windows quick start: double-click `run.bat`. It installs the packages, starts the server
and opens your browser. Keep the black window open while you use the site.

Manual start: `pip install -r requirements.txt`, then `python app.py`. The terminal prints the
link to open (normally http://127.0.0.1:5000; it picks another port if 5000 is busy).

The `.env` file is included with defaults. Log in with admin@queuecare.com / admin123
(change them in `.env`). SMS is in console mode until you set SMS_PROVIDER (see below).

## Two separate websites
- `/` - **Patient website** (open to everyone). Starts with "Welcome to our QueueCare", then
  Get a token, My token and the **Live board** (big token numbers + live clock + expected time).
  There is no link to the admin panel anywhere on it.
- `/admin` - **Admin website**. A visitor sees only a login page. The admin panel page and its
  script (`/admin/admin.js`) are sent only after a correct login. A logged-in admin can also open the
  patient website (link at the top of the panel).
- Admin address is not shown to patients: give it only to staff (`http://127.0.0.1:5000/admin`).
- 5 wrong passwords = 5 minutes wait.

## New design (Medical Teal + Navy + White)
- Patient website `/` now has 5 pages (same address, switched by the menu): Home, Doctors, Live queue,
  My token, Get token. Admin panel has a navy sidebar, KPI cards and charts built from real queue data.
- Only the frontend files changed (`static/*`). One small backend addition: `/api/public/doctors` now also
  returns each doctor's average consultation time (`avg`) for the doctor cards and queue list.
- Access control is unchanged: patients are anonymous and only see `/api/public/...`; every admin API and
  `/admin/admin.js` need the admin login (checked on the server, not just hidden in the page).

## Where things live
- `app.py`: the whole backend (database, queue logic, wait time, SMS + email, login, API).
- `static/user.html`, `user.js`: patient website. `static/admin-login.html`, `admin-login.js`: admin login.
- `static/admin.html`, `admin.js`: admin panel (private). `static/style.css`: look of everything.
- `queuecare.db`: SQLite database, created automatically. Delete it to start fresh.

## API routes
- Admin (login needed): `POST /api/login`, `/api/logout`, `GET /api/me`, `GET /api/state`,
  `POST /api/tokens`, `/api/doctors`, `/api/doctors/<id>/next`, `/skip`, `/api/tokens/<id>/cancel`, `/priority`
- Patient (no login): `/api/public/doctors`, `/api/public/tokens`, `/api/public/token`, `/api/public/tokens/cancel`

## Feature logic (all in app.py)
- Priority queue: `ORDER BY emergency DESC, id ASC` in `get_waiting_list()`
- Predictive wait time: (people ahead, including the one being served) x doctor's average
  consultation time. The average moves 30% toward each finished consultation.
  `DEMO_SIMULATE=1` uses fake consultation times; set `DEMO_SIMULATE=0` to measure real time.
- Virtual queue alerts: `notify_patients()` runs after every change and sends an SMS (and an email,
  if the patient gave one) to each waiting patient who has 2 or fewer people ahead (once per token).

## Two sides: Patient portal and Admin panel
- `/` - landing page with two buttons
- `/user` - **Patient portal** (no login). A patient can take a token, see the live doctor queues,
  track their own token (token number + phone) and cancel it while waiting. A patient can never see
  other patients, phone numbers, the notification log or Settings. Online tokens are always normal
  priority.
- `/admin` - **Admin panel** (login from `.env`). Full control: add doctors, call next / skip, add
  reception patients, change a token between Normal and Emergency, cancel any token, and see every
  online booking (the "Online" tag, the "Latest online bookings" box on the Dashboard).
- Public API lives under `/api/public/...`. Admin API needs the admin login.

## Email notifications (free)
Emails: booking confirmation (sent immediately - the patient sees the real result, or the real
error reason), "your turn is near", and "it is your turn now". If a patient says no mail came:
check the Spam folder, and look at the Notification log in the admin Settings page for the reason.
Patients can give an email when they take a token. They get the same "your turn is near"
alert by email as well as by SMS. Fill SMTP_USER and SMTP_PASSWORD in `.env`
(for Gmail: turn on 2-Step Verification, create an App Password at
myaccount.google.com/apppasswords, paste the 16 letters). Restart the server. Emails can land in Spam the first time.

## Getting a real SMS on your phone
Set `SMS_PROVIDER` in `.env`. `console` only prints in the terminal.
- android (free software, uses your own phone and SIM): install the open-source
  "SMS Gateway for Android" app, turn on Local Server, then tap the status button
  until it says Online. Put the URL (http://PHONE_IP:8080/message), username and
  password it shows into `.env`, set `SMS_PROVIDER=android`, restart the server.
  Phone and PC must be on the same WiFi. Normal SMS charges of your SIM apply.
- bulksmsbd: account with credit, approved Sender ID, IP whitelisting turned off.
- twilio: trial accounts only send to verified numbers; Bangladesh must be enabled in Geo permissions.
After changing `.env`, restart the server. The notification log on the page shows the gateway reply,
so you can see why a send failed.
