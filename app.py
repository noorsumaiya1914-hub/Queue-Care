# ======================================================================
#  QueueCare - Smart Hospital Queue System
#  Backend: Python + Flask + SQLite
#
#  Includes:
#    - Login and SQLite database
#    - Doctor, patient token and emergency-priority queues
#    - Predictive wait time and live board API
#    - SMS: console, Android SMS Gateway, Textbee, BulkSMSBD, Twilio
#    - Email notifications
#    - Notification log
#    - Patient portal (public, read-only for others' data) + Admin panel
# ======================================================================

import hmac
import os
import re
import time
import random
import smtplib
import socket
import sqlite3
import threading
import webbrowser
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape as html_escape

import requests
from dotenv import load_dotenv
from flask import (
    Flask, jsonify, redirect, request, session, send_from_directory, g
)


# ======================================================================
# PART 1: SETTINGS
# ======================================================================
load_dotenv()

BASE_FOLDER = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(BASE_FOLDER, "queuecare.db")

# static_folder=None: files are NOT served automatically. Every page and
# script is served by an explicit route below, so the admin files can be
# kept private (only a logged-in admin can download them).
app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@queuecare.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Supported: console, android, textbee, bulksmsbd, twilio
# Keep console as the safe default. Set SMS_PROVIDER=textbee in .env to use Textbee.
SMS_MODE = os.getenv("SMS_PROVIDER", "console").strip().lower()

# Textbee settings
TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY", "").strip()
TEXTBEE_BASE_URL = os.getenv(
    "TEXTBEE_BASE_URL", "https://api.textbee.dev/api/v1"
).rstrip("/")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "").strip()
TEXTBEE_STATUS_WAIT = int(os.getenv("TEXTBEE_STATUS_WAIT", "180"))

# Demo mode: 1 = simulate consultation duration, 0 = measure real duration
DEMO_MODE = os.getenv("DEMO_SIMULATE", "1") == "1"

NOTIFY_WHEN_AHEAD = 2

# Email settings
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USER

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# Android SMS Gateway settings
ANDROID_READ_TIMEOUT = float(os.getenv("ANDROID_GW_TIMEOUT", "90"))
ANDROID_STATUS_WAIT = float(os.getenv("ANDROID_STATUS_WAIT", "180"))

sms_send_times = []


