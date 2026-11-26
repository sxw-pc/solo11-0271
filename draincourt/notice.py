"""Offline notification simulator with distinct attempt, delivery, and acknowledgment."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_messages (
    id TEXT PRIMARY KEY, receiver TEXT NOT NULL, request TEXT NOT NULL,
    status TEXT NOT NULL, evidence TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_attempts (
    id TEXT PRIMARY KEY, message TEXT NOT NULL, sent INTEGER NOT NULL, delivered INTEGER,
    acknowledged INTEGER
);
CREATE TABLE IF NOT EXISTS notice_receiver_state (
    receiver TEXT NOT NULL, request TEXT NOT NULL, incident TEXT NOT NULL,
    last_attempt TEXT NOT NULL, PRIMARY KEY (receiver, request)
);
"""


def handle(db, x):
    op, at = x['op'], x['at']
    if op == 'enqueue':
        case = one(db, 'SELECT * FROM drain_cases WHERE id=?', (x['evidence'],))
        if (not case or case['request'] != x['request'] or at < case['at']
                or one(db, 'SELECT 1 FROM notice_messages WHERE id=?', (x['id'],))):
            return {'decision': 'notice_denied'}
        status = 'open' if case['status'] == 'waiting' else 'closed'
        db.execute('INSERT INTO notice_messages VALUES (?,?,?,?,?,?)',
                   (x['id'], x['receiver'], x['request'], status, case['id'], at))
        return {'decision': 'notice_queued', 'status': status}
    if op == 'send':
        message = one(db, 'SELECT * FROM notice_messages WHERE id=?', (x['message'],))
        if not message or at < message['at'] or one(db, 'SELECT 1 FROM notice_attempts WHERE id=?', (x['id'],)):
            return {'decision': 'send_denied'}
        db.execute('INSERT INTO notice_attempts VALUES (?,?,?,NULL,NULL)', (x['id'], message['id'], at))
        return {'decision': 'attempt_sent'}
    if op in ('deliver', 'ack'):
        attempt = one(db, 'SELECT a.*, m.receiver, m.request, m.status FROM notice_attempts a '
                      'JOIN notice_messages m ON m.id=a.message WHERE a.id=?', (x['attempt'],))
        if not attempt or x['receiver'] != attempt['receiver'] or at < attempt['sent']:
            return {'decision': 'attempt_denied'}
        if op == 'deliver':
            if attempt['delivered'] is not None:
                return {'decision': 'attempt_already_delivered'}
            db.execute('UPDATE notice_attempts SET delivered=? WHERE id=?', (at, attempt['id']))
            db.execute('INSERT INTO notice_receiver_state VALUES (?,?,?,?) '
                       'ON CONFLICT(receiver, request) DO UPDATE SET incident=excluded.incident, last_attempt=excluded.last_attempt',
                       (attempt['receiver'], attempt['request'], attempt['status'], attempt['id']))
            return {'decision': 'attempt_delivered', 'incident': attempt['status']}
        if attempt['delivered'] is None or at < attempt['delivered'] or attempt['acknowledged'] is not None:
            return {'decision': 'ack_denied'}
        db.execute('UPDATE notice_attempts SET acknowledged=? WHERE id=?', (at, attempt['id']))
        return {'decision': 'attempt_acknowledged'}
    return {'decision': 'unknown_operation'}
