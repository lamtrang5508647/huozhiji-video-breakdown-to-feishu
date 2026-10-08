"""Compare a fresh full-detail Docx XML snapshot to the saved chapter registry.

Read-only, never overwrites registry or remote content. A mismatch can be a human
edit and requires reconciliation, not automatic replacement with an old draft.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def canonical(node):
    return [node.tag, sorted((k, v) for k, v in node.attrib.items() if k != 'id'),
            node.text or '', node.tail or '', [canonical(c) for c in node]]


def check(registry, snapshot):
    if not snapshot.get('ok'):
        raise ValueError('Snapshot is not a successful CLI response')
    doc = snapshot['data']['document']
    groups = [g for g in registry['groups'].values() if g['doc_token'] == doc['document_id']]
    if len(groups) != 1:
        raise ValueError('Snapshot document_id does not uniquely match the registry')
    blocks = list(ET.fromstring('<root>' + doc['content'] + '</root>'))
    if any(b.tag == 'fragment' for b in blocks):
        raise ValueError('A full-detail full-document XML snapshot is required')
    result, used_anchors = [], set()
    for video_id, video in groups[0]['videos'].items():
        anchors = [video['chapter_root_block_id'], *video['segment_block_ids'].values()]
        if len(anchors) != len(set(anchors)) or used_anchors.intersection(anchors):
            raise ValueError(f'{video_id}: duplicate chapter/segment anchors')
        used_anchors.update(anchors)
        indexes = [i for i, b in enumerate(blocks) if b.get('id') == video['chapter_root_block_id']]
        if len(indexes) != 1:
            raise ValueError(f'{video_id}: H1 anchor missing or duplicated')
        start = indexes[0]
        end = next((i for i in range(start + 1, len(blocks)) if blocks[i].tag == 'h1'), len(blocks))
        chapter = blocks[start:end]
        if chapter[0].tag != 'h1' or ''.join(chapter[0].itertext()) != video['source_title']:
            raise ValueError(f'{video_id}: original title changed')
        if len(chapter) < 2 or chapter[1].tag != 'p':
            raise ValueError(f'{video_id}: source metadata missing directly below H1')
        metadata = ''.join(chapter[1].itertext())
        links = {a.get('href') for a in chapter[1].iter('a')}
        url = video.get('source_url')
        id_matches = re.search(r'(?<![A-Za-z0-9_-])' + re.escape(video_id) + r'(?![A-Za-z0-9_-])', metadata)
        if not id_matches and not (video_id == url and url in links):
            raise ValueError(f'{video_id}: stable source identity missing from chapter metadata')
        if url and url not in links:
            raise ValueError(f'{video_id}: source URL does not match chapter metadata')
        h2s = [b.get('id') for b in chapter if b.tag == 'h2']
        if not set(video['segment_block_ids'].values()) <= set(h2s):
            raise ValueError(f'{video_id}: segment anchors changed')
        digest = hashlib.sha256(json.dumps([canonical(b) for b in chapter],
                  ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        if digest != video['content_sha256']:
            raise ValueError(f'{video_id}: chapter content/style differs; preserve and reconcile possible human edits')
        result.append({'video_id': video_id, 'content_sha256': digest, 'h2_count': len(video['segment_block_ids'])})
    return {'status': 'PASS', 'doc_token': doc['document_id'], 'revision': doc['revision_id'],
            'chapters': result, 'scope': 'Supplied snapshot only; fetch live before any update', 'mutations': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registry', required=True, type=Path)
    p.add_argument('--snapshot', required=True, type=Path)
    a = p.parse_args()
    try:
        result = check(json.loads(a.registry.read_text(encoding='utf-8-sig')),
                       json.loads(a.snapshot.read_text(encoding='utf-8-sig')))
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc), 'mutations': 0}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
