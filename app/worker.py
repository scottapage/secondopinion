"""Background worker: polls the mailbox every minute, processes queued bills,
and emails summaries once every bill from an inbound email has been analysed."""
import threading
import time
import traceback

from . import catalogue, db, example, extract, mailer, notify

POLL_SECONDS = 60


PARALLEL = 3


def claim():
    with db.tx() as c:
        row = c.execute("UPDATE invoices SET status='processing' WHERE id=(SELECT id FROM invoices"
                        " WHERE status='queued' ORDER BY id LIMIT 1) RETURNING id").fetchone()
        return row[0] if row else None


def extract_loop():
    while True:
        try:
            iid = claim()
            if iid:
                extract.process_invoice(iid)
                continue
        except Exception:
            traceback.print_exc()
        time.sleep(2)


def send_pending_summaries():
    subs = db.q(
        "SELECT s.id FROM submissions s WHERE s.source='email' AND s.notified_at IS NULL"
        " AND s.created_at < strftime('%s','now') - 5"
        " AND EXISTS (SELECT 1 FROM invoices i WHERE i.submission_id=s.id) AND NOT EXISTS"
        " (SELECT 1 FROM invoices i WHERE i.submission_id=s.id AND (i.status IN ('queued','processing')"
        " OR i.learning_state='running'))"
    )
    for s in subs:
        notify.submission_summary(s["id"])


def mail_loop():
    while True:
        try:
            mailer.poll()
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


def catalogue_loop():
    """Refresh the plan catalogue on request, and every `catalogue_days` days."""
    time.sleep(30)
    while True:
        try:
            days = float(db.get_setting("catalogue_days") or 14)
            last = float(db.get_setting("catalogue_refreshed_at") or 0)
            if db.get_setting("catalogue_requested") == "1" or time.time() - last > days * 86400:
                db.set_setting("catalogue_requested", "running")
                catalogue.refresh_all()
                catalogue.refresh_all_benefits()
                db.set_setting("catalogue_requested", "0")
        except Exception:
            traceback.print_exc()
            db.set_setting("catalogue_requested", "0")
        time.sleep(300)


def digest_loop():
    """Send the daily digest at 7am New Zealand time (once per day)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    while True:
        try:
            now = datetime.now(ZoneInfo("Pacific/Auckland"))
            today = now.strftime("%Y-%m-%d")
            if db.get_setting("digest") == "on" and now.hour >= 7 and db.get_setting("digest_sent") != today:
                db.set_setting("digest_sent", today)
                notify.daily_digest()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def main():
    db.init()
    # Bills left mid-processing by a restart go back on the queue
    db.x("UPDATE invoices SET status='queued' WHERE status='processing'")
    db.x("UPDATE invoices SET learning_state='done' WHERE learning_state='running'")
    example.seed(force=db.get_setting("example_version") != "3")
    if db.get_setting("catalogue_requested") == "running":
        db.set_setting("catalogue_requested", "1")
    threading.Thread(target=mail_loop, daemon=True).start()
    threading.Thread(target=catalogue_loop, daemon=True).start()
    threading.Thread(target=digest_loop, daemon=True).start()
    for _ in range(PARALLEL):
        threading.Thread(target=extract_loop, daemon=True).start()
    while True:
        try:
            db.set_setting("worker_heartbeat", str(time.time()))
            send_pending_summaries()
        except Exception:
            traceback.print_exc()
        time.sleep(2)


if __name__ == "__main__":
    main()
