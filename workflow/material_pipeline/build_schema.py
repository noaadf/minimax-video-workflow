"""Generate the portable JSON Schema contract; core.validate checks file evidence."""
from pathlib import Path
from .core import write_json


def obj(props, required=None):
    return {'type': 'object', 'properties': props, 'required': required or list(props), 'additionalProperties': False}


S = {'type': 'string', 'minLength': 1}
N = {'type': 'number', 'minimum': 0}
H = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
P = {'type': 'string', 'pattern': '^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$'}
DATE = {'type': 'string', 'format': 'date'}


def arr(item): return {'type': 'array', 'items': item}
def enum(*values): return {'enum': list(values)}
def ref(name): return {'$ref': '#/$defs/' + name}


def schema():
    definitions = {
        'event': obj({'entity': S, 'action': S, 'date': DATE, 'object': S}),
        'source': obj({'id': S, 'url': {'type': 'string', 'format': 'uri'}, 'title': S,
                       'publisher': S, 'role': enum('primary', 'secondary', 'craft_reference'),
                       'published': {'anyOf': [DATE, {'type': 'null'}]}, 'date_evidence': S,
                       'archive_path': P, 'sha256': H, 'text_path': P, 'text_sha256': H,
                       'retrieved_at': {'type': 'string', 'format': 'date-time'}, 'acquisition': S}),
        'evidence': obj({'source_id': S, 'quote': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                         'start': {'type': 'integer', 'minimum': 0}, 'end': {'type': 'integer', 'minimum': 1}}),
        'fact': obj({'id': S, 'text': S, 'subject': S, 'predicate': S, 'object': S,
                     'modality': enum('observed', 'reported', 'planned', 'hypothesis'),
                     'fingerprint': H, 'verification': enum('source_supported', 'uncertain'),
                     'evidence': {'type': 'array', 'items': ref('evidence'), 'minItems': 1}}),
        'rights': obj({'status': enum('unknown', 'restricted', 'licensed', 'public_domain', 'original'),
                       'owner': S, 'evidence_path': {'anyOf': [P, {'type': 'null'}]}, 'note': S}),
        'asset': obj({'id': S, 'source_id': S, 'url': {'type': 'string', 'format': 'uri'}, 'path': P,
                      'sha256': H, 'bytes': {'type': 'integer', 'minimum': 1},
                      'kind': enum('video', 'image', 'audio'), 'duration': N,
                      'use': enum('research_only', 'production'), 'rights': ref('rights')}),
        'keyframe': obj({'at': N, 'path': P, 'sha256': H}),
        'shot': obj({'id': S, 'asset_id': S, 'start': N, 'end': N,
                     'status': enum('candidate', 'visually_reviewed'),
                     'observation': {'anyOf': [S, {'type': 'null'}]},
                     'keyframes': {'type': 'array', 'items': ref('keyframe'), 'minItems': 1},
                     'boundary_method': S}),
        'transcript': obj({'status': enum('pending', 'not_applicable', 'automatic_unreviewed', 'reviewed'),
                           'asset_id': S, 'path': {'anyOf': [P, {'type': 'null'}]},
                           'sha256': {'anyOf': [H, {'type': 'null'}]}, 'method': S, 'limitations': arr(S)}),
        'beat': obj({'id': S, 'purpose': S, 'fact_ids': arr(S), 'original_narration': S,
                     'visual_plan': S}),
        'editorial': obj({'audience': S, 'angle': S, 'beats': arr(ref('beat')),
                          'cautions': arr(S), 'verification_scope': S})
    }
    result = obj({'schema_version': {'const': 1}, 'content_id': {'type': 'string', 'pattern': '^material_[0-9a-f]{24}$'},
                  'title': S, 'event': ref('event'), 'sources': {'type': 'array', 'items': ref('source'), 'minItems': 1},
                  'facts': {'type': 'array', 'items': ref('fact'), 'minItems': 1}, 'assets': arr(ref('asset')),
                  'shots': arr(ref('shot')), 'transcript': ref('transcript'), 'editorial': ref('editorial'),
                  'collection_pending': arr(S), 'production_pending': arr(S),
                  'production_asset_plan_reviewed': {'type': 'boolean'}})
    result.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                   '$id': 'urn:minimax:material-package:1', '$defs': definitions})
    return result


if __name__ == '__main__':
    write_json(Path(__file__).with_name('package.schema.json'), schema())
