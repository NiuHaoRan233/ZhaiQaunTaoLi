'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=2)=>v===null||v===undefined||!Number.isFinite(+v)?'—':(+v).toLocaleString('en-US',{maximumFractionDigits:d});
const price=v=>v>0?fmt(v,4):'—';
const timeText=(v,full=false)=>v?new Date(v).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai',...(full?{}:{hour:'2-digit',minute:'2-digit',second:'2-digit'})}):'—';
const sideClass=s=>s==='B'?'buy':s==='S'?'sell':'neutral';
const sideLabel=s=>s==='B'?'B ↑':s==='S'?'S ↓':'◆';
const spread=q=>q.bid[0]>0&&q.ask[0]>=q.bid[0]&&q.bv[0]>0&&q.av[0]>0?(q.ask[0]-q.bid[0])/((q.ask[0]+q.bid[0])/2)*100:null;
function summarizeDailySpread(points){
 // Each quote weights the interval until the next observation. Do not infer
 // persistence through breaks, stale gaps or beyond the final received quote.
 let duration=0,weightedSpread=0,weightedPercent=0,intervals=0;
 for(let i=0;i+1<points.length;i++){
  const p=points[i],next=points[i+1],dt=next.t-p.t,percent=spread(p);
  if(dt<=0||dt>60000||next.reset||percent===null)continue;
  duration+=dt;
  weightedSpread+=(p.ask[0]-p.bid[0])*dt;
  weightedPercent+=percent*dt;
  intervals++;
 }
 return {averageSpread:duration?weightedSpread/duration:null,
         averagePercent:duration?weightedPercent/duration:null,
         validMinutes:duration/60000,intervals};
}
let favorites=[];try{favorites=JSON.parse(localStorage.getItem('commodity-option-favorites')||'[]')}catch{}
const state={catalog:null,chain:null,product:'SF:ru',code:'ru2610P18000.SF',month:'2610',exchange:'',favoriteMode:false,favorites,
 data:null,points:[],events:[],eventPage:0,window:null,hover:-1,request:0,chainRequest:0,loading:false,mouse:null,
 chainOrder:{metric:'volume',side:'both',direction:'desc'}};
async function api(path){const r=await fetch(path);const data=await r.json();if(!r.ok)throw new Error(data.error||r.statusText);return data;}
function notice(t){$('statusText').textContent=t;}
function saveFavorites(){localStorage.setItem('commodity-option-favorites',JSON.stringify(state.favorites));$('favCount').textContent=state.favorites.length;$('star').textContent=state.favorites.includes(state.code)?'★ 已自选':'☆ 自选';}