# ======================================================================
# PART 2: DATABASE
# ======================================================================
def get_db():
    """Open the database once per request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_FILE, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_tables():
    """Create tables without deleting any existing QueueCare data."""
    db = sqlite3.connect(DATABASE_FILE)

    db.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL,
            dept  TEXT NOT NULL,
            avg   REAL NOT NULL DEFAULT 8
        )""")

    db.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            phone       TEXT NOT NULL,
            email       TEXT NOT NULL DEFAULT '',
            doctor_id   INTEGER NOT NULL,
            emergency   INTEGER NOT NULL DEFAULT 0,
            status      TEXT NOT NULL DEFAULT 'w',
            sms_sent    INTEGER NOT NULL DEFAULT 0,
            created     REAL NOT NULL,
            called_at   REAL
        )""")

    db.execute("""
        CREATE TABLE IF NOT EXISTS sms_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            channel   TEXT NOT NULL DEFAULT 'SMS',
            text      TEXT NOT NULL,
            status    TEXT NOT NULL,
            created   REAL NOT NULL
        )""")

    # Migrate older databases safely by adding missing columns only.
    token_columns = [
        row[1] for row in db.execute("PRAGMA table_info(tokens)")
    ]
    if "email" not in token_columns:
        db.execute(
            "ALTER TABLE tokens ADD COLUMN email TEXT NOT NULL DEFAULT ''"
        )

    if "source" not in token_columns:
        db.execute(
            "ALTER TABLE tokens ADD COLUMN source TEXT NOT NULL DEFAULT 'reception'"
        )

    log_columns = [
        row[1] for row in db.execute("PRAGMA table_info(sms_log)")
    ]
    if "channel" not in log_columns:
        db.execute(
            "ALTER TABLE sms_log ADD COLUMN channel TEXT NOT NULL DEFAULT 'SMS'"
        )

    # Add demo doctors only if the doctors table is empty.
    doctor_count = db.execute(
        "SELECT COUNT(*) FROM doctors"
    ).fetchone()[0]
    if doctor_count == 0:
        db.execute(
            "INSERT INTO doctors (name, dept, avg) VALUES (?, ?, ?)",
            ("Dr. Rahman", "Medicine", 8)
        )
        db.execute(
            "INSERT INTO doctors (name, dept, avg) VALUES (?, ?, ?)",
            ("Dr. Sultana", "Cardiology", 12)
        )

    db.commit()
    db.close()


# ======================================================================
# PART 3: SMS AND EMAIL
# ======================================================================
def fix_phone_number(text):
    """Convert Bangladeshi 01XXXXXXXXX to 8801XXXXXXXXX."""
    digits = re.sub(r"\D", "", text)
    if digits.startswith("8801") and len(digits) == 13:
        return digits
    if digits.startswith("01") and len(digits) == 11:
        return "88" + digits
    return None


def sms_console(number, message):
    """Test mode: print SMS in the terminal; do not send a real SMS."""
    print(f"[SMS console mode] to {number}: {message}")
    return True, "Console mode only. No real SMS was sent. Set SMS_PROVIDER in .env"


# -------------------- Android SMS Gateway --------------------
def sms_android(number, message, progress=None):
    """Send through the SMS Gateway for Android app."""
    url = os.environ["ANDROID_GW_URL"]
    login = (os.environ["ANDROID_GW_USER"], os.environ["ANDROID_GW_PASS"])
    body = {
        "textMessage": {"text": message},
        "phoneNumbers": ["+" + number]
    }
    wait_seconds = (5, ANDROID_READ_TIMEOUT)

    try:
        reply = requests.post(
            url, json=body, auth=login, timeout=wait_seconds
        )
        if reply.status_code == 400:
            body = {"message": message, "phoneNumbers": ["+" + number]}
            reply = requests.post(
                url, json=body, auth=login, timeout=wait_seconds
            )
    except requests.exceptions.ReadTimeout:
        return None, (
            "The request reached the phone app but it did not confirm in time. "
            "Open the app's Messages tab to check the result."
        )

    if reply.status_code not in (200, 201, 202):
        return False, (
            f"Phone gateway replied {reply.status_code}: {reply.text[:150]}"
        )

    try:
        message_id = reply.json().get("id")
    except ValueError:
        message_id = None

    if not message_id:
        return None, (
            "The phone app accepted the message, but its final status is unknown."
        )

    return wait_for_android_status(url, login, message_id, progress)


def wait_for_android_status(url, login, message_id, progress=None):
    """Poll the Android gateway until the message is sent, failed or times out."""
    base = url.rstrip("/")
    status_urls = [f"{base}/{message_id}", f"{base}s/{message_id}"]
    stop_time = time.time() + ANDROID_STATUS_WAIT
    last_shown = None

    while time.time() < stop_time:
        info = None

        for status_url in status_urls:
            try:
                answer = requests.get(
                    status_url, auth=login, timeout=(5, 15)
                )
                if answer.status_code == 200:
                    info = answer.json()
                    break
            except (requests.exceptions.RequestException, ValueError):
                continue

        if info is None:
            return None, (
                "The phone app accepted the message, but its final status "
                "could not be checked. Open the app's Messages tab."
            )

        state = info.get("state")

        if state in ("Sent", "Delivered"):
            return True, f"Phone app says: {state}."

        if state == "Failed":
            reason = info.get("reason") or ""
            for recipient in info.get("recipients") or []:
                if isinstance(recipient, dict):
                    reason = recipient.get("error") or reason
            return False, f"Phone app says: Failed. {reason}".strip()

        if progress and state != last_shown:
            progress(f"Sending... Phone app says: {state or 'Pending'}.")
            last_shown = state

        time.sleep(3)

    return None, (
        "The phone app accepted the message but did not finish in time. "
        "Open the app's Messages tab."
    )


# -------------------- Textbee SMS Gateway --------------------
def sms_textbee(number, message, progress=None):
    """
    Send SMS using Textbee and check its batch status.

    The phone running Textbee must be online, the Textbee app must be enabled,
    and SMS permission must be granted on that phone.
    """
    if not TEXTBEE_API_KEY:
        return False, "TEXTBEE_API_KEY is missing from the .env file."

    headers = {
        "x-api-key": TEXTBEE_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Find an enabled Textbee device.
    try:
        devices_reply = requests.get(
            f"{TEXTBEE_BASE_URL}/gateway/devices",
            headers=headers,
            timeout=20,
        )
    except requests.exceptions.RequestException as error:
        return False, f"Could not contact Textbee: {str(error)[:160]}"

    if not devices_reply.ok:
        return False, (
            f"Textbee device lookup failed ({devices_reply.status_code}): "
            f"{devices_reply.text[:180]}"
        )

    try:
        devices_payload = devices_reply.json()
    except ValueError:
        return False, "Textbee returned an invalid device-list response."

    device_list = devices_payload.get("data", [])
    if isinstance(device_list, dict):
        device_list = device_list.get("devices", [])

    if not isinstance(device_list, list):
        return False, "Textbee returned an unexpected device-list format."

    enabled_devices = [
        device for device in device_list
        if isinstance(device, dict) and device.get("enabled") is True
    ]

    if TEXTBEE_DEVICE_ID:
        selected_device = next(
            (
                device for device in enabled_devices
                if str(device.get("_id") or device.get("id") or "")
                == TEXTBEE_DEVICE_ID
            ),
            None,
        )
        if selected_device is None:
            return False, (
                "The TEXTBEE_DEVICE_ID was not found among enabled Textbee "
                "devices. Check the device ID and enable the phone in Textbee."
            )
    else:
        selected_device = next(
            (device for device in enabled_devices if device.get("isDefault")),
            None,
        )
        if selected_device is None and enabled_devices:
            selected_device = enabled_devices[0]

    if selected_device is None:
        return False, (
            "No enabled Textbee device was found. Open the Textbee Android app, "
            "enable the device, and check its internet connection."
        )

    device_id = (
        selected_device.get("_id")
        or selected_device.get("id")
        or selected_device.get("deviceId")
    )

    if not device_id:
        return False, "Textbee device response did not include a device ID."

    if progress:
        progress("Sending... Connected to Textbee device.")

    # Ask Textbee to send the SMS.
    payload = {
        "message": message,
        "recipients": ["+" + number],
        "deviceId": device_id,
    }

    try:
        send_reply = requests.post(
            f"{TEXTBEE_BASE_URL}/gateway/send-sms",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.exceptions.RequestException as error:
        return None, (
            "Textbee send request did not return a confirmed response: "
            f"{str(error)[:150]}"
        )

    if not send_reply.ok:
        return False, (
            f"Textbee rejected the SMS ({send_reply.status_code}): "
            f"{send_reply.text[:180]}"
        )

    try:
        send_data = send_reply.json()
    except ValueError:
        return None, (
            "Textbee accepted the request but returned an unreadable response; "
            "final SMS status is unknown."
        )

    result_data = send_data.get("data", send_data)
    if not isinstance(result_data, dict):
        return None, "Textbee accepted the request, but its result format was unexpected."

    batch_id = result_data.get("smsBatchId")

    # Some immediate responses may contain counts but no batch ID.
    if not batch_id:
        failure_count = int(result_data.get("failureCount") or 0)
        success_count = int(result_data.get("successCount") or 0)

        if failure_count > 0 and success_count == 0:
            return False, "Textbee reported that the SMS could not be sent."

        return None, (
            "Textbee accepted the SMS request, but did not provide a batch ID "
            "for final status checking."
        )

    # Poll the batch status. 'sent' means the phone handed the SMS to the
    # carrier; 'delivered' is a delivery receipt when the carrier provides one.
    stop_time = time.time() + TEXTBEE_STATUS_WAIT
    last_status = None

    while time.time() < stop_time:
        status_url = (
            f"{TEXTBEE_BASE_URL}/gateway/devices/{device_id}"
            f"/sms-batch/{batch_id}"
        )

        try:
            status_reply = requests.get(
                status_url, headers=headers, timeout=20
            )
        except requests.exceptions.RequestException as error:
            if progress and last_status != "status-check-error":
                progress("Sending... Waiting for Textbee status.")
                last_status = "status-check-error"
            time.sleep(3)
            continue

        if status_reply.status_code == 404:
            return None, (
                "Textbee accepted the SMS, but its batch status was not found."
            )

        if not status_reply.ok:
            if progress and last_status != "status-check-error":
                progress("Sending... Waiting for Textbee status.")
                last_status = "status-check-error"
            time.sleep(3)
            continue

        try:
            status_payload = status_reply.json()
        except ValueError:
            time.sleep(3)
            continue

        status_data = status_payload.get("data", status_payload)
        if isinstance(status_data, dict):
            batch = status_data.get("batch", status_data)
        else:
            batch = {}

        if not isinstance(batch, dict):
            batch = {}

        state = str(batch.get("status") or "unknown").lower()
        success_count = int(batch.get("successCount") or 0)
        failure_count = int(batch.get("failureCount") or 0)

        if progress and state != last_status:
            progress(
                f"Sending... Textbee status: {state} "
                f"(success: {success_count}, failed: {failure_count})."
            )
            last_status = state

        if state in ("completed", "partial_success"):
            if success_count > 0:
                return True, (
                    f"Textbee batch status: {state}. "
                    f"Successful: {success_count}, failed: {failure_count}. "
                    "This confirms the phone sent the SMS; carrier delivery "
                    "confirmation may be separate."
                )
            return False, (
                f"Textbee batch finished with no successful SMS. "
                f"Failed: {failure_count}."
            )

        if state == "failed":
            return False, (
                f"Textbee batch failed. Successful: {success_count}, "
                f"failed: {failure_count}."
            )

        if state in ("sent", "delivered"):
            return True, f"Textbee reports SMS status: {state}."

        time.sleep(3)

    return None, (
        "Textbee accepted the SMS, but its batch did not reach a final status "
        f"within {TEXTBEE_STATUS_WAIT} seconds. Check the Textbee dashboard."
    )


# -------------------- Other SMS providers --------------------
def sms_bulksmsbd(number, message):
    """Send through BulkSMSBD (requires credit and an approved Sender ID)."""
    reply = requests.get(
        "https://bulksmsbd.net/api/smsapi",
        params={
            "api_key": os.environ["BULKSMSBD_API_KEY"],
            "senderid": os.environ["BULKSMSBD_SENDER_ID"],
            "number": number,
            "message": message,
            "type": "text",
        },
        timeout=15,
    )
    try:
        answer = reply.json()
    except ValueError:
        return False, reply.text[:200]

    ok = str(answer.get("response_code")) == "202"
    return (
        ok,
        answer.get("success_message")
        or answer.get("error_message")
        or str(answer),
    )


def sms_twilio(number, message):
    """Send through Twilio (trial accounts only send to verified numbers)."""
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]

    reply = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data={
            "From": os.environ["TWILIO_FROM"],
            "To": "+" + number,
            "Body": message,
        },
        auth=(account_sid, auth_token),
        timeout=15,
    )

    try:
        answer = reply.json()
    except ValueError:
        return False, reply.text[:200]

    ok = reply.status_code in (200, 201)
    return ok, answer.get("message") or answer.get("status", "")


SMS_SERVICES = {
    "console": sms_console,
    "android": sms_android,
    "textbee": sms_textbee,
    "bulksmsbd": sms_bulksmsbd,
    "twilio": sms_twilio,
}


# SMS_FALLBACK=android  -> if the main route fails or is not confirmed, try the next one.
# Leave it empty for automatic (textbee <-> android when both are set up), or none to turn it off.
SMS_FALLBACK = os.getenv("SMS_FALLBACK", "").strip().lower()


def sms_providers():
    """Main SMS route first, then the backup routes."""
    chain = [SMS_MODE]
    if SMS_FALLBACK == "none":
        return chain
    extra = [p.strip() for p in SMS_FALLBACK.split(",") if p.strip()]
    if not extra:
        if SMS_MODE == "textbee" and os.getenv("ANDROID_GW_URL") and os.getenv("ANDROID_GW_USER"):
            extra = ["android"]
        elif SMS_MODE == "android" and TEXTBEE_API_KEY:
            extra = ["textbee"]
    return chain + [p for p in extra if p in SMS_SERVICES and p not in chain]


def send_sms(number, message, progress=None):
    """Send SMS with the provider from .env; if it fails, try the backup route."""
    now = time.time()
    sms_send_times[:] = [t for t in sms_send_times if now - t < 60]
    if len(sms_send_times) >= 10:
        return False, "Rate limit: max 10 SMS per minute"
    sms_send_times.append(now)

    notes, ok, detail = [], False, ""
    for provider in sms_providers():
        send_function = SMS_SERVICES.get(provider, sms_console)
        try:
            if provider in ("android", "textbee"):
                ok, detail = send_function(number, message, progress)
            else:
                ok, detail = send_function(number, message)
        except Exception as error:
            ok, detail = False, str(error)[:200]

        if ok:
            return True, " | ".join(notes + [f"[{provider}] {detail}"])
        notes.append(f"[{provider}] {detail[:160]}")
        if progress:
            progress(f"{provider} did not confirm. Trying the next SMS route...")
    return ok, " | ".join(notes)


def sms_check():
    """Look at every SMS route and say in plain words whether it is ready."""
    results = []
    for provider in sms_providers():
        if provider == "console":
            results.append((provider, False, "Console mode: nothing is sent to a real phone."))
        elif provider == "android":
            url = os.getenv("ANDROID_GW_URL", "")
            try:
                reply = requests.get(
                    url, timeout=(4, 6),
                    auth=(os.getenv("ANDROID_GW_USER", ""), os.getenv("ANDROID_GW_PASS", "")),
                )
                results.append((provider, True, f"Phone gateway answered at {url} (status {reply.status_code})."))
            except Exception as error:
                results.append((provider, False,
                    f"Cannot reach the phone at {url}. Phone and PC must be on the SAME WiFi, the app's "
                    f"Local Server must say Online, and the phone IP may have changed (copy the new URL "
                    f"from the app into ANDROID_GW_URL). {str(error)[:80]}"))
        elif provider == "textbee":
            try:
                reply = requests.get(f"{TEXTBEE_BASE_URL}/gateway/devices",
                                     headers={"x-api-key": TEXTBEE_API_KEY}, timeout=15)
                if not reply.ok:
                    results.append((provider, False, f"Textbee refused the API key ({reply.status_code}). Make a new key in the Textbee dashboard."))
                    continue
                devices = reply.json().get("data", [])
                if isinstance(devices, dict):
                    devices = devices.get("devices", [])
                on = [d for d in devices if isinstance(d, dict) and d.get("enabled")]
                if not on:
                    results.append((provider, False, "No enabled phone in Textbee. Open the Textbee app and enable the device."))
                else:
                    seen = next((str(v) for d in on for k, v in d.items()
                                 if "heartbeat" in k.lower() or "seen" in k.lower()), "")
                    results.append((provider, True,
                        f"Textbee sees {len(on)} enabled phone(s)" + (f" (last signal: {seen})" if seen else "") +
                        ". If SMS stays at 'processing', the phone app is asleep: open Textbee, allow SMS "
                        "permission, set battery to Unrestricted, keep it open and connected to the internet."))
            except Exception as error:
                results.append((provider, False, "Could not contact Textbee: " + str(error)[:100]))
        else:
            results.append((provider, True, "Configured in .env."))
    return results


def clock_text(timestamp):
    """12-hour clock text such as 10:25 PM (server computer's local time)."""
    return time.strftime("%I:%M %p", time.localtime(timestamp)).lstrip("0")


def email_html(heading, rows, note):
    """A clean HTML version of a notification email."""
    row_html = "".join(
        "<tr><td style='padding:6px 14px 6px 0;color:#5d7080'>"
        f"{html_escape(label)}</td>"
        f"<td style='padding:6px 0;font-weight:700'>{html_escape(value)}</td></tr>"
        for label, value in rows
    )
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:480px;"
        "margin:auto;border:1px solid #d5dfe6;border-radius:10px;overflow:hidden'>"
        "<div style='background:#0b7285;color:#fff;padding:14px 18px;"
        "font-size:20px;font-weight:700'>QueueCare</div>"
        "<div style='padding:18px;color:#12222d'>"
        f"<h2 style='margin:0 0 12px;font-size:18px'>{html_escape(heading)}</h2>"
        f"<table style='border-collapse:collapse'>{row_html}</table>"
        f"<p style='margin:16px 0 0;color:#5d7080'>{html_escape(note)}</p>"
        "</div></div>"
    )


