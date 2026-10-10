"""Manifest-bound natural-color grading; applied before captions, never to audio."""
import math
DEFAULT={'version':'natural_v1','saturation':0.9,'contrast':1.0,'brightness':0.0}
def filters(plan):
 policy=plan.get('color_policy')
 if not policy:return '',[]
 if policy.get('version')!='natural_v1':raise ValueError('Unknown color policy')
 saturation=policy.get('saturation',.9)
 if type(saturation) not in (int,float) or not math.isfinite(saturation) or not .7<=saturation<=1:raise ValueError('Natural saturation must be between .7 and 1')
 if policy.get('contrast',1)!=1 or policy.get('brightness',0)!=0:raise ValueError('Natural policy preserves contrast and exposure')
 parts=[];receipt=[];t=0
 for s in plan['shots']:
  end=t+s['duration_seconds'];graphic=s.get('production_mode')=='deterministic_graphic'
  gain=1 if graphic else saturation
  if gain!=1:parts.append(f"eq=saturation={gain}:enable='gte(t,{t:.6f})*lt(t,{end:.6f})'")
  receipt.append({'shot_id':s['id'],'start':t,'end':end,'saturation':gain,'reason':'authored graphic preserved' if graphic else 'natural live-action default'})
  t=end
 return ','.join(parts),receipt