async function catalog(){
 const data=await api('/api/catalog');if(!state.catalog?.status.connected&&data.status.connected&&state.data&&!state.data.source.includes('QMT历史'))state.needsBackfill=true;state.catalog=data;const s=data.status;
 $('productCount').textContent=fmt(s.products,0);$('contractCount').textContent=fmt(s.contracts,0);$('quotedCount').textContent=`${fmt(s.quoted,0)} / ${fmt(s.two_sided,0)}`;
 $('coverage').textContent=`${new Set(data.products.map(p=>p.market)).size} 个市场`+(s.missing.length?' · '+s.missing.join('、')+'未返回':'')+(s.future_timestamps?` · ${fmt(s.future_timestamps,0)} 时间待核`:'');
 $('coverage').title=s.future_timestamps?`${fmt(s.future_timestamps,0)} 个QMT最新快照带未来日期，原时间保留；不据此选择主图日期。`:'';
 $('latestTime').textContent=timeText(s.latest,true);$('connection').textContent=s.connected?'QMT 已连接':'QMT 离线 · 历史可用';$('led').classList.toggle('online',s.connected);
 $('connection').title=s.phase;$('recordingText').textContent=`本次接收 ${fmt(s.received,0)} · 已保存 ${fmt(s.written,0)}`+(s.dropped?` · 丢失 ${s.dropped}`:'');
 $('recordingText').title=`记录开始 ${s.recording_since}\n已订阅 ${s.subscribed.join(' / ')}\n待写入 ${s.pending}\n${s.errors.map(e=>e.message).join('\n')}`;
 if(!$('date').value)$('date').value=data.default_date;
 renderProducts();
 if(!state.data)notice(s.phase);
}
function renderProducts(){
 if(!state.catalog)return;
 let ps=state.catalog.products.filter(p=>!state.exchange||p.market===state.exchange);
 const q=$('search').value.trim().toLowerCase();if(q)ps=ps.filter(p=>(p.name+p.symbol).toLowerCase().includes(q));
 if(state.favoriteMode){const set=new Set(state.favorites.map(c=>c.replace(/\d.*$/,'')+':'+c.split('.').pop()));ps=ps.filter(p=>set.has(p.symbol+':'+p.market));}
 $('visibleProducts').textContent=ps.length+' 品种';
 $('products').innerHTML=ps.map(p=>`<button class="product-row ${p.id===state.product?'active':''}" data-product="${esc(p.id)}"><span class="symbol">${esc(p.symbol.toUpperCase())}</span><span class="name">${esc(p.name)}</span><small>${p.count}</small></button>`).join('')||'<div class="empty-message">暂无匹配品种</div>';
}
async function selectProduct(product,code=null,quiet=false){
 const token=++state.chainRequest;state.chainLoading=true;if(!quiet)notice('正在读取合约链…');
 try{
 const chain=await api('/api/chain?product='+encodeURIComponent(product));if(token!==state.chainRequest)return;
 const changed=state.product!==product;state.product=product;state.chain=chain;
 if(code){state.code=code;const c=chain.items.find(i=>i.code===code);if(c)state.month=c.month;}
 else if(changed||!chain.items.some(i=>i.code===state.code)){state.month=chain.ranks[0]?.month||chain.product.months[0];state.code=bestContract(state.month)?.code||chain.items[0]?.code;}
 $('month').innerHTML='<option value="all">全部月份</option>'+chain.product.months.map(m=>`<option value="${m}">${m}${chain.ranks[0]?.month===m?' · 主力':chain.ranks[1]?.month===m?' · 次主力':''}</option>`).join('');
 $('month').value=state.month;renderProducts();renderChain();saveFavorites();
 if(!quiet)await loadHistory();
 }finally{if(token===state.chainRequest)state.chainLoading=false;}
}
function bestContract(month){let items=state.chain.items.filter(i=>month==='all'||i.month===month);if(state.favoriteMode)items=items.filter(i=>state.favorites.includes(i.code));return items.sort((a,b)=>b.quote.volume-a.quote.volume||b.quote.oi-a.quote.oi)[0];}
async function selectMonth(month){state.month=month;$('month').value=month;renderChain();const next=bestContract(month);if(next){state.code=next.code;await loadHistory();}}
function renderChain(){
 if(!state.chain)return;let items=state.chain.items.filter(i=>state.month==='all'||i.month===state.month);
 if(state.favoriteMode)items=items.filter(i=>state.favorites.includes(i.code));
 if($('onlyQuoted').checked)items=items.filter(i=>spread(i.quote)!==null);
 const groups=new Map();for(const i of items){const k=i.month+':'+i.strike;if(!groups.has(k))groups.set(k,{month:i.month,strike:i.strike});groups.get(k)[i.side]=i;}
 let rows=[...groups.values()];const {metric,side,direction}=state.chainOrder;
 const value=row=>{
  if(metric==='strike')return row.strike;
  const contracts=side==='both'?[row.C,row.P]:[row[side]];
  const values=contracts.filter(Boolean).map(i=>{
   const q=i.quote;
   if(metric==='spread')return spread(q);
   if(metric==='bid'||metric==='ask')return q[metric][0]>0&&q[metric==='bid'?'bv':'av'][0]>0?q[metric][0]:null;
   return q.t>0&&Number.isFinite(q[metric])?q[metric]:null;
  }).filter(v=>v!==null);
  return values.length?Math.max(...values):null;
 };
 rows.sort((a,b)=>{
  const av=value(a),bv=value(b);
  if(av===null&&bv!==null)return 1;
  if(bv===null&&av!==null)return -1;
  return (av!==null&&bv!==null?(av-bv)*(direction==='asc'?1:-1):0)||a.month.localeCompare(b.month)||a.strike-b.strike;
 });
 $('chainSort').value=metric;
 $('sortDirection').textContent=direction==='asc'?'升序 ↑':'降序 ↓';
 const metricName={strike:'行权价',volume:'成交量',oi:'持仓量',spread:'价差率',bid:'买一价',ask:'卖一价'}[metric];
 $('sortNote').textContent=`排序：${metric==='strike'?'':side==='both'?'两侧较大值 · ':side==='C'?'认购 · ':'认沽 · '}${metricName} ${direction==='asc'?'↑':'↓'} · 同行认购认沽保持配对`;
 document.querySelectorAll('[data-chain-sort]').forEach(button=>{
  const active=button.dataset.chainSort===metric&&(metric==='strike'||button.dataset.side===side);
  button.classList.toggle('active',active);
  button.querySelector('.sort-arrow').textContent=active?(direction==='asc'?'↑':'↓'):'↕';
  button.closest('th').setAttribute('aria-sort',active?(direction==='asc'?'ascending':'descending'):'none');
 });
 const cells=(i,call)=>{
  if(!i)return '<td colspan="6" class="empty">—</td>';
  const q=i.quote,sp=spread(q),common=`data-code="${esc(i.code)}" title="${esc(i.code)} · ${esc(timeText(q.t,true))}${q.t>Date.now()?' · QMT时间晚于当前，待核':''}"`,sel=i.code===state.code?'selected':'';
  let values=[['contract-code',i.code.split('.')[0]],['',fmt(q.oi,0)],['',fmt(q.volume,0)],['buy',q.bv[0]>0?price(q.bid[0]):'—'],['sell',q.av[0]>0?price(q.ask[0]):'—'],['',sp===null?'—':fmt(sp)+'%']];
  if(!call)values=[values[5],values[3],values[4],values[2],values[1],values[0]];
  return values.map(([cl,v])=>`<td ${common} class="${cl} ${sel}">${esc(v)}</td>`).join('');
 };
 $('chain').innerHTML=rows.map(r=>`<tr>${cells(r.C,true)}<td class="strike">${fmt(r.strike)}<small>${r.month}</small></td>${cells(r.P,false)}</tr>`).join('')||'<tr><td colspan="13" class="empty-message">当前筛选没有合约</td></tr>';
 $('chainTitle').textContent=`${state.chain.product.name} · ${state.month==='all'?'全部月份':state.month} · ${items.length} 个合约`;
 $('rankNote').textContent=`主力 ${state.chain.ranks[0]?.month||'—'} / 次主力 ${state.chain.ranks[1]?.month||'—'} · ${state.chain.rank_basis}`;
 document.querySelectorAll('#rankButtons button').forEach(b=>b.classList.toggle('active',b.dataset.rank==='all'?state.month==='all':state.chain.ranks[+b.dataset.rank]?.month===state.month));
}
async function loadHistory(refresh=false,quiet=false){
 if(!state.code)return;const token=++state.request,code=state.code,date=$('date').value;
 const preserve=quiet&&state.data?.code===code&&state.data?.date===date;
 state.loading=true;
 if(!preserve){$('loading').textContent='正在读取 '+code+' 的真实盘口…';$('loading').classList.remove('hidden');}
 try{
  const d=await api(`/api/history?code=${encodeURIComponent(code)}&date=${date}${refresh?'&refresh=1':''}`);if(token!==state.request)return;
  d.dailySpread=summarizeDailySpread(d.points);
  state.data=d;state.hover=-1;if(!preserve){state.window=null;state.eventPage=0;}
  setPoints();renderHeader();renderChain();saveFavorites();renderBook(state.points.at(-1));renderEvents();renderStats();draw();
  const count=d.points.length;$('loading').textContent=count?'':'该日暂未返回盘口数据';$('loading').classList.toggle('hidden',count>0);
  notice(`${d.source||'无历史记录'} · ${fmt(count,0)} 条快照 · ${d.date}`);
  $('dataMessage').innerHTML=`${esc(d.source||'暂无历史')} · 自然日 ${esc(date)}　<span>${esc(d.semantics)}</span>`+d.messages.map(m=>`<div class="warn">${esc(m)}</div>`).join('');
 }catch(e){if(token===state.request){$('loading').textContent='读取失败：'+e.message;$('loading').classList.remove('hidden');notice(e.message);}}
 finally{if(token===state.request)state.loading=false;}
}
function setPoints(){
 const mode=$('session').value;state.points=(state.data?.points||[]).filter(p=>{const h=new Date(p.t+8*3600000).getUTCHours();return mode==='all'||(mode==='day'?h>=8&&h<18:h<8||h>=18);});
 state.events=state.points.map((p,i)=>({p,i})).filter(x=>x.p.event);
 if(state.points.length&&(!state.window||state.window[0]<state.points[0].t||state.window[1]>state.points.at(-1).t+1000))state.window=[state.points[0].t,state.points.at(-1).t+1000];
}
function renderHeader(){
 const d=state.data,m=d.meta;$('breadcrumb').textContent=`${({SF:'上期所',DF:'大商所',ZF:'郑商所',GF:'广期所',INE:'能源中心'})[m.market]} / ${m.name} / ${m.side==='C'?'认购 CALL':'认沽 PUT'}`;
 $('instrumentName').textContent=`${m.name} ${m.month} ${m.side==='C'?'购':'沽'} ${fmt(m.strike,4)}`;$('code').textContent=m.code;$('expiry').textContent='到期 '+(m.expiry||'待返回');$('contractUnit').textContent=`每手乘数 ${m.unit||'—'} · 最小变动 ${m.tick||'—'}`;
 const summary=d.dailySpread;
 $('daySpread').textContent=summary.averageSpread===null?'—':fmt(summary.averageSpread,4);
 $('daySpreadPercent').textContent=summary.averagePercent===null?'—':fmt(summary.averagePercent,3)+'%';
 $('daySpreadNote').textContent=`${d.date} 全天 · 有效连续报价 ${fmt(summary.validMinutes,1)} 分钟 · 时间加权`;
}
function renderBook(p){
 $('bookTime').textContent=p?timeText(p.t):'—';
 if(!p){$('book').innerHTML='<div class="empty-message">无可用盘口</div>';$('bookSummary').textContent='';return;}
 const max=Math.max(...p.bv,...p.av,1),row=(side,i)=>{const buy=side==='bid',v=p[buy?'bv':'av'][i],pr=p[side][i],cl=buy?'buy':'sell';return `<div class="book-row ${buy&&i===0?'top-bid':''}"><div class="bar" style="width:${Math.min(100,v/max*100)}%;background:var(--${cl})"></div><span>${buy?'买':'卖'}${i+1}</span><span class="${cl}">${v>0?price(pr):'—'}</span><span>${pr>0&&v>0?fmt(v,0):'—'}</span></div>`;};
 $('book').innerHTML=[4,3,2,1,0].map(i=>row('ask',i)).join('')+[0,1,2,3,4].map(i=>row('bid',i)).join('');
 $('bookSummary').innerHTML=`<span>持仓 ${fmt(p.oi,0)} 手</span><span>盘口量：手</span>`;
}
function filteredEvents(){const side=$('eventSide').value;return state.events.filter(x=>!side||x.p.event.side===side).slice().reverse();}
function renderEvents(){
 const es=filteredEvents(),pages=Math.max(1,Math.ceil(es.length/100));state.eventPage=Math.max(0,Math.min(state.eventPage,pages-1));
 $('events').innerHTML=es.slice(state.eventPage*100,state.eventPage*100+100).map(({p,i})=>`<button class="event-row ${sideClass(p.event.side)}" data-index="${i}" title="${esc(p.event.reason)}；本条量增 ${p.event.qty} 手，可能包含多笔"><span>${timeText(p.t)}</span><span>${price(p.last)}</span><span>${fmt(p.event.qty,0)}</span><span>${sideLabel(p.event.side)}</span></button>`).join('')||'<div class="empty-message">当前时段无量增事件</div>';
 $('eventPage').textContent=`${state.eventPage+1}/${pages} · ${fmt(es.length,0)} 条`;$('eventPrev').disabled=state.eventPage===0;$('eventNext').disabled=state.eventPage===pages-1;
}
function renderStats(){
 const ps=state.points,es=state.events;let volume=0,b=0,s=0,n=0;es.forEach(({p})=>{volume+=p.event.qty;if(p.event.side==='B')b++;else if(p.event.side==='S')s++;else n++;});
 const stats=[['盘口快照',fmt(ps.length,0),''],['B ↑ 事件',fmt(b,0),'buy'],['S ↓ 事件',fmt(s,0),'sell'],['未判定事件',fmt(n,0),'neutral'],['观测成交量增',fmt(volume,0)+' 手',''],['累计量重置',fmt(ps.filter(p=>p.reset).length,0),'']];
 $('statistics').innerHTML=stats.map(([label,value,cl])=>`<div class="stat"><span>${label}</span><strong class="${cl}">${value}</strong></div>`).join('');
}