def friendly_email_error(error):
    """Turn a raw SMTP error into a sentence a person can act on."""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return (
            "The email server refused the login. For Gmail use a 16-letter "
            "App Password (not your normal password) in SMTP_PASSWORD."
        )
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The email server rejected the patient's email address."
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "The email server took too long to answer (check the internet)."
    if isinstance(error, (socket.gaierror, ConnectionError, OSError)):
        return "Could not reach the email server (check the internet)."
    return str(error)[:200]


def send_email(to_address, subject, text, html=None):
    """Send one email using the configured SMTP account.

    Tries twice (a single network hiccup should not lose the email) and
    returns (True/False, plain-English detail).
    """
    if not SMTP_USER:
        return False, (
            "Email is not set up. Fill SMTP_USER and SMTP_PASSWORD in .env"
        )

    sender_domain = SMTP_FROM.split("@")[-1] if "@" in SMTP_FROM else None

    mail = EmailMessage()
    mail["From"] = formataddr(("QueueCare", SMTP_FROM))
    mail["To"] = to_address
    mail["Reply-To"] = SMTP_FROM
    mail["Subject"] = subject
    mail["Date"] = formatdate(localtime=True)
    mail["Message-ID"] = make_msgid(domain=sender_domain)
    mail.set_content(text)
    if html:
        mail.add_alternative(html, subtype="html")

    last_problem = "Unknown email error"
    for attempt in (1, 2):
        try:
            if SMTP_SECURITY == "ssl":
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)

            with server:
                server.ehlo()
                if SMTP_SECURITY == "starttls":
                    server.starttls()
                    server.ehlo()
                if SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(mail)

            return True, "Email sent to " + to_address
        except (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPRecipientsRefused,
        ) as error:
            # Retrying cannot fix a wrong password or a bad address.
            return False, friendly_email_error(error)
        except Exception as error:
            last_problem = friendly_email_error(error)
            if attempt == 1:
                time.sleep(1.5)

    return False, last_problem


