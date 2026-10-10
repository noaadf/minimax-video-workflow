"""Verified independent generation groups; a group is an exclusive queue capability."""
import json
import datetime as dt


def check_lease(group, now=None, minimum_seconds=600, require_expiry=False):
    """Fail before dispatch when a timed Notebook cannot safely fit a new shot."""
    expiry = group.get('expires_at')
    if not expiry:
        if require_expiry and group.get('type') == 'ascend_ssh':
            raise ValueError('Notebook expiry must be verified before controller dispatch')
        return
    if not isinstance(expiry,str):
        raise ValueError('Notebook expiry must be a timezone-aware ISO timestamp')
    deadline = dt.datetime.fromisoformat(expiry.replace('Z', '+00:00'))
    if deadline.tzinfo is None:
        raise ValueError('Notebook expiry requires a timezone')
    current = now or dt.datetime.now(dt.timezone.utc)
    if (deadline - current).total_seconds() < minimum_seconds:
        raise ValueError('Notebook expired or too close to expiry for a new shot')

def unavailable_reasons(group, now=None):
    """Current scheduling eligibility, distinct from past model validation."""
    reasons=[]
    if group.get('type')!='ascend_ssh':return ['unsupported generation group']
    if group.get('ready') is not True:reasons.append('generation backend not validated ready')
    if group.get('ready_for_dispatch') is False:reasons.append('resource explicitly unavailable for dispatch')
    try:
        check_lease(group,now=now,require_expiry=True)
    except ValueError as exc:
        reasons.append(str(exc))
    return reasons


def _invalid_device_ids(ids):
    return not isinstance(ids, list) or not ids or len(ids) != len(set(ids))


def _invalid_resource_shape(total, physical):
    return (type(total) is not int or type(physical) is not int or physical <= 0
            or total <= 0 or total % physical != 0)


def _invalid_device_range(ids, total):
    return any(type(i) is not int or not 0 <= i < total for i in ids)


