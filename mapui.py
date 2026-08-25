#!/usr/bin/env python3
"""Edit the Push's 64 shortcut pads in a browser. Writes the real macros.json,
which push_cc.py re-reads on change, so edits land on the pads live.

    python3 mapui.py        then open http://localhost:8765
"""
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import push_cc

PORT = 8765

PAGE = """<!doctype html><meta charset=utf-8><title>Push shortcuts</title>
<style>
 :root{color-scheme:dark}
 body{background:#0b0b0d;color:#e6e6e6;font:14px/1.4 ui-monospace,Menlo,monospace;
      margin:0;padding:24px;display:flex;gap:24px;flex-wrap:wrap}
 h1{font-size:15px;font-weight:600;margin:0 0 14px;color:#9aa}
 #grid{display:grid;grid-template-columns:repeat(8,88px);gap:6px}
 .pad{height:52px;border:1px solid #2a2a30;border-radius:6px;background:#141419;
      padding:5px 6px;cursor:pointer;overflow:hidden;font-size:11px;color:#cfd6e4}
 .pad:hover{border-color:#4a4a58}
 .pad.set{background:#16202e;border-color:#3d5a80}
 .pad.sel{outline:2px solid #7aa2d2;outline-offset:1px}
 .pad.over{border-color:#7aa2d2;background:#1d2733}
 .pad.drag{opacity:.35}
 .pad[draggable=true]{user-select:none}
 .n{color:#555c66;font-size:9px}
 aside{width:330px}
 label{display:block;margin:12px 0 4px;color:#9aa;font-size:12px}
 input,textarea{width:100%;box-sizing:border-box;background:#141419;color:#e6e6e6;
   border:1px solid #2a2a30;border-radius:5px;padding:7px;font:inherit}
 textarea{height:120px;resize:vertical}
 button{margin-top:12px;margin-right:8px;background:#3d5a80;color:#fff;border:0;
   border-radius:5px;padding:8px 14px;font:inherit;cursor:pointer}
 button.ghost{background:#2a2a30}
 #msg{margin-top:10px;color:#7fb37f;height:16px;font-size:12px}
 .row{display:flex;align-items:center;gap:8px;margin-top:12px}
 .row input{width:auto}
 .row label{margin:0}
</style>
<div><h1>Shortcut pads &mdash; laid out as they sit on the Push
  <span class=n style="margin-left:10px">drag a pad onto another to swap them</span></h1>
<div id=grid></div></div>
<aside>
  <h1>Pad <span id=cur>-</span></h1>
  <label>Label <span class=n>(shown on the Push screen)</span></label>
  <input id=label maxlength=24>
  <label>Text <span class=n>(inserted into the prompt)</span></label>
  <textarea id=text></textarea>
  <label>Colour label</label>
  <select id=tag></select>
  <div class=row>
    <input type=checkbox id=submit>
    <label for=submit>Auto submit <span class=n>(press enter on tap)</span></label>
  </div>
  <button onclick=save()>Save</button>
  <button class=ghost onclick=clearPad()>Clear pad</button>
  <div id=msg></div>

  <h1 style="margin-top:22px">Colour labels</h1>
  <div id=labels></div>
  <div style="display:flex;gap:6px;margin-top:8px">
    <input id=lname placeholder="name" style="flex:1">
    <select id=lcol></select>
    <button onclick=addLabel() style="margin:0">Add</button>
  </div>
</aside>
<script>
let macros=[], labels=[], sel=null, from=null;
// Approximate only. The Push has its own 128-entry palette and the browser
// cannot see it; these swatches are a guide, the index is the truth.
const PALETTE=[["red",127,"#e03c3c"],["orange",3,"#e08a2c"],["yellow",8,"#e0d02c"],
               ["green",126,"#3cd05a"],["blue",125,"#4a86d0"],["white",122,"#e6e6e6"]];
const hexFor=c=>(PALETTE.find(p=>p[1]===c)||[,,"#4a86d0"])[2];
const $=id=>document.getElementById(id);
// note 36 is bottom-left on the Push, so the top screen row is the top pad row
function draw(){
  const g=$('grid'); g.innerHTML='';
  for(let r=7;r>=0;r--) for(let c=0;c<8;c++){
    const i=r*8+c, m=macros[i], d=document.createElement('div');
    d.className='pad'+(m?' set':'')+(i===sel?' sel':'');
    d.innerHTML='<div class=n>'+(36+i)+(m&&m.submit?' <span style=color:#6eaa6e>\u23ce</span>':'')
                +'</div>'+(m?escapeHtml(m.label):'');
    if(m) d.style.borderLeft='4px solid '+hexFor(m.colour);
    d.onclick=()=>pick(i);
    d.draggable=!!m;                       // an empty pad has nothing to carry
    d.ondragstart=e=>{ from=i; d.classList.add('drag');
                       e.dataTransfer.effectAllowed='move'; };
    d.ondragend=()=>{ from=null; draw(); };
    d.ondragover=e=>{ if(from!==null&&from!==i){ e.preventDefault(); d.classList.add('over'); } };
    d.ondragleave=()=>d.classList.remove('over');
    d.ondrop=e=>{ e.preventDefault(); if(from===null||from===i) return; swap(from,i); };
    g.appendChild(d);
  }
}
const escapeHtml=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// Swap rather than overwrite: dropping onto an occupied pad must never be a
// way to lose what was already there.
function swap(a,b){
  const t=macros[a]; macros[a]=macros[b]; macros[b]=t;
  if(sel===a) sel=b; else if(sel===b) sel=a;
  from=null; put(); }

function pick(i){ sel=i; const m=macros[i]||{label:'',text:''};
  $('cur').textContent=(36+i); $('label').value=m.label||''; $('text').value=m.text||'';
  $('tag').value=m.tag||''; $('submit').checked=!!m.submit; draw(); }

function drawLabels(){
  const opts=['<option value="">(none)</option>'].concat(
    labels.map(l=>'<option value="'+escapeHtml(l.name)+'">'+escapeHtml(l.name)+'</option>'));
  $('tag').innerHTML=opts.join('');
  $('lcol').innerHTML=PALETTE.map(p=>'<option value="'+p[1]+'">'+p[0]+'</option>').join('');
  $('labels').innerHTML=labels.map((l,i)=>
    '<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'+
    '<span style="width:14px;height:14px;border-radius:3px;background:'+hexFor(l.colour)+'"></span>'+
    '<span style="flex:1">'+escapeHtml(l.name)+'</span>'+
    '<button class=ghost style="margin:0;padding:2px 8px" onclick="delLabel('+i+')">x</button></div>'
  ).join('') || '<span class=n>none yet</span>';
}
function addLabel(){
  const name=$('lname').value.trim(); if(!name) return note('name it first');
  if(labels.some(l=>l.name===name)) return note('that name is taken');
  labels.push({name,colour:+$('lcol').value}); $('lname').value=''; put(); }
function delLabel(i){
  const gone=labels[i].name;
  labels.splice(i,1);
  macros.forEach(m=>{ if(m&&m.tag===gone){ m.tag=null; } });   // no orphan tags
  put(); }
async function save(){
  if(sel===null) return note('pick a pad first');
  const text=$('text').value.trim();
  const tag=$('tag').value||null;
  const lab=labels.find(l=>l.name===tag);
  macros[sel]= text ? {label:($('label').value.trim()||text.split(/\\s+/).slice(0,2).join(' ')),
                       text, tag, colour: lab?lab.colour:125,
                       submit: $('submit').checked} : null;
  await put(); }
async function clearPad(){ if(sel===null) return note('pick a pad first');
  macros[sel]=null; $('label').value=''; $('text').value=''; await put(); }
async function put(){
  const r=await fetch('/macros',{method:'POST',
    body:JSON.stringify({labels,pads:macros})});
  note(r.ok?'saved \\u2014 live on the Push':'save failed'); draw(); drawLabels(); }
function note(t){ $('msg').textContent=t; setTimeout(()=>$('msg').textContent='',2500); }
fetch('/macros').then(r=>r.json()).then(d=>{
  labels=d.labels||[]; macros=d.pads||[]; draw(); drawLabels(); });
</script>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/macros":
            labels, macros = push_cc.load_macros()
            self._send(200, json.dumps({"labels": labels, "pads": macros}),
                       "application/json")
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/macros":
            return self._send(404, "no", "text/plain")
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            return self._send(400, "bad json", "text/plain")
        # go through load_macros' shape so the file can only ever hold what the
        # Push can actually use -- 64 slots, label derived when it is missing
        labels = entries.get("labels") or []
        pads = entries.get("pads") or []
        push_cc.save_macros(
            [None if not e else
             {"label": e.get("label") or push_cc.label_for(e.get("text", "")),
              "text": e.get("text", ""),
              "colour": int(e.get("colour", push_cc.BLUE)) & 0x7F,
              "tag": e.get("tag"), "submit": bool(e.get("submit"))}
             for e in pads[:push_cc.MACRO_SLOTS]], labels)
        self._send(200, "ok", "text/plain")

    def log_message(self, *_):
        pass                                   # ponytail: no request spam


if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    print(f"editing {push_cc.MACRO_FILE}\n{url}")
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