def status_label(ok):
    """True = sent, False = failed, None = not confirmed."""
    if ok is None:
        return "Unconfirmed."
    return "Sent." if ok else "Failed."


def save_log(channel, text, ok, detail):
    """Save a notification result to the database."""
    result = status_label(ok) + " " + detail
    get_db().execute(
        """
        INSERT INTO sms_log (channel, text, status, created)
        VALUES (?, ?, ?, ?)
        """,
        (channel, text, result, time.time()),
    )


# ======================================================================
# PART 4: QUEUE LOGIC
# ======================================================================
def make_label(token):
    """Token label: T-5 for normal, E-5 for emergency."""
    prefix = "E-" if token["emergency"] else "T-"
    return prefix + str(token["id"])


def get_waiting_list(doctor_id):
    """Emergency patients first, then earliest token first."""
    return get_db().execute(
        """
        SELECT * FROM tokens
        WHERE doctor_id = ? AND status = 'w'
        ORDER BY emergency DESC, id ASC
        """,
        (doctor_id,),
    ).fetchall()


def get_patient_being_served(doctor_id):
    return get_db().execute(
        "SELECT * FROM tokens WHERE doctor_id = ? AND status = 's'",
        (doctor_id,),
    ).fetchone()


def predict_wait(people_ahead, average_minutes):
    return round(people_ahead * average_minutes)


def send_in_background(jobs):
    """Send each notification in a background thread and update its log."""
    def work(log_id, kind, target, subject, message, html=None):
        db = sqlite3.connect(DATABASE_FILE, timeout=10)

        def update_log(status):
            db.execute(
                "UPDATE sms_log SET status = ? WHERE id = ?",
                (status, log_id),
            )
            db.commit()

        try:
            if kind == "sms":
                ok, detail = send_sms(
                    target, message, progress=update_log
                )
            else:
                ok, detail = send_email(
                    target, subject, message, html
                )

            update_log(status_label(ok) + " " + detail)
        except Exception as error:
            update_log("Failed. " + str(error)[:200])
        finally:
            db.close()

    for job in jobs:
        threading.Thread(
            target=work, args=job, daemon=True
        ).start()