// Canvas uses every snapshot and every volume-increment event; no candle aggregation.
const canvas=$('chart'),ctx=canvas.getContext('2d');let geometry=null;
function draw(){
 const rect=canvas.getBoundingClientRect(),w=rect.width,h=rect.height,dpr=devicePixelRatio||1;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 if(!state.points.length||!state.window)return;
 const [t0,t1]=state.window,ps=state.points,indices=[];for(let i=0;i<ps.length;i++)if(ps[i].t>=t0&&ps[i].t<=t1)indices.push(i);
 if(!indices.length)return;
 let prices=[];for(const i of indices){const p=ps[i];if(p.bid[0]>0&&p.bv[0]>0)prices.push(p.bid[0]);if(p.ask[0]>0&&p.av[0]>0)prices.push(p.ask[0]);if(p.event&&p.last>0)prices.push(p.last);}
 let lo=prices.length?Infinity:0,hi=prices.length?-Infinity:1;for(const v of prices){lo=Math.min(lo,v);hi=Math.max(hi,v);}const pad=(hi-lo||hi*.01||1)*.12;lo-=pad;hi+=pad;
 const left=62,right=w-15,top=20,bottom=h*.62,depthTop=h*.68,depthBottom=h*.79,volTop=h*.84,volBottom=h-24;
 const x=t=>left+(t-t0)/(t1-t0)*(right-left),y=p=>bottom-(p-lo)/(hi-lo)*(bottom-top);
 geometry={left,right,top,bottom,x,y,t0,t1,w,h};ctx.font='12px Consolas';ctx.lineWidth=1;
 for(let k=0;k<=5;k++){const yy=top+(bottom-top)*k/5;ctx.strokeStyle='#2b343e';ctx.beginPath();ctx.moveTo(left,yy+.5);ctx.lineTo(right,yy+.5);ctx.stroke();ctx.fillStyle='#8595a9';ctx.textAlign='right';ctx.fillText(fmt(hi-(hi-lo)*k/5,3),left-9,yy+3);}
 for(let k=0;k<=6;k++){const xx=left+(right-left)*k/6;ctx.strokeStyle='#28313a';ctx.beginPath();ctx.moveTo(xx+.5,top);ctx.lineTo(xx+.5,volBottom);ctx.stroke();ctx.fillStyle='#7d8fa5';ctx.textAlign=k===0?'left':k===6?'right':'center';ctx.fillText(timeText(t0+(t1-t0)*k/6).slice(0,8),xx,h-7);}
 ctx.textAlign='left';ctx.fillStyle='#71859a';ctx.fillText('挂单量 / 手',left,depthTop-5);ctx.fillText('成交量增 / 手',left,volTop-5);
 for(const key of ['bid','ask']){ctx.strokeStyle=key==='bid'?'#f66f78':'#35ceb0';ctx.lineWidth=1.25;ctx.beginPath();let prev=null;for(const i of indices){const p=ps[i],v=p[key][0],valid=v>0&&p[key==='bid'?'bv':'av'][0]>0;if(!valid){prev=null;continue;}if(prev&&!p.gap&&!p.reset&&p.t-prev.t<=60000){ctx.lineTo(x(p.t),y(prev[key][0]));ctx.lineTo(x(p.t),y(v));}else ctx.moveTo(x(p.t),y(v));prev=p;}ctx.stroke();}
 let maxDepth=1;for(const i of indices)maxDepth=Math.max(maxDepth,ps[i].bv[0],ps[i].av[0]);
 for(const key of ['bv','av']){ctx.strokeStyle=key==='bv'?'#bc6571':'#41a793';ctx.lineWidth=.8;ctx.beginPath();let prev=null;for(const i of indices){const p=ps[i],xx=x(p.t),yy=depthBottom-p[key][0]/maxDepth*(depthBottom-depthTop);if(prev&&!p.gap&&!p.reset){ctx.lineTo(xx,depthBottom-prev[key][0]/maxDepth*(depthBottom-depthTop));ctx.lineTo(xx,yy);}else ctx.moveTo(xx,yy);prev=p;}ctx.stroke();}
 ctx.fillStyle='#71859a';ctx.textAlign='right';ctx.fillText(fmt(maxDepth,0),left-8,depthTop+4);
 const events=indices.filter(i=>ps[i].event);let maxVol=1;for(const i of events)maxVol=Math.max(maxVol,ps[i].event.qty);
 ctx.fillText(fmt(maxVol,0),left-8,volTop+4);
 for(const i of events){const p=ps[i],xx=x(p.t),yy=p.last>0?y(p.last):bottom-4,side=p.event.side;ctx.strokeStyle=ctx.fillStyle=side==='B'?'#fa7a83':side==='S'?'#38d6b4':'#a6b1c2';ctx.lineWidth=1;ctx.beginPath();
  if(side==='B'){ctx.moveTo(xx,yy+12);ctx.lineTo(xx,yy+2);ctx.moveTo(xx-3,yy+6);ctx.lineTo(xx,yy+2);ctx.lineTo(xx+3,yy+6);}
  else if(side==='S'){ctx.moveTo(xx,yy-12);ctx.lineTo(xx,yy-2);ctx.moveTo(xx-3,yy-6);ctx.lineTo(xx,yy-2);ctx.lineTo(xx+3,yy-6);}
  else{ctx.moveTo(xx,yy-3);ctx.lineTo(xx+3,yy);ctx.lineTo(xx,yy+3);ctx.lineTo(xx-3,yy);ctx.closePath();}ctx.stroke();
  ctx.globalAlpha=.7;ctx.fillRect(xx-.65,volBottom-p.event.qty/maxVol*(volBottom-volTop),1.3,Math.max(1,p.event.qty/maxVol*(volBottom-volTop)));ctx.globalAlpha=1;
 }
 if(state.hover>=0&&ps[state.hover]){const p=ps[state.hover];ctx.strokeStyle='#8da4b9';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x(p.t),top);ctx.lineTo(x(p.t),volBottom);if(state.mouse){ctx.moveTo(left,state.mouse.y);ctx.lineTo(right,state.mouse.y);}ctx.stroke();ctx.setLineDash([]);}
 $('rangeLabel').textContent=`${timeText(t0)} — ${timeText(t1)} · 当前窗口 ${fmt(indices.length,0)} 条`;
}
function nearest(t){const ps=state.points;let l=0,r=ps.length-1;while(l<r){const m=(l+r)>>1;if(ps[m].t<t)l=m+1;else r=m;}return l>0&&Math.abs(ps[l-1].t-t)<Math.abs(ps[l].t-t)?l-1:l;}
function showHover(index,mx,my){const p=state.points[index];if(!p)return;state.hover=index;state.mouse={x:mx,y:my};renderBook(p);$('bookTitle').textContent='悬停时点五档';const e=p.event;
 $('tooltip').innerHTML=`<div class="tip-time">${timeText(p.t,true)}.${String(p.t%1000).padStart(3,'0')}</div><div class="buy">买一 ${p.bv[0]>0?price(p.bid[0]):'—'}　${fmt(p.bv[0],0)} 手</div><div class="sell">卖一 ${p.av[0]>0?price(p.ask[0]):'—'}　${fmt(p.av[0],0)} 手</div><div>价差 ${spread(p)===null?'—':fmt(spread(p))+'%'}</div>${e?`<div class="${sideClass(e.side)}">${sideLabel(e.side)}　末笔 ${price(p.last)}　量增 ${fmt(e.qty,0)} 手</div><div class="tip-reason">${esc(e.reason)}</div>`:'<div class="tip-reason">此帧无正成交量增</div>'}`;
 const tip=$('tooltip');tip.classList.remove('hidden');tip.style.left=Math.max(4,Math.min(mx+16,canvas.clientWidth-tip.offsetWidth-6))+'px';tip.style.top=Math.max(4,Math.min(my+12,canvas.clientHeight-tip.offsetHeight-6))+'px';draw();}
