"""Cheap editorial duplicate checks; same protagonist alone is never a rejection."""
import json,re

def normalized(text):return re.sub(r'[^\w\u4e00-\u9fff]','',text.lower())
def grams(text):
 s=normalized(text);return {s[i:i+4] for i in range(max(0,len(s)-3))}
def overlap(a,b):return len(a&b)/max(1,min(len(a),len(b)))
def script(p):return ''.join(n.get('text','') for n in p.get('narration',[]))
def facts(p):return {normalized(f.get('claim',f.get('text',''))) for f in p.get('facts',[]) if f.get('claim',f.get('text',''))}
def check(connection,plan):
 policy=plan.get('novelty_policy',{})
 if not policy.get('enabled') or plan.get('parent_job_id'):return
 text=script(plan);g=grams(text)
 for subject in policy.get('paused_subjects',[]):
  if subject in (plan.get('title','')+plan.get('topic','')+text):raise ValueError('editorial subject paused: '+subject)
 for row in connection.execute('SELECT id,manifest FROM jobs ORDER BY rowid DESC LIMIT 300'):
  old=json.loads(row['manifest'])
  if old.get('product_id',old.get('id',row['id']))==plan.get('product_id',plan['id']):continue
  def assets(p):
   hashes=p.get('input_sha256',{})
   return [hashes[x['source_path']] for x in p.get('shots',[]) if x.get('source_path') in hashes]
  a1,a2=assets(plan),set(assets(old))
  if len(a1)>=2 and sum(a in a2 for a in a1)/len(a1)>.5:
   raise ValueError('editorial repeated footage from '+row['id'])
  if len(normalized(text))<40:continue
  similarity=overlap(g,grams(script(old)));f1,f2=facts(plan),facts(old)
  same_facts=len(f1)>=2 and len(f2)>=2 and overlap(f1,f2)>=.8
  if similarity>=policy.get('script_overlap_limit',.72) or (same_facts and similarity>=.4):
   raise ValueError('editorial duplicate of '+row['id']+'; change the story/event/argument, not only its wording')