def notify_patients():
    """Notify waiting patients when two or fewer people are ahead."""
    db = get_db()
    jobs = []

    for doctor in db.execute("SELECT * FROM doctors").fetchall():
        busy = 1 if get_patient_being_served(doctor["id"]) else 0

        for position, patient in enumerate(
            get_waiting_list(doctor["id"])
        ):
            people_ahead = position + busy
            already_told = patient["sms_sent"] == 1

            if people_ahead > NOTIFY_WHEN_AHEAD or already_told:
                continue

            label = make_label(patient)

            sms_text = (
                f"Hi {patient['name']}, your token {label} is coming up soon "
                f"({people_ahead} ahead)."
            )

            cursor = db.execute(
                """
                INSERT INTO sms_log (channel, text, status, created)
                VALUES ('SMS', ?, 'Sending...', ?)
                """,
                (sms_text, time.time()),
            )
            jobs.append(
                (cursor.lastrowid, "sms", patient["phone"], "", sms_text)
            )

            if patient["email"]:
                wait = predict_wait(people_ahead, doctor["avg"])
                expected = clock_text(time.time() + wait * 60)
                subject = f"Your token {label} is coming up soon"
                email_text = (
                    f"Hi {patient['name']},\n\n"
                    f"Your token {label} with {doctor['name']} "
                    f"({doctor['dept']}) is coming up soon.\n"
                    f"People ahead of you: {people_ahead}\n"
                    f"Estimated wait: about {wait} minutes "
                    f"(around {expected}).\n\n"
                    "Please stay near the doctor's room."
                )
                email_page = email_html(
                    "Your turn is coming up soon",
                    [
                        ("Token", label),
                        ("Doctor", f"{doctor['name']} ({doctor['dept']})"),
                        ("People ahead", str(people_ahead)),
                        ("Estimated time", f"about {wait} min (around {expected})"),
                    ],
                    "Please stay near the doctor's room.",
                )

                cursor = db.execute(
                    """
                    INSERT INTO sms_log (channel, text, status, created)
                    VALUES ('Email', ?, 'Sending...', ?)
                    """,
                    (f"To {patient['email']}: {subject}", time.time()),
                )
                jobs.append(
                    (
                        cursor.lastrowid,
                        "email",
                        patient["email"],
                        subject,
                        email_text,
                        email_page,
                    )
                )

            # Prevent duplicate notifications for this token.
            db.execute(
                "UPDATE tokens SET sms_sent = 1 WHERE id = ?",
                (patient["id"],),
            )

    db.commit()

    if jobs:
        send_in_background(jobs)


def send_booking_confirmation(token_id):
    """Send an SMS (and an email, if given) as soon as a patient books a token.

    If the patient is already close to the front, this message doubles as the
    "your turn is near" alert, so the token is marked as notified and the
    normal alert in notify_patients() will not send a second copy.
    """
    db = get_db()
    token = db.execute(
        "SELECT * FROM tokens WHERE id = ?", (token_id,)
    ).fetchone()
    if token is None:
        return

    doctor = db.execute(
        "SELECT * FROM doctors WHERE id = ?", (token["doctor_id"],)
    ).fetchone()
    if doctor is None:
        return

    busy = 1 if get_patient_being_served(doctor["id"]) else 0
    people_ahead = 0
    for position, patient in enumerate(get_waiting_list(doctor["id"])):
        if patient["id"] == token["id"]:
            people_ahead = position + busy
            break

    label = make_label(token)
    wait = predict_wait(people_ahead, doctor["avg"])
    near = people_ahead <= NOTIFY_WHEN_AHEAD

    sms_text = (
        f"Hi {token['name']}, your QueueCare token {label} is confirmed "
        f"with {doctor['name']} ({doctor['dept']}). "
        f"People ahead: {people_ahead}. Estimated wait: about {wait} min."
    )
    if near:
        sms_text += " Your turn is coming up soon, please stay near the doctor's room."

    jobs = []
    cursor = db.execute(
        """
        INSERT INTO sms_log (channel, text, status, created)
        VALUES ('SMS', ?, 'Sending...', ?)
        """,
        (sms_text, time.time()),
    )
    jobs.append((cursor.lastrowid, "sms", token["phone"], "", sms_text))

    email_job = None
    if token["email"]:
        expected = clock_text(time.time() + wait * 60)
        subject = f"Your token {label} is confirmed"
        email_text = (
            f"Hi {token['name']},\n\n"
            f"Your token {label} is confirmed.\n"
            f"Doctor: {doctor['name']} ({doctor['dept']})\n"
            f"People ahead of you: {people_ahead}\n"
            f"Estimated wait: about {wait} minutes (around {expected}).\n\n"
        )
        if near:
            email_text += "Your turn is coming up soon. Please stay near the doctor's room.\n\n"
        email_text += "You can track your token live on the QueueCare patient portal."
        email_page = email_html(
            "Your token is confirmed",
            [
                ("Token", label),
                ("Doctor", f"{doctor['name']} ({doctor['dept']})"),
                ("People ahead", str(people_ahead)),
                ("Estimated time", f"about {wait} min (around {expected})"),
            ],
            "Your turn is coming up soon. Please stay near the doctor's room."
            if near else
            "Track your token live on the QueueCare patient portal.",
        )

        cursor = db.execute(
            """
            INSERT INTO sms_log (channel, text, status, created)
            VALUES ('Email', ?, 'Sending...', ?)
            """,
            (f"To {token['email']}: {subject}", time.time()),
        )
        email_job = (cursor.lastrowid, token["email"], subject, email_text, email_page)

    if near:
        db.execute("UPDATE tokens SET sms_sent = 1 WHERE id = ?", (token["id"],))

    db.commit()
    send_in_background(jobs)

    # The confirmation email is sent right now (not in the background) so the
    # patient is told the real result instead of a hopeful "on the way".
    if email_job is None:
        return None

    log_id, address, subject, email_text, email_page = email_job
    ok, detail = send_email(address, subject, email_text, email_page)
    db.execute(
        "UPDATE sms_log SET status = ? WHERE id = ?",
        (status_label(ok) + " " + detail, log_id),
    )
    db.commit()
    return {"ok": ok, "detail": detail, "to": address}