function zoom(factor,center){if(!state.window||state.points.length<2)return;const min=state.points[0].t,max=state.points.at(-1).t+1000,[a,b]=state.window;center=center??(a+b)/2;const range=Math.min(max-min,Math.max(5000,(b-a)*factor)),ratio=(center-a)/(b-a);let start=center-range*ratio;start=Math.max(min,Math.min(max-range,start));state.window=[start,start+range];state.hover=-1;$('tooltip').classList.add('hidden');draw();}
let drag=null;
canvas.addEventListener('wheel',e=>{e.preventDefault();if(!geometry)return;const r=canvas.getBoundingClientRect(),g=geometry,t=g.t0+(e.clientX-r.left-g.left)/(g.right-g.left)*(g.t1-g.t0);zoom(e.deltaY>0?1.4:1/1.4,t);},{passive:false});
canvas.addEventListener('pointerdown',e=>{if(!state.window)return;drag={x:e.clientX,window:[...state.window]};canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointerup',()=>drag=null);
canvas.addEventListener('pointercancel',()=>drag=null);
canvas.addEventListener('pointermove',e=>{if(!geometry||!state.points.length)return;const r=canvas.getBoundingClientRect(),g=geometry,mx=e.clientX-r.left,my=e.clientY-r.top;
 if(drag){const [a,b]=drag.window,delta=(e.clientX-drag.x)/(g.right-g.left)*(b-a),min=state.points[0].t,max=state.points.at(-1).t+1000;const start=Math.max(min,Math.min(max-(b-a),a-delta));state.window=[start,start+b-a];state.hover=-1;$('tooltip').classList.add('hidden');draw();return;}
 if(mx>=g.left&&mx<=g.right)showHover(nearest(g.t0+(mx-g.left)/(g.right-g.left)*(g.t1-g.t0)),mx,my);
});
canvas.addEventListener('pointerleave',()=>{if(drag)return;state.hover=-1;state.mouse=null;$('tooltip').classList.add('hidden');$('bookTitle').textContent='图末五档';renderBook(state.points.at(-1));draw();});
new ResizeObserver(()=>draw()).observe($('chartWrap'));

$('products').addEventListener('click',e=>{const b=e.target.closest('[data-product]');if(b)selectProduct(b.dataset.product).catch(e=>notice(e.message));});
$('exchanges').addEventListener('click',e=>{const b=e.target.closest('[data-ex]');if(!b)return;state.exchange=b.dataset.ex;document.querySelectorAll('[data-ex]').forEach(x=>x.classList.toggle('active',x===b));renderProducts();});
$('chain').addEventListener('click',e=>{const td=e.target.closest('[data-code]');if(td){state.code=td.dataset.code;document.body.classList.remove('chain-mode');$('viewBook').classList.add('active');$('viewChain').classList.remove('active');loadHistory();}});
$('month').addEventListener('change',()=>selectMonth($('month').value));
$('rankButtons').addEventListener('click',e=>{const b=e.target.closest('[data-rank]');if(!b)return;const rank=b.dataset.rank;selectMonth(rank==='all'?'all':state.chain.ranks[+rank]?.month||state.month);});
$('date').addEventListener('change',()=>loadHistory());$('refresh').addEventListener('click',()=>loadHistory(true));
$('session').addEventListener('change',()=>{state.window=null;state.eventPage=0;setPoints();renderHeader();renderBook(state.points.at(-1));renderEvents();renderStats();draw();$('loading').textContent='所选时段没有返回盘口';$('loading').classList.toggle('hidden',state.points.length>0);});
$('onlyQuoted').addEventListener('change',renderChain);
$('chainSort').addEventListener('change',()=>{const metric=$('chainSort').value;state.chainOrder={metric,side:'both',direction:metric==='strike'?'asc':'desc'};renderChain();});
$('sortDirection').addEventListener('click',()=>{state.chainOrder.direction=state.chainOrder.direction==='asc'?'desc':'asc';renderChain();});
document.querySelector('.chain-table thead').addEventListener('click',e=>{
 const button=e.target.closest('[data-chain-sort]');if(!button)return;
 const metric=button.dataset.chainSort,side=button.dataset.side||'both',previous=state.chainOrder;
 const same=previous.metric===metric&&previous.side===side;
 state.chainOrder={metric,side,direction:same?(previous.direction==='asc'?'desc':'asc'):(metric==='strike'?'asc':'desc')};
 renderChain();
});
$('eventSide').addEventListener('change',()=>{state.eventPage=0;renderEvents();});$('eventPrev').addEventListener('click',()=>{state.eventPage--;renderEvents();});$('eventNext').addEventListener('click',()=>{state.eventPage++;renderEvents();});
$('events').addEventListener('click',e=>{const row=e.target.closest('[data-index]');if(!row)return;const i=+row.dataset.index,p=state.points[i],min=state.points[0].t,max=state.points.at(-1).t+1000;state.window=[Math.max(min,p.t-60000),Math.min(max,p.t+60000)];state.hover=i;draw();renderBook(p);$('bookTitle').textContent='所选事件五档';});
$('zoomIn').addEventListener('click',()=>zoom(.5));$('zoomOut').addEventListener('click',()=>zoom(2));$('resetZoom').addEventListener('click',()=>{state.window=null;setPoints();draw();});
$('export').addEventListener('click',()=>{if(!state.data)return;const blob=new Blob([JSON.stringify(state.data)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${state.code}_${state.data.date}_盘口与方向推断.json`;a.click();URL.revokeObjectURL(url);});
$('star').addEventListener('click',()=>{if(state.favorites.includes(state.code))state.favorites=state.favorites.filter(c=>c!==state.code);else state.favorites.push(state.code);saveFavorites();if(state.favoriteMode){renderProducts();renderChain();}});
$('viewBook').addEventListener('click',()=>{document.body.classList.remove('chain-mode');$('viewBook').classList.add('active');$('viewChain').classList.remove('active');draw();});
$('viewChain').addEventListener('click',()=>{document.body.classList.add('chain-mode');$('viewChain').classList.add('active');$('viewBook').classList.remove('active');});
$('viewFavorites').addEventListener('click',()=>{state.favoriteMode=!state.favoriteMode;$('viewFavorites').classList.toggle('active',state.favoriteMode);renderProducts();renderChain();if(state.favoriteMode&&!state.favorites.length)notice('点击合约名称旁的 ☆ 自选，建立你的观察列表');});
$('helpButton').addEventListener('click',()=>$('help').showModal());$('closeHelp').addEventListener('click',()=>$('help').close());
let searchTimer,searchToken=0;
$('search').addEventListener('input',()=>{renderProducts();clearTimeout(searchTimer);const token=++searchToken,q=$('search').value.trim();if(!q){$('searchResults').classList.add('hidden');return;}searchTimer=setTimeout(async()=>{try{const rows=await api('/api/search?q='+encodeURIComponent(q));if(token!==searchToken)return;$('searchResults').innerHTML=rows.map(i=>`<button class="search-result" data-search-code="${esc(i.code)}" data-search-product="${esc(i.product)}">${esc(i.code)}<span>${esc(i.name)} · ${i.month} · ${i.side==='C'?'认购':'认沽'} ${fmt(i.strike)}</span></button>`).join('')||'<div class="empty-message">无匹配合约</div>';$('searchResults').classList.remove('hidden');}catch(e){notice(e.message);}},250);});
$('searchResults').addEventListener('click',e=>{const b=e.target.closest('[data-search-code]');if(!b)return;$('search').value='';$('searchResults').classList.add('hidden');selectProduct(b.dataset.searchProduct,b.dataset.searchCode).catch(e=>notice(e.message));});
document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))$('searchResults').classList.add('hidden');});
async function boot(){try{saveFavorites();await catalog();if(!state.catalog.products.some(p=>p.id===state.product))state.product=state.catalog.products[0]?.id;await selectProduct(state.product,state.code);}catch(e){notice(e.message);$('loading').textContent='加载失败：'+e.message;}}
boot();
let polling=false;
setInterval(async()=>{if(polling||state.loading||state.chainLoading||document.hidden||!$('live').checked)return;polling=true;try{await catalog();if(state.chain)await selectProduct(state.product,null,true);const today=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});if(state.data&&!state.loading&&(state.data.date===today||state.needsBackfill)){const backfill=state.needsBackfill;state.needsBackfill=false;await loadHistory(!!backfill,true);}}catch(e){notice('刷新失败：'+e.message);}finally{polling=false;}},5000);
