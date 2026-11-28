import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from draincourt.store import connect, one, replay, run
from draincourt.tracevalues import PRIVATE
from draincourt.transcript import REDACTION

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_FIXTURE = ROOT / 'fixtures' / 'transcript_shares.json'
TOKEN_RE = re.compile(r'\[([0-9a-f]{12})-S(\d+)\]')
PRIVATE_TEXT = 'RID-π-732'
SOURCE_IDS = ('private-a', 'private-b', 'private-other')


def load_fragments():
    return json.loads((ROOT / 'fixtures' / 'transcript_fragments.json').read_text(encoding='utf8'))


def feed(db, events):
    return {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}


class TranscriptShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)
        feed(self.db, load_fragments())

    def share(self, share_id, incident, mode, frames, name):
        destination = str(self.dir / name)
        result = run(self.db, 'transcript', {
            'op': 'share', 'event_id': 'event-' + share_id, 'id': share_id,
            'incident': incident, 'mode': mode, 'frames': frames,
            'destination': destination,
        })
        self.assertEqual(result['decision'], 'share_exported', result)
        return destination, json.loads(Path(destination).read_text(encoding='utf8'))

    def frame_text(self, artifact, frame_id):
        return next(f['text'] for f in artifact['frames'] if f['frame'] == frame_id)

    # --- contract A: single-frame structure authorization -------------------
    def test_structure_contract_redacts_every_private_span_identically(self):
        _, artifact = self.share('contract-a', 'one', 'structure', ['first', 'second'], 'a.json')
        self.assertEqual(artifact['mode'], 'structure')
        self.assertNotIn('namespace', artifact)
        self.assertEqual(
            self.frame_text(artifact, 'first'),
            'pod=[遮盖] phase=waiting tag=RID- suffix=[遮盖]',
        )
        self.assertEqual(self.frame_text(artifact, 'second'), 'done=[遮盖] peer=[遮盖]')
        # Public same-looking field and literals survive verbatim; the public
        # RID- must remain even though private sources start with RID-.
        joined = ''.join(f['text'] for f in artifact['frames'])
        self.assertIn('tag=RID-', joined)
        self.assertIn('phase=waiting', joined)
        # Every private span is the very same semantic-free placeholder, so a
        # reader cannot tell done's source from peer's.
        self.assertEqual(joined.count(REDACTION), 4)
        self.assertNotIn('π', joined)

    def test_structure_contract_carries_no_cross_frame_correlation(self):
        _, artifact = self.share('contract-a2', 'one', 'structure', ['first', 'second'], 'a2.json')
        payload = json.dumps(artifact, ensure_ascii=False)
        for token in TOKEN_RE.findall(payload):  # no opaque identity tokens
            self.fail('structure export must not carry correlation tokens: %r' % (token,))
        for leaked in SOURCE_IDS + ('recipe', 'ref', PRIVATE_TEXT, 'π-732'):
            self.assertNotIn(leaked, payload)

    # --- contract B: single-event correlation authorization -----------------
    def test_correlation_links_same_source_across_frames_but_separates_identical_sources(self):
        _, artifact = self.share('contract-b', 'one', 'correlation', ['first', 'second'], 'b.json')
        first = self.frame_text(artifact, 'first')
        second = self.frame_text(artifact, 'second')
        first_tokens = TOKEN_RE.findall(first)
        second_tokens = TOKEN_RE.findall(second)
        # first's two private slices share one source identity -> one token.
        self.assertEqual(len(first_tokens), 2)
        self.assertEqual(first_tokens[0], first_tokens[1])
        a_token = first_tokens[0]
        # second's done is the same private-a slice (start 4) -> same token.
        self.assertEqual(second_tokens[0], a_token)
        # peer is private-b: identical text, different source -> other token.
        self.assertNotEqual(second_tokens[1], a_token)
        self.assertEqual(first, 'pod=[%s-S%s] phase=waiting tag=RID- suffix=[%s-S%s]'
                         % (a_token[0], a_token[1], a_token[0], a_token[1]))
        self.assertEqual(second, 'done=[%s-S%s] peer=[%s-S%s]'
                         % (a_token[0], a_token[1], second_tokens[1][0], second_tokens[1][1]))
        self.assertEqual(artifact['namespace'], a_token[0])
        self.assertEqual(second_tokens[1][0], artifact['namespace'])

    def test_correlation_namespace_is_per_event_and_per_export(self):
        _, one_artifact = self.share('b-one', 'one', 'correlation', ['first', 'second'], 'b1.json')
        _, two_artifact = self.share('b-two', 'two', 'correlation', ['third'], 'b2.json')
        # Third event gets an independent namespace; equal-looking sequence
        # numbers must not be equal or associable across the two exports.
        self.assertNotEqual(one_artifact['namespace'], two_artifact['namespace'])
        one_tokens = {m.group(0) for f in one_artifact['frames'] for m in TOKEN_RE.finditer(f['text'])}
        two_tokens = {m.group(0) for f in two_artifact['frames'] for m in TOKEN_RE.finditer(f['text'])}
        self.assertTrue(one_tokens and two_tokens)
        self.assertFalse(one_tokens & two_tokens)
        self.assertEqual(self.frame_text(two_artifact, 'third'),
                         'done=[%s-S1]' % two_artifact['namespace'])
        # A differently scoped export of the same event is another authorization:
        # its markers must not match the combined export either.
        _, solo = self.share('b-solo', 'one', 'correlation', ['second'], 'solo.json')
        self.assertNotEqual(solo['namespace'], one_artifact['namespace'])
        solo_tokens = {m.group(0) for m in TOKEN_RE.finditer(self.frame_text(solo, 'second'))}
        self.assertFalse(solo_tokens & one_tokens)

    def test_markers_cannot_be_derived_from_source_text_or_identity(self):
        _, artifact = self.share('b-opaque', 'one', 'correlation', ['first', 'second'], 'bo.json')
        payload = json.dumps(artifact, ensure_ascii=False)
        for leaked in SOURCE_IDS + (PRIVATE_TEXT, 'π-732', 'recipe', 'ref'):
            self.assertNotIn(leaked, payload)
        for source_id in SOURCE_IDS:
            self.assertNotIn(source_id, artifact['namespace'])
        # No marker lookup table: frames only carry rendered text.
        self.assertEqual(sorted(artifact['frames'][0]), ['frame', 'text'])

    # --- hard constraint: code-point spans keep public order around pi ------
    def test_public_spans_keep_python_code_point_order_and_boundaries(self):
        self.assertEqual(len(PRIVATE_TEXT), 9)  # pi is one Python character
        run(self.db, 'tracevalues', {
            'op': 'value', 'event_id': 'public-pi', 'id': 'public-pi',
            'incident': 'one', 'classification': 'public', 'text': 'α-πβ',
        })
        recipe = [
            {'literal': 'x='},
            {'ref': 'public-pi', 'start': 0, 'end': 3},   # α-π
            {'literal': ' y='},
            {'ref': 'private-a', 'start': 0, 'end': 4},   # RID-
            {'ref': 'private-a', 'start': 4, 'end': 9},   # π-732
            {'ref': 'public-pi', 'start': 3, 'end': 4},   # β
        ]
        rendered = run(self.db, 'transcript', {
            'op': 'frame', 'event_id': 'pi-frame', 'id': 'pi-frame',
            'incident': 'one', 'recipe': recipe,
        })
        self.assertEqual(rendered['text'], 'x=α-π y=RID-π-732β')
        _, structure = self.share('pi-structure', 'one', 'structure', ['pi-frame'], 'pis.json')
        self.assertEqual(self.frame_text(structure, 'pi-frame'),
                         'x=α-π y=' + REDACTION + REDACTION + 'β')
        _, correlated = self.share('pi-correlation', 'one', 'correlation', ['pi-frame'], 'pic.json')
        ns = correlated['namespace']
        self.assertEqual(self.frame_text(correlated, 'pi-frame'),
                         'x=α-π y=[%s-S1][%s-S1]β' % (ns, ns))

    # --- hard constraint: broken frames abort the whole export --------------
    def test_unknown_and_cross_event_frames_abort_without_artifact(self):
        for name, incident, frames in [
            ('unknown.json', 'one', ['first', 'no-such-frame']),
            ('cross.json', 'one', ['first', 'third']),
        ]:
            destination = self.dir / name
            result = run(self.db, 'transcript', {
                'op': 'share', 'event_id': 'broken-' + name, 'id': 'broken-' + name,
                'incident': incident, 'mode': 'structure', 'frames': frames,
                'destination': str(destination),
            })
            self.assertEqual(result['decision'], 'share_unavailable', result)
            self.assertFalse(destination.exists())

    def test_unknown_source_and_out_of_bounds_and_corrupt_recipe_abort(self):
        self.db.execute(
            "INSERT INTO trace_frames VALUES (?,?,?,?)",
            ('bad-source', 'one', json.dumps([{'ref': 'ghost', 'start': 0, 'end': 1}]), 'x'),
        )
        self.db.execute(
            "INSERT INTO trace_frames VALUES (?,?,?,?)",
            ('bad-span', 'one', json.dumps([{'ref': 'private-a', 'start': 4, 'end': 99}]), 'x'),
        )
        self.db.execute(
            "INSERT INTO trace_frames VALUES (?,?,?,?)",
            ('bad-json', 'one', '{not json', 'x'),
        )
        for fid in ('bad-source', 'bad-span', 'bad-json'):
            destination = self.dir / (fid + '.json')
            result = run(self.db, 'transcript', {
                'op': 'share', 'event_id': 'event-' + fid, 'id': 'share-' + fid,
                'incident': 'one', 'mode': 'correlation', 'frames': ['first', fid],
                'destination': str(destination),
            })
            self.assertEqual(result['decision'], 'share_unavailable', (fid, result))
            self.assertFalse(destination.exists())  # valid first frame is never partially exported

    def test_malformed_and_conflicting_requests_are_denied(self):
        good = {'op': 'share', 'event_id': 'ok', 'id': 'ok', 'incident': 'one',
                'mode': 'structure', 'frames': ['first'], 'destination': str(self.dir / 'ok.json')}
        run(self.db, 'transcript', good)
        bad_requests = [
            dict(good, event_id='dup-id', destination=str(self.dir / 'dup.json')),  # reuse share id
            dict(good, event_id='dup-dest', id='other-id'),                    # reuse destination
            dict(good, event_id='bad-mode', id='m1', mode='raw'),
            dict(good, event_id='bad-frames', id='m2', frames=[]),
            dict(good, event_id='bad-frames2', id='m3', frames=['first', 'first']),
            dict(good, event_id='bad-dest', id='m4', destination=''),
        ]
        for request in bad_requests:
            self.assertEqual(run(self.db, 'transcript', request)['decision'], 'share_denied', request)

    # --- hard constraint: completed exports are stable when read again ------
    def test_re_read_is_byte_identical_and_tampering_is_rejected(self):
        destination, artifact = self.share('reread', 'one', 'structure', ['first', 'second'], 'r.json')
        verified = run(self.db, 'transcript', {'op': 'share-read', 'event_id': 'read-1', 'id': 'reread'})
        self.assertEqual(verified['decision'], 'share_verified')
        self.assertEqual(verified['artifact'], artifact)
        # A fresh export request on a rebuilt ledger produces the same bytes.
        rebuilt = connect(':memory:')
        self.addCleanup(rebuilt.close)
        feed(rebuilt, load_fragments())
        again = str(self.dir / 'r_again.json')
        run(rebuilt, 'transcript', {
            'op': 'share', 'event_id': 'event-reread', 'id': 'reread', 'incident': 'one',
            'mode': 'structure', 'frames': ['first', 'second'], 'destination': again,
        })
        self.assertEqual(Path(again).read_bytes(), Path(destination).read_bytes())
        # Tamper with the attachment after completion.
        Path(destination).write_text(Path(destination).read_text(encoding='utf8').replace(REDACTION, 'RID-π-732'),
                                     encoding='utf8')
        tampered = run(self.db, 'transcript', {'op': 'share-read', 'event_id': 'read-2', 'id': 'reread'})
        self.assertEqual(tampered['decision'], 'share_unavailable')
        self.assertEqual(tampered['reason'], 'artifact_changed')
        unknown = run(self.db, 'transcript', {'op': 'share-read', 'event_id': 'read-3', 'id': 'ghost'})
        self.assertEqual(unknown['decision'], 'share_unknown')

    # --- ledger replay covers the new share tables and artifacts -------------
    def test_full_replay_rebuilds_share_ledger_and_exports(self):
        db_path = str(self.dir / 'ledger.sqlite')
        ledger = connect(db_path)
        for event in load_fragments():
            run(ledger, event['command'], event['input'])
        destinations = [str(self.dir / ('replay-%d.json' % i)) for i in range(3)]
        requests = [
            ('s1', 'one', 'structure', ['first', 'second'], destinations[0]),
            ('s2', 'one', 'correlation', ['first', 'second'], destinations[1]),
            ('s3', 'two', 'correlation', ['third'], destinations[2]),
        ]
        for i, (sid, incident, mode, frames, dest) in enumerate(requests):
            run(ledger, 'transcript', {'op': 'share', 'event_id': 'ev-' + sid, 'id': sid,
                                       'incident': incident, 'mode': mode, 'frames': frames,
                                       'destination': dest})
        before = {d: Path(d).read_bytes() for d in destinations}
        report = replay(db_path)
        self.assertTrue(report['verified'], report['divergences'])
        for dest, blob in before.items():  # replay rewrites identical artifacts
            self.assertEqual(Path(dest).read_bytes(), blob)
        ledger.close()

    def test_cli_share_smoke_and_fixture_shapes(self):
        # The committed share fixture must describe exactly the two contracts,
        # the third-event export, re-reads and the two broken-frame requests.
        fixture = json.loads(FRAGMENT_FIXTURE.read_text(encoding='utf8'))
        ops = [(x['input']['op'], x['input'].get('mode')) for x in fixture]
        self.assertEqual(ops.count(('share', 'structure')), 2)   # contract A + broken structure
        self.assertEqual(ops.count(('share', 'correlation')), 3)  # contract B + third + broken
        self.assertEqual(ops.count(('share-read', None)), 2)
        # End-to-end through the documented CLI against a throwaway database.
        db_path = self.dir / 'cli.sqlite'
        out_dir = self.dir / 'cli-shares'
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'draincourt', 'transcript',
             '--db', str(db_path), '--fixture', str(ROOT / 'fixtures' / 'transcript_fragments.json')],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn('frame_rendered', proc.stdout)
        share_input = json.loads(FRAGMENT_FIXTURE.read_text(encoding='utf8'))
        redirected = []
        for item in share_input:
            data = dict(item['input'])
            if data['op'] == 'share':
                data = dict(data, destination=str(out_dir / Path(data['destination']).name))
            redirected.append({'command': item['command'], 'input': data})
        cli_fixture = self.dir / 'cli_shares.json'
        cli_fixture.write_text(json.dumps(redirected, ensure_ascii=False), encoding='utf8')
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'draincourt', 'transcript',
             '--db', str(db_path), '--fixture', str(cli_fixture)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        decisions = {d['decision'] for d in json.loads(proc.stdout)}
        self.assertIn('share_exported', decisions)
        self.assertIn('share_verified', decisions)
        self.assertIn('share_unavailable', decisions)
        self.assertFalse((out_dir / 'must_not_be_written_cross.json').exists())
        self.assertFalse((out_dir / 'must_not_be_written_unknown.json').exists())
        contract_a = json.loads((out_dir / 'contract_a_structure.json').read_text(encoding='utf8'))
        self.assertEqual(
            next(f['text'] for f in contract_a['frames'] if f['frame'] == 'first'),
            'pod=[遮盖] phase=waiting tag=RID- suffix=[遮盖]',
        )
        contract_b = json.loads((out_dir / 'contract_b_correlation.json').read_text(encoding='utf8'))
        texts = {f['frame']: f['text'] for f in contract_b['frames']}
        self.assertIn('tag=RID-', texts['first'])
        self.assertEqual(len(TOKEN_RE.findall(texts['first'])), 2)
        self.assertEqual(TOKEN_RE.findall(texts['first'])[0], TOKEN_RE.findall(texts['second'])[0])
        self.assertNotEqual(TOKEN_RE.findall(texts['second'])[0], TOKEN_RE.findall(texts['second'])[1])
        third = json.loads((out_dir / 'contract_b_third_event.json').read_text(encoding='utf8'))
        self.assertNotEqual(third['namespace'], contract_b['namespace'])

    def test_private_classification_constant_matches_registry_levels(self):
        row = one(self.db, "SELECT * FROM trace_values WHERE id='private-a'")
        self.assertEqual(row['classification'], PRIVATE)
