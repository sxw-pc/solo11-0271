"""Local controller image artifact registry with encoded and decoded identities."""
import base64
import gzip
import hashlib
import json

from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_blobs (
    digest TEXT PRIMARY KEY, diff_id TEXT NOT NULL, encoding TEXT NOT NULL,
    size INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS image_manifests (
    digest TEXT PRIMARY KEY, platform TEXT NOT NULL, layers TEXT NOT NULL, diff_ids TEXT NOT NULL
);
"""


def digest(value):
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def manifest_digest(platform, layers, diff_ids):
    return digest(json.dumps(dict(platform=platform, layers=layers, diff_ids=diff_ids),
                             sort_keys=True, separators=(',', ':')).encode())


def handle(db, x):
    if x['op'] == 'blob':
        try:
            binary = base64.b64decode(x['payload'], validate=True)
            if x['encoding'] == 'gzip':
                plain = gzip.decompress(binary)
            elif x['encoding'] == 'raw':
                plain = binary
            else:
                return {'decision': 'encoding_denied'}
        except (ValueError, OSError, EOFError):
            return {'decision': 'invalid_blob'}
        if digest(binary) != x['digest'] or digest(plain) != x['diff_id']:
            return {'decision': 'digest_mismatch'}
        db.execute('INSERT OR IGNORE INTO image_blobs VALUES (?,?,?,?,?)',
                   (x['digest'], x['diff_id'], x['encoding'], len(binary), x['payload']))
        return {'decision': 'blob_stored', 'size': len(binary)}
    if x['op'] == 'manifest':
        layers, diff_ids = x['layers'], x['diff_ids']
        if (not layers or len(layers) != len(diff_ids)
                or manifest_digest(x['platform'], layers, diff_ids) != x['digest']):
            return {'decision': 'manifest_denied'}
        db.execute('INSERT OR IGNORE INTO image_manifests VALUES (?,?,?,?)',
                   (x['digest'], x['platform'], json.dumps(layers), json.dumps(diff_ids)))
        return {'decision': 'manifest_stored'}
    return {'decision': 'unknown_operation'}