def notify_turn(token, doctor):
    """Tell a patient it is their turn now (SMS, plus email if given)."""
    db = get_db()
    label = make_label(token)
    jobs = []

    sms_text = (
        f"Hi {token['name']}, it is your turn now. Token {label}, please go to "
        f"{doctor['name']} ({doctor['dept']})."
    )
    cursor = db.execute(
        "INSERT INTO sms_log (channel, text, status, created) "
        "VALUES ('SMS', ?, 'Sending...', ?)",
        (sms_text, time.time()),
    )
    jobs.append((cursor.lastrowid, "sms", token["phone"], "", sms_text))

    if token["email"]:
        subject = f"It is your turn now - token {label}"
        email_text = (
            f"Hi {token['name']},\n\nIt is your turn now.\n"
            f"Token: {label}\nDoctor: {doctor['name']} ({doctor['dept']})\n\n"
            "Please go to the doctor's room."
        )
        email_page = email_html(
            "It is your turn now",
            [("Token", label), ("Doctor", f"{doctor['name']} ({doctor['dept']})")],
            "Please go to the doctor's room.",
        )
        cursor = db.execute(
            "INSERT INTO sms_log (channel, text, status, created) "
            "VALUES ('Email', ?, 'Sending...', ?)",
            (f"To {token['email']}: {subject}", time.time()),
        )
        jobs.append(
            (cursor.lastrowid, "email", token["email"], subject, email_text, email_page)
        )

    db.commit()
    send_in_background(jobs)


def token_to_dict(token, doctor, people_ahead):
    return {
        "no": token["id"],
        "label": make_label(token),
        "name": token["name"],
        "emergency": bool(token["emergency"]),
        "status": token["status"],
        "source": token["source"],
        "phone": token["phone"],
        "doctor_id": doctor["id"],
        "doctor_name": doctor["name"],
        "dept": doctor["dept"],
        "ahead": people_ahead,
        "wait": predict_wait(people_ahead, doctor["avg"]),
    }


def call_next_patient(doctor_id, skip_current):
    db = get_db()

    doctor = db.execute(
        "SELECT * FROM doctors WHERE id = ?",
        (doctor_id,),
    ).fetchone()

    if doctor is None:
        return jsonify(error="Doctor not found"), 404

    current = get_patient_being_served(doctor_id)

    if current is not None:
        if skip_current:
            db.execute(
                "UPDATE tokens SET status = 'k' WHERE id = ?",
                (current["id"],),
            )
        else:
            db.execute(
                "UPDATE tokens SET status = 'd' WHERE id = ?",
                (current["id"],),
            )

            if DEMO_MODE:
                minutes_taken = doctor["avg"] + random.uniform(-3, 3)
            else:
                started = current["called_at"] or time.time()
                minutes_taken = (time.time() - started) / 60

            minutes_taken = min(max(minutes_taken, 3), 40)
            new_average = doctor["avg"] * 0.7 + minutes_taken * 0.3

            db.execute(
                "UPDATE doctors SET avg = ? WHERE id = ?",
                (round(max(3, new_average), 1), doctor_id),
            )

    waiting_list = get_waiting_list(doctor_id)
    next_patient = waiting_list[0] if waiting_list else None

    if next_patient is not None:
        db.execute(
            "UPDATE tokens SET status = 's', called_at = ? WHERE id = ?",
            (time.time(), next_patient["id"]),
        )

    db.commit()
    notify_patients()

    if next_patient is not None:
        notify_turn(next_patient, doctor)

    return jsonify(ok=True, called=next_patient is not None)


# ======================================================================
# PART 5: ROUTES
# ======================================================================
def is_logged_in():
    return session.get("ok") is True


# -------------------- Two separate websites --------------------
#   /        PATIENT website   - open to everyone. It has no link to the
#                                admin panel anywhere.
#   /admin   ADMIN website     - a visitor who is not logged in only gets a
#                                login page. The admin panel itself (page and
#                                script) is sent only to a logged-in admin.
#                                A logged-in admin can open both websites.
def page(name):
    """Send one file from the static folder (never cached)."""
    reply = send_from_directory("static", name)
    reply.headers["Cache-Control"] = "no-store"
    return reply


@app.get("/")
def home_page():
    """Patient website (welcome page + token + live board)."""
    return page("user.html")


@app.get("/user")
def user_page():
    """Old address of the patient portal: send people to the main page."""
    return redirect("/")


@app.get("/admin")
@app.get("/admin/")
def admin_page():
    if is_logged_in():
        return page("admin.html")
    return page("admin-login.html")


@app.get("/admin/admin.js")
def admin_script():
    """The admin panel's code is private: admin login required."""
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401
    return page("admin.js")


# Files that any visitor may download (patient site + admin login page).
PUBLIC_FILES = {"style.css", "user.js", "admin-login.js"}


@app.get("/<path:name>")
def public_file(name):
    if name not in PUBLIC_FILES:
        return jsonify(error="Not found"), 404
    return page(name)


# -------------------- Login --------------------
login_failures = {}  # ip -> [timestamps of recent wrong passwords]


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    visitor = request.remote_addr or "?"
    now = time.time()

    # Slow down password guessing: 5 wrong tries, then wait 5 minutes.
    recent = [t for t in login_failures.get(visitor, []) if now - t < 300]
    login_failures[visitor] = recent
    if len(recent) >= 5:
        return jsonify(
            ok=False, error="Too many wrong tries. Wait 5 minutes."
        ), 429

    email_ok = hmac.compare_digest(
        str(data.get("email", "")).strip().lower().encode(),
        ADMIN_EMAIL.lower().encode(),
    )
    password_ok = hmac.compare_digest(
        str(data.get("password", "")).encode(), ADMIN_PASSWORD.encode()
    )

    if email_ok and password_ok:
        login_failures.pop(visitor, None)
        session["ok"] = True
        session["email"] = ADMIN_EMAIL
        return jsonify(ok=True, email=ADMIN_EMAIL)

    recent.append(now)
    return jsonify(ok=False), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def who_am_i():
    return jsonify(
        ok=is_logged_in(),
        email=session.get("email"),
    )


# -------------------- State for the website --------------------
@app.get("/api/sms-check")
def api_sms_check():
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401
    return jsonify(results=[{"provider": p, "ok": ok, "text": t} for p, ok, t in sms_check()])


