"""Validate a reviewer's explicit action and every assembly input, including absence."""
import hashlib
import json
import argparse
from pathlib import Path

INPUTS = ('reviewed_timeline.json', 'sections-content.json', 'source.json', 'subtitle-corrections.json')
ACTION = 'render continuous segment clips'

def snapshot(job):
    job = Path(job)
    return {name: hashlib.sha256((job/name).read_bytes()).hexdigest() if (job/name).is_file() else None
            for name in INPUTS}

def verify(job):
    job = Path(job)
    path = job/'content-review-approval.json'
    data = path.read_bytes()
    approval = json.loads(data.decode('utf-8-sig'))
    assert approval.get('status') == 'PASS' and approval.get('reviewer') and approval.get('evidence_inspected'), 'Independent content review required'
    assert ACTION in approval.get('approved_actions', []), 'Reviewer has not approved segment rendering'
    expected = approval.get('input_sha256')
    assert isinstance(expected, dict) and set(expected) == set(INPUTS), 'Review must bind every assembly input and optional absence'
    actual = snapshot(job)
    assert all(actual[name] for name in INPUTS[:3]), 'Required assembly inputs missing'
    assert actual == expected, 'An assembly input changed after review'
    return approval, actual, hashlib.sha256(data).hexdigest()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    try:
        approval, inputs, receipt_sha = verify(args.job)
        print(json.dumps({'status': 'PASS', 'reviewer': approval['reviewer'],
                          'review_receipt_sha256': receipt_sha, 'input_sha256': inputs,
                          'scope': 'Explicit reviewed action and unchanged inputs; no new content judgment'}, ensure_ascii=False))
    except (AssertionError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
