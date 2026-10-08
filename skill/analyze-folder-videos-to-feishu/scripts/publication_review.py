"""Bind the supervising decision and contextual audit before any v9 write."""
import hashlib,json,pathlib
from phrase_annotation import PROFILE,require,validate_supervisor_review
from context_review import validate_context_review

def validate_publication_review(review,analysis,analysis_sha256,manifest_sha256,context_path):
    require(analysis.get('annotation_profile')==PROFILE,
            'New publication/repair must use v9 annotations and contextual review')
    validate_supervisor_review(review,analysis_sha256,manifest_sha256,
                               [unit['id'] for unit in analysis['units']])
    path=pathlib.Path(context_path)
    require(path.is_file(),'Current context-review.json is required; a global reviewed declaration is insufficient')
    raw=path.read_bytes()
    require(review.get('context_review_sha256')==hashlib.sha256(raw).hexdigest(),
            'Supervising approval must bind the exact context-review.json')
    record=validate_context_review(json.loads(raw),analysis,analysis_sha256)
    require(record['unresolved_count']==0,
            'Context audit still has unresolved decisions; supervisor must resolve before publishing')
    return record
