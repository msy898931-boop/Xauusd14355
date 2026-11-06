"""Render chart.html from chart_data.json -- a self-contained levels map."""
import json
import os

import pathlib
_OUT = pathlib.Path(os.environ.get("CHART_OUT_DIR")
                    or pathlib.Path(__file__).resolve().parent)
D = json.load(open(_OUT / "chart_data.json"))

HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XAUUSD — خريطة المستويات</title>
<style>
  :root{--bg:#0b1220;--panel:#111a2e;--grid:#1e2b45;--txt:#e2e8f0;--mut:#8ea0bd;
        --gold:#facc15;--sup:#f87171;--dem:#4ade80;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font-family:"Segoe UI",Tahoma,system-ui,sans-serif;padding:18px}
  .wrap{max-width:1120px;margin:0 auto}
  h1{font-size:20px;margin:0 0 4px}
  h3{font-size:15px;margin:0 0 8px}
  .sub{color:var(--mut);font-size:13px;margin-bottom:14px}
  .card{background:var(--panel);border:1px solid var(--grid);border-radius:14px;
        padding:14px;margin-bottom:14px}
  .alert{border-color:#7f1d1d;background:#2a1214}
  svg{width:100%;height:auto;display:block}
  .legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px;font-size:12.5px;color:var(--mut)}
  .legend i{display:inline-block;width:12px;height:12px;border-radius:3px;
            margin-inline-end:6px;vertical-align:-1px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:7px 9px;text-align:right;border-bottom:1px solid var(--grid)}
  th{color:var(--mut);font-weight:600;font-size:12px}
  .tag{font-size:11px;padding:2px 7px;border-radius:6px;background:#1c2b47;color:#9fc0ff}
  .buy{color:var(--dem)}.sell{color:var(--sup)}.gold{color:var(--gold)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:760px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body><div class="wrap">
<h1>📉 XAUUSD — خريطة المستويات والمناطق</h1>
<div class="sub" id="sub"></div>

<div class="card alert" id="evCard" style="display:none"></div>

<div class="card">
  <svg id="chart" viewBox="0 0 1080 540" preserveAspectRatio="xMidYMid meet"></svg>
  <div class="legend">
    <span><i style="background:var(--gold)"></i>السعر الحيّ</span>
    <span><i style="background:var(--dem);opacity:.5"></i>مناطق الشراء (طلب)</span>
    <span><i style="background:var(--sup);opacity:.5"></i>مناطق البيع (عرض)</span>
    <span><i style="background:var(--sup)"></i>مقاومة</span>
    <span><i style="background:var(--dem)"></i>دعم</span>
    <span>▲▼ قمم/قيعان مطبوعة</span>
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h3>🔴 مناطق البيع (عرض)</h3>
    <table><thead><tr><th>المنطقة</th><th>القوة</th><th>ملاحظة</th></tr></thead>
    <tbody id="sellRows"></tbody></table>
  </div>
  <div class="card">
    <h3>🟢 مناطق الشراء (طلب)</h3>
    <table><thead><tr><th>المنطقة</th><th>القوة</th><th>ملاحظة</th></tr></thead>
    <tbody id="buyRows"></tbody></table>
  </div>
</div>

<div class="card">
  <h3>⚡ السيناريوهات (مشروطة — مو توصية)</h3>
  <table><thead><tr><th>السيناريو</th><th>يتفعّل</th><th>الوقف</th><th>الهدف</th>
  <th>R:R</th><th>يُبطَل</th></tr></thead>
  <tbody id="scRows"></tbody></table>
</div>

<div class="grid2">
  <div class="card">
    <h3>💧 السيولة (تجمّع الوقفات)</h3>
    <table><thead><tr><th>المستوى</th><th>الجهة</th><th>الفريم</th></tr></thead>
    <tbody id="liqRows"></tbody></table>
  </div>
  <div class="card">
    <h3>🧲 أرقام مستديرة</h3>
    <div id="roundBox" style="font-size:20px;font-weight:700;color:var(--gold);padding:10px 2px"></div>
    <div style="color:#8ea0bd;font-size:12px">أرقام تستجذب السعر عادةً</div>
  </div>
</div>
</div>
<script>
const D = __PAYLOAD__;
const BARS=D.bars, PIV=D.piv, LIVE=D.live;
const NS="http://www.w3.org/2000/svg";
const W=1080,H=540,PL=64,PR=152,PT=18,PB=30;
const pw=W-PL-PR, ph=H-PT-PB;
const fmt=v=>v.toLocaleString('en-US',{maximumFractionDigits:2});
let vals=[];
BARS.forEach(b=>vals.push(b.h,b.l));
D.supply.concat(D.demand).forEach(z=>vals.push(z.lo,z.hi));
D.res.forEach(r=>vals.push(r.p)); D.sup.forEach(s=>vals.push(s.p));
vals.push(LIVE);
let lo=Math.min(...vals), hi=Math.max(...vals);
const s0=hi-lo; lo-=s0*0.06; hi+=s0*0.06; const span=hi-lo;
const Y=v=>PT+(hi-v)/span*ph, X=i=>PL+i/(BARS.length-1)*pw;
const svg=document.getElementById('chart');
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function txt(x,y,s,fill,size,anchor,weight){
  const t=el('text',{x,y,fill:fill||'#e2e8f0','font-size':size||11,
    'text-anchor':anchor||'start','font-family':'Segoe UI,Tahoma,sans-serif',
    'font-weight':weight||'400','direction':'rtl'});
  t.textContent=s; return t;}
for(let i=0;i<=5;i++){const v=lo+span*i/5,y=Y(v);
  svg.appendChild(el('line',{x1:PL,y1:y,x2:PL+pw,y2:y,stroke:'#1e2b45','stroke-width':1}));
  svg.appendChild(txt(PL+pw+8,y+4,fmt(Math.round(v)),'#8ea0bd',11));}
D.supply.forEach(z=>{const y0=Y(z.hi),y1=Y(z.lo);
  svg.appendChild(el('rect',{x:PL,y:y0,width:pw,height:Math.max(2,y1-y0),
    fill:'#ef4444',opacity:0.13}));});
D.demand.forEach(z=>{const y0=Y(z.hi),y1=Y(z.lo);
  svg.appendChild(el('rect',{x:PL,y:y0,width:pw,height:Math.max(2,y1-y0),
    fill:'#22c55e',opacity:0.13}));});
D.res.forEach(r=>{const y=Y(r.p);
  svg.appendChild(el('line',{x1:PL,y1:y,x2:PL+pw,y2:y,stroke:'#f87171',
    'stroke-width':1,'stroke-dasharray':'5 4',opacity:0.75}));
  svg.appendChild(txt(PL+pw+8,y-4,fmt(r.p)+'  '+r.l,'#f87171',10.5));});
D.sup.forEach(s=>{const y=Y(s.p);
  svg.appendChild(el('line',{x1:PL,y1:y,x2:PL+pw,y2:y,stroke:'#4ade80',
    'stroke-width':1,'stroke-dasharray':'5 4',opacity:0.75}));
  svg.appendChild(txt(PL+pw+8,y+12,fmt(s.p)+'  '+s.l,'#4ade80',10.5));});
const bw=Math.max(3,Math.min(11,pw/BARS.length*0.62));
BARS.forEach((b,i)=>{const x=X(i),up=b.c>=b.o,col=up?'#22c55e':'#ef4444';
  svg.appendChild(el('line',{x1:x,y1:Y(b.h),x2:x,y2:Y(b.l),stroke:col,'stroke-width':1.4}));
  const yo=Y(b.o),yc=Y(b.c);
  svg.appendChild(el('rect',{x:x-bw/2,y:Math.min(yo,yc),width:bw,
    height:Math.max(1.5,Math.abs(yc-yo)),fill:col,opacity:0.92}));});
PIV.forEach(([i,k,p])=>{const x=X(i);
  const y=k==='H'?Y(p)-9:Y(p)+9;
  svg.appendChild(txt(x,y,k==='H'?'▲':'▼',k==='H'?'#f87171':'#4ade80',11,'middle','700'));});
const yl=Y(LIVE);
svg.appendChild(el('line',{x1:PL,y1:yl,x2:PL+pw,y2:yl,stroke:'#facc15','stroke-width':1.8}));
svg.appendChild(el('circle',{cx:PL+pw,cy:yl,r:4,fill:'#facc15'}));
svg.appendChild(txt(PL+pw+8,yl-5,fmt(LIVE)+'  السعر','#facc15',12,null,'700'));
svg.appendChild(el('line',{x1:PL,y1:PT,x2:PL,y2:PT+ph,stroke:'#334155','stroke-width':1}));
svg.appendChild(txt(PL,H-8,BARS[0].t.replace('T',' '),'#8ea0bd',10.5));
svg.appendChild(txt(PL+pw,H-8,BARS[BARS.length-1].t.replace('T',' '),'#8ea0bd',10.5,'end'));
// The chart must never pass a stale read for a fresh one: print its own age,
// computed in the reader's browser, and colour it when the data is old.
(function(){
  const el=document.getElementById('sub');
  let ageTxt='', stale=false;
  if(D.built_at){
    const mins=(Date.now()-Date.parse(D.built_at))/60000;
    stale=mins>15;
    ageTxt=' · حُدِّثت '+(mins<1?'قبل ثوانٍ':mins<60?('قبل '+Math.round(mins)+' دقيقة'):
      ('قبل '+Math.round(mins/60)+' ساعة'));
  }
  el.textContent='آخر '+BARS.length+' ساعة · السعر الحيّ '+fmt(LIVE)+
    ' · المدى '+fmt(Math.round(lo))+' – '+fmt(Math.round(hi))+ageTxt;
  el.style.color=stale?'#f87171':'#8ea0bd';
  if(stale) el.textContent+=' ⚠ بيانات قديمة — شغّل التحديث';
})();
function rows(id,arr,cls){document.getElementById(id).innerHTML=arr.map(z=>
  `<tr><td class="${cls}"><b>${fmt(z.lo)} – ${fmt(z.hi)}</b></td>
   <td><span class="tag">${z.n} لمسة</span></td>
   <td>${z.note}<br><span style="color:#8ea0bd;font-size:11.5px">${z.tf}</span></td></tr>`).join('');}
rows('sellRows',D.supply,'sell'); rows('buyRows',D.demand,'buy');
document.getElementById('liqRows').innerHTML=(D.liq||[]).map(l=>
  `<tr><td class="gold"><b>${fmt(l.p)}</b></td>
   <td>${l.side==='above'?'فوق (وقفات شراء)':'تحت (وقفات بيع)'}</td>
   <td>${l.tf}</td></tr>`).join('') || '<tr><td colspan="3">ما في تجمّع وقفات قريب</td></tr>';
document.getElementById('roundBox').textContent=(D.rounds||[]).map(fmt).join('  ·  ')||'—';
if(D.event){const c=document.getElementById('evCard');c.style.display='';
  const e=D.event, h=(e.hours!=null?(' خلال ~'+e.hours+' ساعة'):'');
  const fc=(e.forecast!=null?(' · متوقع '+e.forecast):'')+(e.previous!=null?(' · سابق '+e.previous):'');
  c.innerHTML='<b>⚠ حدث قريب</b> — '+e.name+' ['+e.currency+']'+h+fc+
    '<br><span style="color:#cbd5e1;font-size:12px">لا تنفيذ قبل مروره — '+
    'السبريد يتوسّع والاتجاه يضرب الطرفين.</span>';}
document.getElementById('scRows').innerHTML=D.sc.map(s=>
  `<tr><td class="${s.cls}"><b>${s.k}</b></td><td>${fmt(s.on)}</td><td>${fmt(s.stop)}</td>
   <td>${fmt(s.tgt)}</td><td>${s.rr}</td><td>${fmt(s.inv)}</td></tr>`).join('');
</script>
</body></html>"""

out = HTML.replace("__PAYLOAD__", json.dumps(D, ensure_ascii=False))
with open(_OUT / "chart.html", "w") as fh:
    fh.write(out)
print("chart.html written", len(out), "bytes")
