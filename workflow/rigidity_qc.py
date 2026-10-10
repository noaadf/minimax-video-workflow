"""Sparse full-frame tracking for triage of mostly stationary structural illustrations.

No proof of physical topology: legitimate reflections, parallax, people and tracking
failures can all trigger review. Small/local/untextured deformations can be missed.
"""
from pathlib import Path
import cv2
import numpy as np

VERSION='rigid-track-v1'


def residuals(origin,current):
    matrix,inliers=cv2.estimateAffinePartial2D(origin,current,method=cv2.RANSAC,
        ransacReprojThreshold=1.5,maxIters=1000,confidence=.99,refineIters=10)
    if matrix is None:return None
    prediction=origin@matrix[:,:2].T+matrix[:,2]
    errors=np.linalg.norm(prediction-current,axis=1)
    span=np.ptp(origin,axis=0)
    return {'points':len(origin),'p90_pixels':float(np.percentile(errors,90)),
            'fraction_over_3px':float(np.mean(errors>3)),
            'origin_span':span.tolist(),'matrix':matrix.tolist()}


def scan(path,start=0,duration=4,cuts=()):
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened():raise ValueError(f'Cannot decode {path}')
    fps=cap.get(cv2.CAP_PROP_FPS)
    if fps<=0:raise ValueError('Missing frame rate')
    begin=round(start*fps);stop=round((start+duration)*fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES,begin)
    resets={round(t*fps) for t in cuts if 0<t<duration}
    rows=[];previous=points=origin=None;initial_count=0;decoded=0
    for index in range(stop-begin):
        ok,frame=cap.read()
        if not ok:break
        decoded+=1
        gray=cv2.cvtColor(cv2.resize(frame,(270,480)),cv2.COLOR_BGR2GRAY)
        if previous is None or index in resets:
            mask=np.zeros_like(gray);mask[24:456,13:257]=255
            points=cv2.goodFeaturesToTrack(gray,maxCorners=450,qualityLevel=.01,minDistance=5,mask=mask)
            origin=None if points is None else points.copy();initial_count=0 if points is None else len(points)
            previous=gray
            rows.append({'frame':index,'seconds':round(index/fps,4),'status':'baseline' if initial_count>=40 else 'insufficient_texture','points':initial_count})
            continue
        row={'frame':index,'seconds':round(index/fps,4)}
        if points is None or len(points)<30:
            rows.append({**row,'status':'tracking_lost'});previous=gray;continue
        nxt,valid,_=cv2.calcOpticalFlowPyrLK(previous,gray,points,None,winSize=(21,21),maxLevel=3)
        if nxt is None:
            points=None;rows.append({**row,'status':'tracking_lost'});previous=gray;continue
        back,back_valid,_=cv2.calcOpticalFlowPyrLK(gray,previous,nxt,None,winSize=(21,21),maxLevel=3)
        if back is None:
            points=None;rows.append({**row,'status':'tracking_lost'});previous=gray;continue
        good=(valid.ravel()>0)&(back_valid.ravel()>0)&(np.linalg.norm(back-points,axis=2).ravel()<1)
        points=nxt[good];origin=origin[good]
        measurement=residuals(origin.reshape(-1,2),points.reshape(-1,2)) if len(points)>=30 else None
        if measurement is None:
            row['status']='tracking_lost'
        else:
            measurement['retained_fraction']=len(points)/initial_count
            enough=measurement['retained_fraction']>=.6 and measurement['origin_span'][0]>=108 and measurement['origin_span'][1]>=192
            row.update(measurement)
            row['status']='insufficient_coverage' if not enough else ('nonrigid_motion_candidate' if measurement['p90_pixels']>2 or measurement['fraction_over_3px']>.1 else 'within_rigid_motion_threshold')
        rows.append(row);previous=gray
    cap.release()
    exceptions=[];run=[]
    for row in rows+[{'status':'baseline'}]:
        abnormal=row['status'] not in ('baseline','within_rigid_motion_threshold')
        if abnormal:run.append(row)
        elif run:
            if len(run)>=3:exceptions.append({'start':run[0]['seconds'],'end':round(run[-1]['seconds']+1/fps,4),'frames':len(run),'reasons':sorted({r['status'] for r in run})})
            run=[]
    return {'version':VERSION,'path':str(Path(path).resolve()),'fps':fps,'decoded_frames':decoded,'expected_frames':stop-begin,
            'decode_complete':decoded==stop-begin,'rows':rows,'exception_intervals':exceptions,
            'status':'review_candidate' if exceptions or decoded!=stop-begin else 'no_threshold_exception',
            'content_approved':False,'topology_certified':False,'thresholds':{'width':270,'height':480,'p90_pixels':2,'fraction_over_3px':.1,'minimum_persistent_frames':3},
            'limits':['Sparse feature tracking on every decoded frame, not every pixel.','Global similarity model; reflection, parallax, motion and edits can trigger without structural defects.','Cannot count panels, establish axes/real device identity or detect all small and textureless distortions.','Source-shot screening; final overlays and cuts require separate checks.']}
