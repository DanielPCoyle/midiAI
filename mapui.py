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
</style>
<div><h1>Shortcut pads &mdash; laid out as they sit on the Push</h1><div id=grid></div></div>
<aside>
  <h1>Pad <span id=cur>-</span></h1>
  <label>Label <span class=n>(shown on the Push screen)</span></label>
  <input id=label maxlength=24>
  <label>Text <span class=n>(inserted into the prompt, not submitted)</span></label>
  <textarea id=text></textarea>
  <button onclick=save()>Save</button>
  <button class=ghost onclick=clearPad()>Clear pad</button>
  <div id=msg></div>
</aside>
<script>
let macros=[], sel=null;
const $=id=>document.getElementById(id);
// note 36 is bottom-left on the Push, so the top screen row is the top pad row
function draw(){
  const g=$('grid'); g.innerHTML='';
  for(let r=7;r>=0;r--) for(let c=0;c<8;c++){
    const i=r*8+c, m=macros[i], d=document.createElement('div');
    d.className='pad'+(m?' set':'')+(i===sel?' sel':'');
    d.innerHTML='<div class=n>'+(36+i)+'</div>'+(m?escapeHtml(m.label):'');
    d.onclick=()=>pick(i); g.appendChild(d);
  }
}
const escapeHtml=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function pick(i){ sel=i; const m=macros[i]||{label:'',text:''};
  $('cur').textContent=(36+i); $('label').value=m.label||''; $('text').value=m.text||''; draw(); }
async function save(){
  if(sel===null) return note('pick a pad first');
  const text=$('text').value.trim();
  macros[sel]= text ? {label:($('label').value.trim()||text.split(/\\s+/).slice(0,2).join(' ')),text} : null;
  await put(); }
async function clearPad(){ if(sel===null) return note('pick a pad first');
  macros[sel]=null; $('label').value=''; $('text').value=''; await put(); }
async function put(){
  const r=await fetch('/macros',{method:'POST',body:JSON.stringify(macros)});
  note(r.ok?'saved \\u2014 live on the Push':'save failed'); draw(); }
function note(t){ $('msg').textContent=t; setTimeout(()=>$('msg').textContent='',2500); }
fetch('/macros').then(r=>r.json()).then(m=>{macros=m; draw();});
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
            macros = push_cc.load_macros()
            self._send(200, json.dumps(
                [None if not m else {"label": m[0], "text": m[1]} for m in macros]),
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
        push_cc.save_macros([None if not e else
                             (e.get("label") or push_cc.label_for(e.get("text", "")),
                              e.get("text", ""))
                             for e in entries[:push_cc.MACRO_SLOTS]])
        self._send(200, "ok", "text/plain")

    def log_message(self, *_):
        pass                                   # ponytail: no request spam


if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    print(f"editing {push_cc.MACRO_FILE}\n{url}")
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
