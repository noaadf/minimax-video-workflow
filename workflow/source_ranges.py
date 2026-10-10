"""Expand exact edit receipts into source ranges; detect adjacent source restarts."""
def expand(source,start,frames,recipes,stack=()):
    if source in stack:raise ValueError('cyclic edit provenance')
    if start<0 or frames<=0:raise ValueError('invalid source interval')
    if source not in recipes:return [{'source_sha256':source,'start_frame':start,'frames':frames}]
    result=[];cursor=0;stop=start+frames
    for segment in recipes[source]:
        end=cursor+segment['frames'];left=max(start,cursor);right=min(stop,end)
        if right>left:
            result+=expand(segment['source_sha256'],segment.get('start_frame',0)+left-cursor,right-left,recipes,stack+(source,))
        cursor=end
    if sum(x['frames'] for x in result)!=frames:raise ValueError('edit provenance does not cover requested interval')
    return result


def adjacent_replays(timeline):
    results=[]
    for left,right in zip(timeline,timeline[1:]):
        if left['global_start_frame']+left['frames']!=right['global_start_frame']:continue
        if left['source_sha256']!=right['source_sha256']:continue
        overlap=min(left['start_frame']+left['frames'],right['start_frame']+right['frames'])-max(left['start_frame'],right['start_frame'])
        if overlap>0:
            results.append({'left_shot':left['shot'],'right_shot':right['shot'],'boundary_frame':right['global_start_frame'],
              'source_sha256':left['source_sha256'],'overlap_frames':overlap,'previous_range':[left['start_frame'],left['start_frame']+left['frames']],
              'next_range':[right['start_frame'],right['start_frame']+right['frames']],'confirmed_range_overlap':True,'confirmed_visible_defect':False})
    return results


def replay_runs(timeline):
    """Contiguous same-source runs that actually restart/overlap a source interval."""
    groups=[]
    for row in timeline:
        if groups and groups[-1][-1]['source_sha256']==row['source_sha256'] and groups[-1][-1]['global_start_frame']+groups[-1][-1]['frames']==row['global_start_frame']:
            groups[-1].append(row)
        else:groups.append([row])
    return [g for g in groups if adjacent_replays(g)]
