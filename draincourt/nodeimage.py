"""A local node imports and validates an ordered controller image manifest."""
import json

from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_installs (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, platform TEXT NOT NULL);
"""


def handle(db, x):
    if x['op'] != 'install':
        return {'decision': 'unknown_operation'}
    item = one(db, 'SELECT * FROM image_manifests WHERE digest=? AND platform=?', (x['manifest'], x['platform']))
    if not item:
        return {'decision': 'manifest_unavailable'}
    for layer, expected in zip(json.loads(item['layers']), json.loads(item['diff_ids'])):
        blob = one(db, 'SELECT diff_id FROM image_blobs WHERE digest=?', (layer,))
        if not blob or blob['diff_id'] != expected:
            return {'decision': 'layer_unavailable', 'layer': layer}
    if one(db, 'SELECT id FROM image_installs WHERE id=?', (x['id'],)):
        return {'decision': 'install_identity_conflict'}
    db.execute('INSERT INTO image_installs VALUES (?,?,?)', (x['id'], item['digest'], item['platform']))
    return {'decision': 'image_installed', 'manifest': item['digest']}