@app.post("/api/test-sms")
def api_test_sms():
    """Send one test SMS to a phone; the result shows in the notification log."""
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401
    phone = fix_phone_number(str((request.get_json(silent=True) or {}).get("phone", "")))
    if phone is None:
        return jsonify(error="Enter a valid phone number (01XXXXXXXXX)"), 400
    text = "QueueCare test SMS. If you can read this, SMS is working. " + clock_text(time.time())
    db = get_db()
    cursor = db.execute(
        "INSERT INTO sms_log (channel, text, status, created) VALUES ('SMS', ?, 'Sending...', ?)",
        (text, time.time()))
    db.commit()
    send_in_background([(cursor.lastrowid, "sms", phone, "", text)])
    return jsonify(ok=True)


@app.get("/api/state")
def get_state():
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    db = get_db()
    doctors = []
    waiting_by_token = {}

    for doctor in db.execute(
        "SELECT * FROM doctors ORDER BY id"
    ).fetchall():
        serving = get_patient_being_served(doctor["id"])
        busy = 1 if serving else 0

        waiting = []
        for position, patient in enumerate(
            get_waiting_list(doctor["id"])
        ):
            info = token_to_dict(
                patient, doctor, position + busy
            )
            waiting.append(info)
            waiting_by_token[info["no"]] = info

        doctors.append({
            "id": doctor["id"],
            "name": doctor["name"],
            "dept": doctor["dept"],
            "avg": round(doctor["avg"], 1),
            "serving": (
                token_to_dict(serving, doctor, 0)
                if serving else None
            ),
            "waiting": waiting,
            "new_wait": predict_wait(
                busy + len(waiting), doctor["avg"]
            ),
        })

    doctor_by_id = {doctor["id"]: doctor for doctor in doctors}
    tokens = []

    latest = db.execute(
        "SELECT * FROM tokens ORDER BY id DESC LIMIT 200"
    ).fetchall()

    for token in reversed(latest):
        if token["id"] in waiting_by_token:
            tokens.append(waiting_by_token[token["id"]])
        else:
            doctor = doctor_by_id.get(token["doctor_id"])
            if doctor:
                tokens.append(token_to_dict(token, doctor, 0))

    def count(sql):
        return db.execute(sql).fetchone()[0]

    stats = {
        "waiting": count(
            "SELECT COUNT(*) FROM tokens WHERE status = 'w'"
        ),
        "served": count(
            "SELECT COUNT(*) FROM tokens WHERE status = 'd'"
        ),
        "emergencies": count(
            "SELECT COUNT(*) FROM tokens WHERE emergency = 1"
        ),
        "sms": count(
            "SELECT COUNT(*) FROM sms_log "
            "WHERE channel = 'SMS' AND status LIKE 'Sent.%'"
        ),
        "emails": count(
            "SELECT COUNT(*) FROM sms_log "
            "WHERE channel = 'Email' AND status LIKE 'Sent.%'"
        ),
        "online": count(
            "SELECT COUNT(*) FROM tokens WHERE source = 'online'"
        ),
    }

    log_rows = db.execute(
        """
        SELECT channel, text, status
        FROM sms_log
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    notifications = [dict(row) for row in log_rows]

    return jsonify(
        doctors=doctors,
        tokens=tokens,
        stats=stats,
        notifications=notifications,
        sms_mode=SMS_MODE,
        email_ready=bool(SMTP_USER),
    )


# -------------------- Patients / tokens --------------------
@app.post("/api/tokens")
def create_token():
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()[:60]
    phone = fix_phone_number(str(data.get("phone", "")))
    email = str(data.get("email", "")).strip()[:100]
    doctor_id = data.get("doctor_id")
    emergency = 1 if data.get("emergency") else 0

    db = get_db()
    doctor = db.execute(
        "SELECT id FROM doctors WHERE id = ?",
        (doctor_id,),
    ).fetchone()

    if email and not EMAIL_PATTERN.fullmatch(email):
        return jsonify(
            error="That email address does not look right"
        ), 400

    if not name or not phone or doctor is None:
        return jsonify(
            error=(
                "Enter a name, a valid phone number "
                "(01XXXXXXXXX) and a doctor"
            )
        ), 400

    cursor = db.execute(
        """
        INSERT INTO tokens
            (name, phone, email, doctor_id, emergency, created)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            phone,
            email,
            doctor_id,
            emergency,
            time.time(),
        ),
    )
    db.commit()

    notify_patients()

    return jsonify(ok=True, no=cursor.lastrowid)


# -------------------- Doctors --------------------
@app.post("/api/doctors/<int:doctor_id>/next")
def next_patient(doctor_id):
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    return call_next_patient(
        doctor_id, skip_current=False
    )


@app.post("/api/doctors/<int:doctor_id>/skip")
def skip_patient(doctor_id):
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    return call_next_patient(
        doctor_id, skip_current=True
    )


@app.post("/api/doctors")
def add_doctor():
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()[:60]
    dept = str(data.get("dept", "")).strip()[:60] or "General"

    if not name:
        return jsonify(error="Enter the doctor name"), 400

    try:
        average = min(
            max(float(data.get("avg") or 8), 1),
            120,
        )
    except (ValueError, TypeError):
        average = 8

    db = get_db()
    db.execute(
        "INSERT INTO doctors (name, dept, avg) VALUES (?, ?, ?)",
        (name, dept, average),
    )
    db.commit()

    return jsonify(ok=True)


# -------------------- Admin control over tokens --------------------
@app.post("/api/tokens/<int:token_id>/cancel")
def admin_cancel_token(token_id):
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    db = get_db()
    token = db.execute(
        "SELECT * FROM tokens WHERE id = ?", (token_id,)
    ).fetchone()

    if token is None:
        return jsonify(error="Token not found"), 404
    if token["status"] not in ("w", "s"):
        return jsonify(error="This token is already closed"), 400

    db.execute("UPDATE tokens SET status = 'c' WHERE id = ?", (token_id,))
    db.commit()
    notify_patients()
    return jsonify(ok=True)


@app.post("/api/tokens/<int:token_id>/priority")
def admin_toggle_priority(token_id):
    """Switch a waiting token between normal and emergency."""
    if not is_logged_in():
        return jsonify(error="Not logged in"), 401

    db = get_db()
    token = db.execute(
        "SELECT * FROM tokens WHERE id = ?", (token_id,)
    ).fetchone()

    if token is None:
        return jsonify(error="Token not found"), 404
    if token["status"] != "w":
        return jsonify(error="Only waiting tokens can change priority"), 400

    db.execute(
        "UPDATE tokens SET emergency = ? WHERE id = ?",
        (0 if token["emergency"] else 1, token_id),
    )
    db.commit()
    notify_patients()
    return jsonify(ok=True)


