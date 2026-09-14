// Local generated-page QA through a dedicated headless Chrome process.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';

const out=path.resolve(process.argv[2]);
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'option-report-qa-'));
const browser=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',[
  '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const pending=new Map();const errors=[];let socket;let id=0;
function send(method,params={}){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});socket.send(JSON.stringify({id:n,method,params}));})}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value}
try{
  for(let i=0;i<100&&!fs.existsSync(path.join(profile,'DevToolsActivePort'));i++)await sleep(100);
  const port=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0];
  const pages=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve);socket.addEventListener('error',reject)});
  socket.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);if(m.error)p.reject(new Error(JSON.stringify(m.error)));else p.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails)});
  await send('Page.enable');await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1150,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:pathToFileURL(path.join(out,'过滤对比.html')).href});
  let ready=false;
  for(let i=0;i<150;i++){if(await evaluate('window.reportReady===true')){ready=true;break}await sleep(200)}
  if(!ready)throw new Error('Initial plots not ready');
  const checks=[];
  checks.push(await evaluate(`({name:'initial',rows:document.querySelectorAll('#matrix tbody tr').length,book:document.getElementById('book').data.length,equity:document.getElementById('equity').data.length})`));
  const screenshot=await send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(out,'页面核验_总览.png'),Buffer.from(screenshot.data,'base64'));
  await evaluate('window.scrollTo(0,900)');await sleep(300);
  fs.writeFileSync(path.join(out,'页面核验_图表.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
  for(const mode of ['improve_l1','improve_single','improve_single_d500','improve_single_d1000','queue_single_d500']){
    checks.push(await evaluate(`(async()=>{document.getElementById('mode').value=${JSON.stringify(mode)};drawTable();await draw();return {mode:document.getElementById('mode').value,traces:document.getElementById('book').data.length,rows:document.querySelectorAll('#matrix tbody tr').length};})()`));
  }
  checks.push(await evaluate(`(async()=>{document.getElementById('mode').value='improve_single_d500';document.getElementById('contract').value='10012348.SHO';document.getElementById('profile').value='risk';await draw();document.getElementById('worst').click();return {name:'risk_and_zoom',risk:document.getElementById('detail').innerText};})()`));
  checks.push(await evaluate(`(async()=>{document.getElementById('contract').value='90007929.SZO';await draw();return {name:'zero_trades',text:document.getElementById('stats').innerText};})()`));
  const horizontal=await evaluate('document.documentElement.scrollWidth>window.innerWidth');
  if(errors.length||horizontal)throw new Error(JSON.stringify({errors,horizontal}));
  if(checks[0].rows!==9||checks[0].book!==6||checks[0].equity!==2)throw new Error('Unexpected initial trace/table counts');
  fs.writeFileSync(path.join(out,'html_qa.json'),JSON.stringify({checks,errors,horizontal,pageReady:true},null,2));
  console.log('HTML QA passed:',checks.length,'checks; zero runtime errors; screenshots saved.');
}finally{
  if(socket?.readyState===1){try{await send('Browser.close')}catch{}}
  socket?.close();browser.kill();
}
