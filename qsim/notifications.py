"""Durable, rate-limited email: key-free review notices and approved key delivery."""
from email.message import EmailMessage
from email.utils import formatdate
from html import escape
from urllib.parse import quote
import os
from pathlib import Path
import smtplib
import ssl
import time
from . import store

RECIPIENT = os.environ.get('QSIM_REVIEW_EMAIL', 'operator@example.invalid')
SENDER = os.environ.get('QSIM_SMTP_SENDER', 'service@example.invalid')
PUBLIC_URL = os.environ.get('QSIM_PUBLIC_URL', 'https://simulation.example.invalid').rstrip('/')
CONSOLE_URL = os.environ.get('QSIM_CONSOLE_URL', 'http://admin.example.invalid/qsimconsole').rstrip('/')


def transport(message):
    password = Path(os.environ.get('QSIM_SMTP_PASSWORD_FILE','/run/qsim-smtp/app-password')).read_text().strip()
    with smtplib.SMTP(os.environ['QSIM_SMTP_HOST'], int(os.environ.get('QSIM_SMTP_PORT','587')), timeout=10) as smtp:
        smtp.ehlo();smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
        smtp.login(os.environ.get('QSIM_SMTP_USERNAME', SENDER), password)
        smtp.send_message(message)


def message_base(row, recipient, kind, subject):
    message=EmailMessage()
    message['From']=SENDER;message['To']=recipient;message['Subject']=subject
    message['Date']=formatdate(localtime=False)
    message['Message-ID']=f'<qsim-{kind}-{row["id"]}@{os.environ.get("QSIM_MAIL_DOMAIN", "example.invalid")}>'
    return message


def send_notice(row):
    url=CONSOLE_URL+'/#request-'+quote(row['id'],safe='')
    message=message_base(row,RECIPIENT,'request','wqsim API key request awaiting approval')
    message.set_content(
        f'An API key request is awaiting your review. No key has been issued.\n\n'
        f'Request: {row["id"]}\nName: {row["name"]}\nEmail: {row["email"]}\n'
        f'Intended use:\n{row["purpose"]}\n\n'
        f'Open console: {url}\n'
        f'Approve request (review and confirm in the console): {url}\n\n'
        'These links require the internal network. Opening a link does not approve the request.\n'
        'Verify the requester before approving. Approval emails the issued key to the requested address.\n')
    message.add_alternative(f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#172b36;line-height:1.6;max-width:640px;padding:24px">
<h1 style="font-size:24px">wqsim access request</h1>
<p>A request is waiting for your review. <strong>No key has been issued.</strong></p>
<p><strong>Name:</strong> {escape(row['name'])}<br>
<strong>Email:</strong> {escape(row['email'])}<br>
<strong>Request:</strong> {escape(row['id'])}</p>
<p><strong>Intended use</strong></p><p style="white-space:pre-wrap">{escape(row['purpose'])}</p>
<p style="margin:28px 0"><a href="{escape(url,quote=True)}" style="display:inline-block;background:#156b52;color:#ffffff;padding:14px 24px;border-radius:6px;font-weight:bold;text-decoration:none">Approve request</a></p>
<p><a href="{escape(url,quote=True)}" style="color:#156b52;text-decoration:underline">Open request in the console</a><br>
<span style="font-size:13px">{escape(url)}</span></p>
<p>Connect to the internal network to open the console. The button opens this request for review; you must confirm approval in the console.</p>
<p>Verify the requester before approving. Approval emails the issued key to the requested address.</p>
</body></html>""",subtype='html')
    transport(message)


def send_key(row, key):
    message=message_base(row,row['email'],'approved','Your wqsim API key')
    message.set_content(
        f'Your wqsim API access request has been approved.\n\n'
        f'Identity: {row["identity"]}\nAPI key: {key}\n\n'
        f'API documentation: {PUBLIC_URL}/\n'
        'Authenticate using the Authorization: Bearer header. Keep this key private.\n'
        'If you did not request access, contact the service operator.\n')
    transport(message)


def deliver_one(notice_sender=send_notice, key_sender=send_key, decrypt=None, now=None):
    now=time.time() if now is None else now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM notification_attempts WHERE created<?',(now-86400,))
        for prefix in ('notification','delivery'):
            state='UNCERTAIN' if prefix=='delivery' else 'RETRY'
            db.execute(f"UPDATE key_requests SET {prefix}_state=? WHERE {prefix}_state='SENDING' AND {prefix}_lease<?",(state,now))
        if db.execute('SELECT COUNT(*) FROM notification_attempts WHERE created>?',(now-3600,)).fetchone()[0]>=10:
            return False
        row=db.execute("SELECT * FROM key_requests WHERE status='APPROVED' AND delivery_state IN ('PENDING','RETRY','UNCERTAIN') AND delivery_attempts<5 AND delivery_next<=? ORDER BY reviewed LIMIT 1",(now,)).fetchone()
        prefix='delivery'
        if row is None:
            row=db.execute("SELECT * FROM key_requests WHERE status='PENDING' AND notification_state IN ('PENDING','RETRY') AND notification_next<=? ORDER BY created LIMIT 1",(now,)).fetchone()
            prefix='notification'
        if row is None:return False
        db.execute(f"UPDATE key_requests SET {prefix}_state='SENDING', {prefix}_lease=?, {prefix}_attempts={prefix}_attempts+1 WHERE id=?",(now+120,row['id']))
        db.execute('INSERT INTO notification_attempts (request_id,created) VALUES (?,?)',(row['id'],now))
    try:
        if prefix=='delivery':
            payload=decrypt(row['key_cipher'])
            if payload['email']!=row['email'] or payload['identity']!=row['identity'] or payload['request_id']!=row['id']:
                raise ValueError('Approval envelope mismatch')
            key_sender(dict(row),payload['key'])
        else:notice_sender(dict(row))
    except Exception as error:
        attempts=row[prefix+'_attempts']+1
        uncertain=prefix=='delivery' and isinstance(error,(smtplib.SMTPServerDisconnected,TimeoutError,OSError))
        state='UNCERTAIN' if uncertain else ('FAILED' if attempts>=5 else 'RETRY')
        with store.connect() as db:
            db.execute(f"UPDATE key_requests SET {prefix}_state=?,{prefix}_next=?,{prefix}_error=?,{prefix}_lease=NULL WHERE id=?",
                       (state,now+min(3600,60*2**(attempts-1)),type(error).__name__,row['id']))
    else:
        with store.connect() as db:
            if prefix=='delivery':
                db.execute("UPDATE key_requests SET delivery_state='SENT', delivered=?,delivery_error=NULL,delivery_lease=NULL,key_cipher=NULL WHERE id=?",(time.time(),row['id']))
            else:
                db.execute("UPDATE key_requests SET notification_state='SENT',notified=?,notification_error=NULL,notification_lease=NULL WHERE id=?",(time.time(),row['id']))
    return True
