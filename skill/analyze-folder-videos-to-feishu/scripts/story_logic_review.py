"""Validate a separately authored story-logic review before script delivery.

This validates a review record's completeness, exact quotations and hash binding.
It does not decide whether the story is logical, the reviewers are genuinely
independent, a product claim is true, or a video has been recorded.

Draft inputs (one of):

* ``{"groups": [{"C": {"parts": [{"text": "..."}, ...]}, "D": {...}}, ...]}``
  Each version's spoken text is the concatenation of its ``parts[].text`` in
  group order, without inserting characters. Every group must contain the same
  version keys. Other group fields (section, times, shot plans) are preserved by
  hashing the entire draft file.
* ``{"versions": {"C": "完整口播", "D": "另一份完整口播"}}`` for a generic
  text-only draft. In this mode, each version has one referenceable group.

Review schema ``story-logic-review/v1``:

```
{
  "profile": "story-logic-review/v1",
  "draft_sha256": "<sha256 of exact draft file bytes>",
  "versions": {
    "C": {
      "version": "C", "draft_sha256": "<same draft hash>",
      "spoken_text_sha256": "<sha256 of exact concatenated UTF-8 spoken text>",
      "author_id": "writer", "reviewer_id": "independent reviewer",
      "review_mode": "independent",
      "checks": {
        "role_and_function": {"verdict": "pass", "reason": "具体理由",
          "quotes": [{"text": "原样的口播片段", "group_index": 1}]},
        ... six other named checks ...
      },
      "unresolved_findings": []
    }
  }
}
```

``group_index`` is optional; if present it is a one-based group number and the
quote must occur in that exact group. An applicable ``pass`` or
``needs_revision`` check needs at least one literal quote from that version.
``not_applicable`` may omit quotes but must explain why, so solo scripts are not
forced to invent a team, a livestream or fans. The independent whole-script
judgment must be ``pass`` with a quote. Unresolved or stale reviews fail closed.
If independent review is unavailable, ``review_mode`` may instead be
``same_agent_second_pass`` with the same author/reviewer identity and a specific
``second_pass_description``. This is explicitly reported as not independent;
all seven individually cited checks are still required. Review identity and
whether anyone actually performed the stated actions are not machine-verifiable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

PROFILE = 'story-logic-review/v1'
REQUIRED_CHECKS = (
    'role_and_function',
    'actions_and_causality',
    'relationships_and_channel',
    'experience_and_attribution',
    'source_structure_and_test_facts',
    'shots_and_timing',
    'overall_comprehension',
)
VALID_VERDICTS = {'pass', 'needs_revision', 'not_applicable'}
_SHA = re.compile(r'^[0-9a-f]{64}$')
_GENERIC = {'pass', 'ok', 'yes', 'n/a', 'na', 'none', '通过', '合格', '已检查', '已审核', '无', '不适用', '同上'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def spoken_sha256(text):
    return sha256_bytes(text.encode('utf-8'))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json_bytes(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    require(isinstance(data, dict), 'JSON root must be an object')
    return raw, data


def _identity(value, context):
    require(isinstance(value, str) and value.strip(), context + ' required')
    identity = ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()
    require(identity not in _GENERIC, context + ' must identify an actual author/reviewer')
    return identity


def _reason(value, context):
    require(isinstance(value, str) and len(value.strip()) >= 6, context + ': concrete reason required')
    require(value.strip().casefold() not in _GENERIC, context + ': generic reason is insufficient')


def draft_versions(draft):
    """Return {version: [spoken group strings]} for the two supported shapes."""
    require(isinstance(draft, dict), 'Draft must be an object')
    has_groups = 'groups' in draft
    has_versions = 'versions' in draft
    require(has_groups != has_versions, 'Provide exactly one of groups or versions')
    if has_versions:
        versions = draft['versions']
        require(isinstance(versions, dict) and versions, 'Draft versions must be a nonempty mapping')
        for key, text in versions.items():
            require(isinstance(key, str) and key.strip() and key == key.strip(), 'Invalid version name')
            require(isinstance(text, str) and text.strip(), key + ': spoken text required')
        return {key: [text] for key, text in versions.items()}

    groups = draft['groups']
    require(isinstance(groups, list) and groups, 'Draft groups must be a nonempty list')
    require(all(isinstance(group, dict) for group in groups), 'Each draft group must be an object')

    def keys(group):
        return {k for k, v in group.items() if isinstance(k, str) and isinstance(v, dict) and 'parts' in v}

    version_keys = keys(groups[0])
    require(bool(version_keys), 'No group versions with parts found')
    require(all(k.strip() and k == k.strip() for k in version_keys), 'Invalid version name')
    result = {k: [] for k in sorted(version_keys)}
    for i, group in enumerate(groups, 1):
        require(keys(group) == version_keys, f'Group {i}: inconsistent version set')
        for version in result:
            parts = group[version]['parts']
            require(isinstance(parts, list) and parts, f'{version} group {i}: nonempty parts required')
            require(all(isinstance(part, dict) and isinstance(part.get('text'), str) and part['text']
                        for part in parts), f'{version} group {i}: each part needs text')
            text = ''.join(part['text'] for part in parts)
            require(bool(text.strip()), f'{version} group {i}: spoken text is empty')
            result[version].append(text)
    return result


def validate_story_review(draft_path, review_path, expected_versions=None):
    """Validate review file against exact draft bytes and per-version text.

    ``expected_versions`` may explicitly select a subset that is being
    delivered. If omitted, the review must cover every version in the draft.
    """
    draft_raw, draft = read_json_bytes(draft_path)
    _, review = read_json_bytes(review_path)
    actual_hash = sha256_bytes(draft_raw)
    versions = draft_versions(draft)
    require(review.get('profile') == PROFILE, 'Unknown story review profile')
    require(isinstance(review.get('draft_sha256'), str) and _SHA.fullmatch(review['draft_sha256']),
            'draft_sha256 must be lowercase SHA256')
    require(review['draft_sha256'] == actual_hash, 'Stale review: draft file hash differs')
    if expected_versions is None:
        expected = set(versions)
    else:
        require(isinstance(expected_versions, (list, tuple)) and expected_versions,
                'Expected version list must not be empty')
        require(all(isinstance(v, str) and v.strip() and v == v.strip() for v in expected_versions),
                'Invalid expected version')
        require(len(expected_versions) == len(set(expected_versions)), 'Duplicate expected version')
        expected = set(expected_versions)
        require(expected <= set(versions), 'Expected version is missing from draft')
    records = review.get('versions')
    require(isinstance(records, dict) and set(records) == expected,
            'Review must cover exactly the versions being delivered')
    checked = []
    for version in sorted(expected):
        context = 'Version ' + version
        item = records[version]
        require(isinstance(item, dict), context + ': review object required')
        require(item.get('version') == version, context + ': version label mismatch')
        require(item.get('draft_sha256') == actual_hash, context + ': stale or cross-draft binding')
        spoken_groups = versions[version]
        spoken = ''.join(spoken_groups)
        require(item.get('spoken_text_sha256') == spoken_sha256(spoken),
                context + ': spoken text hash mismatch')
        author = _identity(item.get('author_id'), context + ' author_id')
        reviewer = _identity(item.get('reviewer_id'), context + ' reviewer_id')
        mode = item.get('review_mode')
        require(mode in ('independent', 'same_agent_second_pass'), context + ': explicit review_mode required')
        if mode == 'independent':
            require(author != reviewer, context + ': independent author and reviewer must differ')
        else:
            require(author == reviewer, context + ': same-agent second pass must identify the same person/agent')
            _reason(item.get('second_pass_description'), context + ' second_pass_description')
        unresolved = item.get('unresolved_findings')
        require(isinstance(unresolved, list), context + ': unresolved_findings must be a list')
        require(not unresolved, context + ': unresolved findings block delivery')
        checks = item.get('checks')
        require(isinstance(checks, dict) and set(checks) == set(REQUIRED_CHECKS),
                context + ': all seven named checks are required')
        for key in REQUIRED_CHECKS:
            check = checks[key]
            scope = context + ' ' + key
            require(isinstance(check, dict), scope + ': check object required')
            verdict = check.get('verdict')
            require(isinstance(verdict, str) and verdict in VALID_VERDICTS, scope + ': invalid verdict')
            _reason(check.get('reason'), scope)
            quotes = check.get('quotes', [])
            require(isinstance(quotes, list), scope + ': quotes must be a list')
            if verdict != 'not_applicable':
                require(bool(quotes), scope + ': exact spoken quotation required')
            for quote in quotes:
                require(isinstance(quote, dict) and isinstance(quote.get('text'), str)
                        and quote['text'].strip(), scope + ': invalid quotation')
                text = quote['text']
                require(text in spoken, scope + ': quotation is absent from this version')
                if 'group_index' in quote:
                    index = quote['group_index']
                    require(type(index) is int and 1 <= index <= len(spoken_groups),
                            scope + ': invalid group_index')
                    require(text in spoken_groups[index - 1], scope + ': quotation is absent from cited group')
            require(verdict != 'needs_revision', scope + ': needs_revision blocks delivery')
            if key == 'overall_comprehension':
                require(verdict == 'pass', scope + ': independent whole-script judgment must pass')
        checked.append({'version': version, 'spoken_text_sha256': spoken_sha256(spoken),
                        'groups': len(spoken_groups), 'checks': len(REQUIRED_CHECKS),
                        'review_mode': mode,
                        'independence': 'declared_independent' if mode == 'independent' else 'not_independent'})
    return {'status': 'PASS', 'profile': PROFILE, 'draft_sha256': actual_hash, 'versions': checked,
            'semantic_truth': 'not_assessed',
            'scope': 'Record completeness, exact text/hash binding and stated independent identities only; no automatic judgment of reasoning, independence or factual truth.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', required=True, type=Path)
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--versions', help='Comma-separated versions being delivered; default is every draft version')
    args = parser.parse_args(argv)
    try:
        expected = [v.strip() for v in args.versions.split(',')] if args.versions is not None else None
        result = validate_story_review(args.draft, args.review, expected)
    except (ValueError, KeyError, TypeError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc), 'semantic_truth': 'not_assessed'}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