# ======================================================================
# PATIENT PORTAL API (no admin login needed)
#
# Rules for this part:
#   - A patient can only see doctors' public queue numbers and THEIR OWN
#     token (token number + the phone number used to book it).
#   - Patient names, phones, notification logs and settings are never
#     returned here. Those stay in the admin API above.
#   - Online tokens are always normal priority. Only the admin can mark
#     a token as emergency.
# ======================================================================
def public_token_info(token):
    """What a patient may see about their own token."""
    db = get_db()
    doctor = db.execute(
        "SELECT * FROM doctors WHERE id = ?", (token["doctor_id"],)
    ).fetchone()

    ahead = 0
    if token["status"] == "w" and doctor is not None:
        busy = 1 if get_patient_being_served(doctor["id"]) else 0
        for position, patient in enumerate(get_waiting_list(doctor["id"])):
            if patient["id"] == token["id"]:
                ahead = position + busy
                break

    wait = predict_wait(ahead, doctor["avg"]) if doctor else 0
    now = time.time()

    return {
        "no": token["id"],
        "label": make_label(token),
        "status": token["status"],
        "emergency": bool(token["emergency"]),
        "doctor_id": doctor["id"] if doctor else None,
        "doctor_name": doctor["name"] if doctor else "",
        "dept": doctor["dept"] if doctor else "",
        "ahead": ahead,
        "wait": wait,
        "expected_at": (now + wait * 60) * 1000,  # milliseconds, for JavaScript
        "server_time": now * 1000,
    }


def find_own_token(token_no, phone_text):
    """Return the token only if the phone number matches."""
    phone = fix_phone_number(str(phone_text or ""))
    try:
        token_no = int(token_no)
    except (ValueError, TypeError):
        return None
    if phone is None:
        return None

    token = get_db().execute(
        "SELECT * FROM tokens WHERE id = ? AND phone = ?",
        (token_no, phone),
    ).fetchone()
    return token


@app.get("/api/public/doctors")
def public_doctors():
    db = get_db()
    result = []
    now = time.time()

    for doctor in db.execute("SELECT * FROM doctors ORDER BY id").fetchall():
        serving = get_patient_being_served(doctor["id"])
        busy = 1 if serving else 0
        waiting = get_waiting_list(doctor["id"])
        new_wait = predict_wait(busy + len(waiting), doctor["avg"])

        result.append({
            "id": doctor["id"],
            "name": doctor["name"],
            "dept": doctor["dept"],
            "avg": round(doctor["avg"], 1),
            "serving": make_label(serving) if serving else None,
            "next": [make_label(p) for p in waiting[:4]],
            "next_emergency": [bool(p["emergency"]) for p in waiting[:4]],
            "waiting": len(waiting),
            "new_wait": new_wait,
            "new_at": (now + new_wait * 60) * 1000,
        })

    # server_time lets the page show the hospital's clock even if the
    # patient's phone clock is a few minutes wrong.
    return jsonify(doctors=result, server_time=now * 1000)


@app.post("/api/public/tokens")
def public_create_token():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()[:60]
    phone = fix_phone_number(str(data.get("phone", "")))
    email = str(data.get("email", "")).strip()[:100]

    db = get_db()
    doctor = db.execute(
        "SELECT id FROM doctors WHERE id = ?", (data.get("doctor_id"),)
    ).fetchone()

    if email and not EMAIL_PATTERN.fullmatch(email):
        return jsonify(error="That email address does not look right"), 400

    if not name or not phone or doctor is None:
        return jsonify(
            error="Enter your name, a valid phone number (01XXXXXXXXX) and a doctor"
        ), 400

    already = db.execute(
        """
        SELECT id FROM tokens
        WHERE phone = ? AND doctor_id = ? AND status IN ('w', 's')
        """,
        (phone, doctor["id"]),
    ).fetchone()
    if already is not None:
        return jsonify(
            error=f"You already have an active token (T-{already['id']}) with this doctor"
        ), 400

    cursor = db.execute(
        """
        INSERT INTO tokens
            (name, phone, email, doctor_id, emergency, created, source)
        VALUES (?, ?, ?, ?, 0, ?, 'online')
        """,
        (name, phone, email, doctor["id"], time.time()),
    )
    db.commit()

    # Confirmation SMS + email for the patient who just booked online.
    # The email result (sent or the real reason it failed) goes back to the page.
    email_result = send_booking_confirmation(cursor.lastrowid)
    notify_patients()

    token = db.execute(
        "SELECT * FROM tokens WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return jsonify(
        ok=True,
        no=token["id"],
        token=public_token_info(token),
        email=email_result,
    )


@app.get("/api/public/token")
def public_get_token():
    token = find_own_token(
        request.args.get("no"), request.args.get("phone")
    )
    if token is None:
        return jsonify(error="Token not found. Check the number and phone."), 404
    return jsonify(ok=True, token=public_token_info(token))


@app.post("/api/public/tokens/cancel")
def public_cancel_token():
    data = request.get_json(silent=True) or {}
    token = find_own_token(data.get("no"), data.get("phone"))

    if token is None:
        return jsonify(error="Token not found"), 404
    if token["status"] != "w":
        return jsonify(
            error="Only a waiting token can be cancelled. Please ask reception."
        ), 400

    db = get_db()
    db.execute("UPDATE tokens SET status = 'c' WHERE id = ?", (token["id"],))
    db.commit()
    notify_patients()

    return jsonify(ok=True)


# ======================================================================
# PART 6: START THE SERVER
# ======================================================================
create_tables()


def find_free_port(start=5000):
    """Find an unused local port."""
    for port in range(start, start + 20):
        with socket.socket() as test_socket:
            if test_socket.connect_ex(
                ("127.0.0.1", port)
            ) != 0:
                return port

    return start


if __name__ == "__main__":
    port = int(os.getenv("PORT") or find_free_port())
    link = f"http://127.0.0.1:{port}"

    print("\n" + "=" * 58)
    print(f"  QueueCare is running.  Open:  {link}")
    print("  Keep this window open. Press Ctrl+C here to stop.")
    print("=" * 58 + "\n")

    threading.Timer(
        1.5,
        lambda: webbrowser.open(link),
    ).start()

    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
    )
