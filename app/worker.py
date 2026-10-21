"""Background worker: polls the mailbox every minute, processes queued bills,
and emails summaries once every bill from an inbound email has been analysed."""
import threading
import time
import traceback

from . import db, extract, mailer, notify

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
        " (SELECT 1 FROM invoices i WHERE i.submission_id=s.id AND i.status IN ('queued','processing'))"
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


def main():
    db.init()
    # Bills left mid-processing by a restart go back on the queue
    db.x("UPDATE invoices SET status='queued' WHERE status='processing'")
    threading.Thread(target=mail_loop, daemon=True).start()
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
