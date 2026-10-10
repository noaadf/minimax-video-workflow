"""CPU-only ending audit of final mix and original stems; no speech/semantic claims."""
import argparse,array,json,math,subprocess,time,sys
from pathlib import Path
from .core.io import sha256, write_json, digest
from .programmatic_qc import timeline_checks
RATE=16000


def pcm(path, channels=1, rate=RATE):
    if channels not in (1,2): raise ValueError('Only mono or stereo PCM is supported')
    if type(rate) is not int or not 8000<=rate<=192000: raise ValueError('Invalid audio sample rate')
    p=subprocess.run(['ffmpeg','-v','error','-threads','2','-i',str(path),'-vn','-ac',str(channels),'-ar',str(rate),'-f','f32le','-'],capture_output=True,timeout=120,check=True)
    a=array.array('f');a.frombytes(p.stdout);return a


def rms(a,start,end):
    s=a[max(0,int(start*RATE)):min(len(a),int(end*RATE))]
    return 20*math.log10(max(1e-12,math.sqrt(sum(x*x for x in s)/max(1,len(s)))))


def evaluate(mix,voice,music,plan,segments):
    length=plan.get('duration_seconds',30)
    ending=plan['narration'][-1];start=ending['start'];end=ending['end']
    windows=[];t=start
    while t<end-.001:
        stop=min(t+.25,end)
        # Anchor the final window to the line end instead of measuring a tiny
        # remainder that can coincide with a waveform zero crossing.
        window_start=max(start,stop-.25)
        windows.append({'start':round(window_start,3),'end':round(stop,3),'bed_dbfs':round(rms(music,window_start,stop),2),'mix_dbfs':round(rms(mix,window_start,stop),2)})
        t=stop
    active=[i/RATE for i in range(int(start*RATE),len(voice),320) if rms(voice,i/RATE,(i+320)/RATE)>-45]
    last_active=round(active[-1]+.02,3) if active else None
    checks=timeline_checks(plan)
    checks.update(mix_duration=abs(len(mix)/RATE-length)<.1,voice_duration=abs(len(voice)/RATE-length)<.1,
                  music_duration=abs(len(music)/RATE-length)<.1,
                  music_under_entire_final_line=bool(windows) and min(w['bed_dbfs'] for w in windows)>-40,
                  final_mix_has_no_quarter_second_silence=bool(windows) and min(w['mix_dbfs'] for w in windows)>-50,
                  final_voice_present=last_active is not None,
                  measured_voice_ends_before_tail=last_active is not None and last_active<=length-.9,
                  planned_tail_at_least_point9=length-end>=.9,
                  post_line_mix_audible=rms(mix,end,min(length,end+.5))>-50,
                  last_word_delivery_unaccelerated=all(s.get('tempo',1)<=1.001 for s in segments if s.get('preserve_delivery')))
    return {'checks':checks,'programmatic_passed':all(checks.values()),'closing_windows':windows,
            'last_voice_activity_seconds':last_active,'planned_narration_end':end,'planned_tail_seconds':length-end,
            'tail_mix_windows':[{'start':t,'end':t+.25,'dbfs':round(rms(mix,t,t+.25),2)} for t in [length-2,length-1.5,length-1,length-.5,length-.25]],
            'preserved_delivery_segments':[s for s in segments if s.get('preserve_delivery')],
            'content_approved':False,'not_assessed':['pronunciation or transcript accuracy','subjective voice timbre and musical ending quality','sound/visual semantic correspondence'],
            'method':'PCM energy windows, timeline arithmetic, known TTS tempo metadata; activity threshold-45dBFS is not ASR'}


def run(spec,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);results=[]
    for item in spec['items']:
        start=time.monotonic()
        for a in item['assets'].values():
            if sha256(a['path'])!=a['sha256']:raise ValueError('asset SHA mismatch')
        key=digest(item);dest=output/(item['id']+'.json')
        if dest.exists() and json.loads(dest.read_text(encoding="utf-8")).get('input_digest')==key:
            results.append(json.loads(dest.read_text(encoding="utf-8")));continue
        aud={k:pcm(v['path']) for k,v in item['assets'].items()}
        result=evaluate(aud['mix'],aud['voice'],aud['music'],item['plan'],item['segments'])
        result.update(id=item['id'],input_digest=key,assets=item['assets'],elapsed_seconds=time.monotonic()-start)
        write_json(dest,result);results.append(result)
    summary={'items':results,'all_programmatic_passed':all(r['programmatic_passed'] for r in results),'content_approved':False,'api_calls':0}
    write_json(output/'summary.json',summary);print(json.dumps({'count':len(results),'all_programmatic_passed':summary['all_programmatic_passed'],'seconds':sum(r['elapsed_seconds'] for r in results),'api_calls':0}))
