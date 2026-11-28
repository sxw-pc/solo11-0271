"""Render a local controller frame from literals and source spans."""
import hashlib
import json
from pathlib import Path

from .store import one
from .tracevalues import PUBLIC, PRIVATE, source

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_frames (
    id TEXT PRIMARY KEY, incident TEXT NOT NULL, recipe TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace_shares (
    id TEXT PRIMARY KEY, incident TEXT NOT NULL, mode TEXT NOT NULL,
    frames TEXT NOT NULL, namespace TEXT, artifact TEXT NOT NULL, sha256 TEXT NOT NULL
);
"""

# Structure authorization: every private span uses the same semantic-free
# placeholder, so a reader cannot tell which source hides behind a redaction.
REDACTION = '[遮盖]'

# Correlation authorization: opaque per-export tokens. The namespace is derived
# from the authorization event itself (never from source text, spans or another
# event), and the sequence only distinguishes distinct private source
# identities in first-appearance order inside this one export.
TOKEN_TEMPLATE = '[{ns}-S{n}]'
_NAMESPACE_HEX = 12


def handle(db, x):
    op = x.get('op')
    if op == 'share':
        return share_handle(db, x)
    if op == 'share-read':
        return share_read_handle(db, x)
    if op != 'frame':
        return {'decision': 'unknown_operation'}
    if one(db, 'SELECT 1 FROM trace_frames WHERE id=?', (x['id'],)):
        return {'decision': 'frame_denied'}
    output = []
    for part in x['recipe']:
        if 'literal' in part:
            output.append(part['literal'])
            continue
        value = source(db, part['ref'], x['incident'])
        if not value or not 0 <= part['start'] < part['end'] <= len(value['text']):
            return {'decision': 'fragment_denied'}
        output.append(value['text'][part['start']:part['end']])
    text = ''.join(output)
    db.execute('INSERT INTO trace_frames VALUES (?,?,?,?)', (x['id'], x['incident'], json.dumps(x['recipe'], sort_keys=True), text))
    return {'decision': 'frame_rendered', 'text': text}


def _denied(reason):
    return {'decision': 'share_denied', 'reason': reason}


def _unavailable(reason):
    # The export must never be partially generated: a broken frame means no
    # artifact at all, since a partial attachment could still carry private
    # synthetic spans.
    return {'decision': 'share_unavailable', 'reason': reason}


def _valid_span(part):
    return (isinstance(part, dict) and 'ref' in part
            and type(part.get('start')) is int and type(part.get('end')) is int)


def _namespace(incident, share_id, mode, frame_ids):
    """Opaque namespace valid only for this one authorization event."""
    canonical = json.dumps(
        {'incident': incident, 'share': share_id, 'mode': mode, 'frames': frame_ids},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'),
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:_NAMESPACE_HEX]


def share_handle(db, x):
    if x.get('op') != 'share':
        return {'decision': 'unknown_operation'}
    share_id = x.get('id')
    incident = x.get('incident')
    mode = x.get('mode')
    frame_ids = x.get('frames')
    destination = x.get('destination')
    if (not isinstance(share_id, str) or not isinstance(incident, str)
            or mode not in ('structure', 'correlation')
            or not isinstance(frame_ids, list) or not frame_ids
            or not all(isinstance(fid, str) for fid in frame_ids)
            or not isinstance(destination, str) or not destination):
        return _denied('malformed_share_request')
    if len(frame_ids) != len(set(frame_ids)):
        return _denied('duplicate_frame')
    if one(db, 'SELECT 1 FROM trace_shares WHERE id=?', (share_id,)):
        return _denied('share_identity_conflict')
    if one(db, 'SELECT 1 FROM trace_shares WHERE artifact=?', (destination,)):
        return _denied('destination_in_use')

    namespace = None if mode == 'structure' else _namespace(incident, share_id, mode, frame_ids)
    rendered = []
    # Distinct private source identities, in first-appearance order within this
    # export. The mapping never leaves this function: no lookup table is
    # exported, and token numbering carries no original value.
    private_order = {}

    for fid in frame_ids:
        frame = one(db, 'SELECT * FROM trace_frames WHERE id=?', (fid,))
        if not frame:
            return _unavailable('unknown_frame')
        if frame['incident'] != incident:
            return _unavailable('cross_event_frame')
        try:
            recipe = json.loads(frame['recipe'])
        except (TypeError, ValueError):
            return _unavailable('corrupt_recipe')
        if not isinstance(recipe, list) or not recipe:
            return _unavailable('corrupt_recipe')

        pieces = []
        for part in recipe:
            if isinstance(part, dict) and 'literal' in part:
                if not isinstance(part['literal'], str):
                    return _unavailable('corrupt_recipe')
                pieces.append(part['literal'])
                continue
            if not _valid_span(part):
                return _unavailable('corrupt_recipe')
            value = source(db, part['ref'], incident)
            if not value:
                # Unknown provenance: we cannot prove the span is public.
                return _unavailable('unknown_source')
            if not 0 <= part['start'] < part['end'] <= len(value['text']):
                return _unavailable('span_out_of_bounds')
            if value['classification'] == PUBLIC:
                # Same-looking public fields and ordinary literals stay intact,
                # including spans containing pi; character positions are Python
                # code-point positions.
                pieces.append(value['text'][part['start']:part['end']])
                continue
            if value['classification'] != PRIVATE:
                return _unavailable('unknown_classification')
            if mode == 'structure':
                pieces.append(REDACTION)
            else:
                token_index = private_order.setdefault(value['id'], len(private_order) + 1)
                pieces.append(TOKEN_TEMPLATE.format(ns=namespace, n=token_index))
        rendered.append({'frame': fid, 'text': ''.join(pieces)})

    artifact = {
        'share': share_id,
        'incident': incident,
        'mode': mode,
        'frames': rendered,
    }
    if mode == 'correlation':
        artifact['namespace'] = namespace
    payload = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    # Only reached after every frame validated; a failed export writes nothing.
    target = Path(destination)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as fh:
        fh.write(payload)
    db.execute(
        'INSERT INTO trace_shares VALUES (?,?,?,?,?,?,?)',
        (share_id, incident, mode, json.dumps(frame_ids), namespace, destination, digest),
    )
    return {
        'decision': 'share_exported',
        'artifact': destination,
        'sha256': digest,
        'frames': frame_ids,
    }


def share_read_handle(db, x):
    if x.get('op') != 'share-read':
        return {'decision': 'unknown_operation'}
    share_id = x.get('id')
    if not isinstance(share_id, str):
        return _denied('malformed_share_request')
    record = one(db, 'SELECT * FROM trace_shares WHERE id=?', (share_id,))
    if not record:
        return {'decision': 'share_unknown', 'reason': 'unknown_share'}
    try:
        with open(record['artifact'], encoding='utf-8') as fh:
            payload = fh.read()
    except OSError:
        return {'decision': 'share_unavailable', 'reason': 'artifact_missing'}
    if hashlib.sha256(payload.encode('utf-8')).hexdigest() != record['sha256']:
        # A tampered or re-derived attachment is not the completed export.
        return {'decision': 'share_unavailable', 'reason': 'artifact_changed'}
    return {'decision': 'share_verified', 'artifact': json.loads(payload)}
