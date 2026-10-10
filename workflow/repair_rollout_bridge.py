"""Adopt current exported rollouts into the original full-shot audit lifecycle."""
import json
from pathlib import Path

from .core.io import sha256, write_json


def adopt(root, items, registered, queue):
    """Read-only queue reconciliation; malformed peers never block other products."""
    by_product = {r['product_id']: r for r in registered}
    with queue.connect() as connection:
        current = {row['item_id']: row['current_job'] for row in
                   connection.execute('SELECT item_id,current_job FROM production_items')}
    adopted = []
    for path in sorted((Path(root) / 'storyboard_rollout/requests').glob('*/deployment_request.json')):
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
            if request.get('state') != 'completed':
                continue
            plan = json.loads(Path(request['manifest_path']).read_text(encoding="utf-8"))
            product = plan.get('root_job_id')
            original = by_product.get(product)
            if not original or current.get(product) != request['job_id']:
                # Historical rollouts cannot replace the current product/audit.
                continue
            if plan.get('id') != request['job_id'] or plan.get('user_repair_request_id') != original['request_id']:
                raise ValueError('rollout_manifest_request_identity_mismatch')
            key = original['request_id']
            old = items.get(key, {})
            if old.get('new_job_id') == request['job_id']:
                continue
            if old.get('state') in ('preparing', 'prepared', 'rendering', 'auditing', 'completed'):
                # Even a changed current card cannot reset an in-flight review.
                # Its own version validator will reconcile/isolate the old work.
                continue
            if sha256(request['film_path']) != request['film_sha256']:
                raise ValueError('export_changed')
            items[key] = {**original, 'state': 'rendering',
                          'new_job_id': request['job_id'],
                          'manifest_path': request['manifest_path'],
                          'rollout_request_path': str(path),
                          'prior_repair_outcome': old}
            adopted.append(key)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            try:
                write_json(path.parent / 'repair_bridge_error.json',
                           {'state': 'isolated', 'reason': str(exc)[:500],
                            'error_type': type(exc).__name__, 'request_path': str(path)})
            except OSError:
                # A broken/unwritable peer folder must not starve other peers.
                pass
    return adopted
