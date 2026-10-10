"""Opt-in evidence repair using decoded first/last PTS, without changing media."""
import bisect
import json
import math
import re
import subprocess
from pathlib import Path
from .core.io import sha256, write_json

POLICY = 'decoded-first-last-pts-v1'

def select_frames(frames, *, start, length, count=24):
    if not math.isfinite(start) or not math.isfinite(length) or start < 0 or length <= 0:
        raise ValueError('Invalid evidence interval')
    pts = [float(f['best_effort_timestamp_time']) for f in frames]
    if any(not math.isfinite(t) for t in pts) or any(b <= a for a,b in zip(pts,pts[1:])):
        raise ValueError('Decoded PTS must be finite and strictly increasing')
    rows = [{'index':i, 'source_pts':t, 'local_pts':round(t-start,6)}
            for i,t in enumerate(pts) if start-1e-7 <= t < start+length-1e-7]
    if len(rows) < count:
        raise ValueError('Too few distinct decoded frames for dense evidence')
    times = [r['source_pts'] for r in rows]
    indexes = []
    # Constrain each selection to leave enough distinct frames for the rest.
    for i in range(count):
        target = times[0] + (times[-1]-times[0])*i/(count-1)
        at = bisect.bisect_left(times, target-1e-7)
        at = min(max(at, indexes[-1]+1 if indexes else 0), len(rows)-(count-i))
        indexes.append(at)
    return rows, [rows[i] for i in indexes]

def build(video, folder, *, start=0, length):
    video=Path(video).resolve();folder=Path(folder)
    if folder.exists():
        raise ValueError('Evidence destination already exists; preserve frozen evidence')
    before=sha256(video)
    result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','frame=best_effort_timestamp_time','-of','json',str(video)],
        capture_output=True,text=True,check=True,timeout=120)
    frames=json.loads(result.stdout)['frames']
    rows,selected=select_frames(frames,start=start,length=length)
    folder.mkdir(parents=True)
    def render(chosen, name, scaling):
        selection='+'.join(f'eq(n,{r["index"]})' for r in chosen)
        vf=f"select='{selection}',showinfo,"+scaling
        command=['ffmpeg','-v','info','-threads','2','-i',str(video),'-vf',vf,
                 '-frames:v','1','-fps_mode','passthrough',str(folder/name)]
        done=subprocess.run(command,capture_output=True,text=True,check=True,timeout=120)
        actual=[float(t) for t in re.findall(r'\bn:\s*\d+\s+pts:.*?pts_time:([\d.eE+\-]+)',done.stderr)]
        if len(actual)!=len(chosen) or any(abs(t-r['source_pts'])>1e-4 for t,r in zip(actual,chosen)):
            raise ValueError('Rendered evidence PTS differs from decoded frame plan')
        return {'path':str((folder/name).resolve()),'sha256':sha256(folder/name),
                'frames':chosen,'rendered_source_pts':actual}
    grid=render(selected,'grid.jpg','scale=270:480,tile=6x4')
    endpoints=render([rows[0],rows[-1]],'endpoints.png','tile=2x1')
    if sha256(video)!=before:
        raise ValueError('Source video changed while extracting evidence')
    record={'policy':POLICY,'source_path':str(video),'source_sha256':before,
            'start':start,'length':length,'decoded_interval_frame_count':len(rows),
            'first_frame':rows[0],'last_frame':rows[-1],'grid':grid,
            'endpoints':endpoints,'source_changed':False,'content_approved':False,
            'limitations':['Discrete samples; no continuous motion or audio verification']}
    write_json(folder/'evidence.json',record)
    return record