def _require_partitions(previous, group):
    for member in [*previous, group]:
        ids = member.get('logical_device_ids')
        total = member.get('resource_logical_devices')
        physical = member.get('resource_physical_cards')
        port = member.get('api_port')
        if (_invalid_device_ids(ids)
                or _invalid_resource_shape(total, physical)
                or _invalid_device_range(ids, total)
                or len(ids) != member.get('logical_devices')
                or type(port) is not int or not 1 <= port <= 65535
                or not member.get('remote_root')):
            raise ValueError('shared resource requires explicit verified service/device partition')
        chips = total // physical
        cards = {i // chips for i in ids}
        if len(cards) != member.get('physical_cards') or len(ids) != len(cards) * chips:
            raise ValueError('service partition must own complete physical cards')


def _require_distinct_services(previous, group, endpoint):
    for old in previous:
        if ((old['host'], old['port']) != endpoint
                or old['resource_logical_devices'] != group['resource_logical_devices']
                or old['resource_physical_cards'] != group['resource_physical_cards']
                or old['api_port'] == group['api_port']
                or old['remote_root'].rstrip('/') == group['remote_root'].rstrip('/')
                or set(old['logical_device_ids']) & set(group['logical_device_ids'])):
            raise ValueError('pool service partitions overlap or describe inconsistent resources')


def _require_pool_identity(group, capabilities, endpoints):
    if group.get('type') != 'ascend_ssh':
        raise ValueError('pool members must be ascend_ssh')
    capability = group.get('capability')
    endpoint = (group.get('host'), group.get('port'))
    resource = group.get('resource_id')
    if not capability or not resource:
        raise ValueError('pool groups need capability and verified resource_id')
    if capability in capabilities or (endpoint in endpoints and endpoints[endpoint] != resource):
        raise ValueError('pool aliases overlap; distinct capabilities cannot share a device group')
    return capability, endpoint, resource


def members(backend, now=None):
    if backend.get('type') != 'pool':
        if backend.get('type')=='ascend_ssh' and unavailable_reasons(backend,now):return []
        return [backend]
    groups = backend.get('groups', [])
    if not groups:
        raise ValueError('backend pool is empty')
    capabilities, endpoints, resources = set(), {}, {}
    ready = []
    for group in groups:
        capability, endpoint, resource = _require_pool_identity(group, capabilities, endpoints)
        previous = resources.get(resource, [])
        if previous:
            # Expanded Notebooks may host two independently accepted services. They
            # share SSH, but must have separate devices, API ports and task roots.
            _require_partitions(previous, group)
            _require_distinct_services(previous, group, endpoint)
        capabilities.add(capability); endpoints[endpoint] = resource
        resources.setdefault(resource, []).append(group)
        if not unavailable_reasons(group,now):
            ready.append(group)
    return ready


def ready(backend, now=None):
    if backend.get('type') == 'assets': return True
    if backend.get('type') == 'pool': return bool(members(backend,now))
    return backend.get('type') == 'ascend_ssh' and not unavailable_reasons(backend,now)


def supports_shot(group, shot):
    # A worker accepting an image field is not proof that its loaded model uses it.
    refs=shot.get('reference_images', [])
    if shot.get('generation_contract') is not None:
        from .first_frame import CONTRACT, require_profile
        if shot['generation_contract'] != CONTRACT or len(refs) != 1:
            return False
        try:
            require_profile(group)
        except (ValueError, KeyError, OSError, TypeError):
            return False
        return group.get('supports_reference_images') is True
    if not refs:return True
    return (group.get('supports_reference_images') is True
            and len(refs)<=group.get('max_reference_images',2)
            and len(refs) in group.get('validated_reference_image_counts',[1,2]))


def assignments(backend, shots, now=None):
    groups = members(backend,now)
    loads = {g['capability']: 0 for g in groups if g.get('capability')}
    result = {}
    for shot in shots:
        if shot.get('source_path'): continue
        eligible = [g for g in groups if supports_shot(g, shot)]
        if not eligible:
            raise ValueError('no verified generation group supports shot ' + shot['id'] +
                             (' with reference images' if shot.get('reference_images') else ''))
        group = min(eligible, key=lambda g: loads[g['capability']])
        result[shot['id']] = group
        loads[group['capability']] += max(4, shot['duration_seconds'])
    return result


def rebalance_unstarted(queue, job_id, pool):
    """Audited dispatch override; never move a claimed, retried or uncertain remote task."""
    groups=members(pool)
    if len(groups)<2:raise ValueError('rebalance requires two verified independent groups')
    moved=[]
    with queue.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        loads={g['capability']:0 for g in groups}
        for row in c.execute("SELECT capability FROM tasks WHERE status IN ('running','reconcile')"):
            if row['capability'] in loads:loads[row['capability']]+=4
        rows=c.execute("SELECT * FROM tasks WHERE job_id=? AND stage='shot' AND status='pending' AND attempts=0 ORDER BY rowid",(job_id,)).fetchall()
        # Validate the entire operation before writing anything.
        for row in rows:
            payload=json.loads(row['payload'])
            if payload['data'].get('source_path'):continue
            original=payload['plan'].get('shot_backends',{}).get(payload['data']['id'],payload['plan']['backend'])
            if not any(supports_shot(g,payload['data']) for g in groups):
                raise ValueError('no verified group supports this shot with reference images')
            if any(g.get('generation')!=original.get('generation') for g in groups):
                raise ValueError('cannot migrate a frozen task to different generation settings')
        for row in rows:
            payload=json.loads(row['payload']);shot=payload['data']
            if shot.get('source_path'):continue
            eligible=[g for g in groups if supports_shot(g,shot)]
            group=min(eligible,key=lambda g:loads[g['capability']]);loads[group['capability']]+=max(4,shot['duration_seconds'])
            if row['capability']==group['capability']:continue
            payload['backend_override']=group
            c.execute('UPDATE tasks SET capability=?,payload=? WHERE id=?',(group['capability'],json.dumps(payload),row['id']))
            record={'id':row['id'],'from':row['capability'],'to':group['capability'],'resource_id':group['resource_id'],'reason':'additional verified independent group; task never submitted remotely'}
            queue.event(c,row['id'],'backend_reassigned',json.dumps(record));moved.append(record)
    return moved
