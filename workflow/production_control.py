"""User pause and reversible removal from active product views."""
import json
from pathlib import Path

def settings(root):
 p=Path(root)/'production_control.json'
 return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
def paused(root):return settings(root).get('paused') is True
def withdrawn(root,jid,plans):
 from .products import product_id
 return product_id(jid,plans) in settings(root).get('withdrawn_products',[])
