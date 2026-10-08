// Local-only source-exact screenshot capture. Requires Node >=22 and Chromium.
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';

const args=Object.fromEntries(process.argv.slice(2).reduce((a,v,i,all)=>i%2===0?[...a,[v,all[i+1]]]:a,[]));
const jobs=JSON.parse(await fs.readFile(args['--jobs'],'utf8'));
const tempRoot=path.resolve(args['--temp-root']);
await fs.mkdir(tempRoot,{recursive:true});
const macApps=['Google Chrome.app/Contents/MacOS/Google Chrome','Microsoft Edge.app/Contents/MacOS/Microsoft Edge','Chromium.app/Contents/MacOS/Chromium'];
const candidates=[args['--browser'],process.env.SCRIPT_PREVIEW_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',...macApps.map(app=>path.join('/Applications',app)),...macApps.map(app=>path.join(os.homedir(),'Applications',app)),'/usr/bin/google-chrome','/usr/bin/chromium'].filter(Boolean);
let browser;
for(const p of candidates){try{await fs.access(p);browser=p;break;}catch{}}
if(!browser) throw new Error('Chrome/Edge not found; pass --browser. No download attempted.');
const server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));
const port=server.address().port;await new Promise(r=>server.close(r));
const profile=await fs.mkdtemp(path.join(tempRoot,'preview-browser-'));
let child;let launchError;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
let ws;let seq=0;const pending=new Map();const errors=[];
function call(method,params={}){const id=++seq;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},20000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));});}
async function js(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
const results=[];
try{
  child=spawn(browser,['--headless=new','--disable-gpu','--disable-background-networking','--no-first-run','--no-default-browser-check',`--remote-debugging-address=127.0.0.1`,`--remote-debugging-port=${port}`,`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore',windowsHide:true});
  child.on('error',error=>{launchError=error;});
  let page;
  for(let n=0;n<80;n++){if(launchError)throw launchError;try{const pages=await (await fetch(`http://127.0.0.1:${port}/json`,{signal:AbortSignal.timeout(1000)})).json();page=pages.find(x=>x.type==='page');if(page)break;}catch{}await delay(150);}
  if(!page)throw new Error('Headless browser startup failed');
  ws=new WebSocket(page.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.addEventListener('open',r,{once:true});ws.addEventListener('error',j,{once:true});});
  ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params);const p=pending.get(m.id);if(p){clearTimeout(p.timer);pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}});
  await call('Page.enable');await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1120,height:1000,deviceScaleFactor:2,mobile:false});
  for(const job of jobs){
    await call('Page.navigate',{url:pathToFileURL(job.html_path).href});
    let ready=false;for(let n=0;n<60;n++){if(await js('document.readyState==="complete" && !!document.querySelector(".unit")')){ready=true;break;}await delay(100);}
    if(!ready)throw new Error('Preview did not finish loading');
    await js('document.fonts.ready.then(()=>true)');
    const actual=await js('[...document.querySelectorAll(".unit")].map(e=>({id:e.dataset.unit,text:e.textContent}))');
    if(JSON.stringify(actual)!==JSON.stringify(job.expected_units))throw new Error('Exact source text mismatch');
    const layout=await js('({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,overflow:[...document.querySelectorAll("main *")].some(e=>e.getBoundingClientRect().right>1121)})');
    if(layout.width!==1120||layout.overflow||layout.height>14000)throw new Error(`Invalid preview layout: ${JSON.stringify(layout)}`);
    const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1120,height:layout.height,scale:1}});
    await fs.writeFile(job.png_path,Buffer.from(shot.data,'base64'));
    results.push({file:job.png_path,units:actual.length,exact_source:true,no_horizontal_overflow:true,css_width:1120,css_height:layout.height});
  }
  if(errors.length)throw new Error('Browser runtime errors');
  await fs.writeFile(path.join(path.dirname(args['--jobs']),'capture-check.json'),JSON.stringify({status:'PASS',results},null,2));
  process.stdout.write(JSON.stringify({status:'PASS',images:results.length,units_checked:results.map(r=>r.units)})+'\n');
}finally{
  if(ws?.readyState===WebSocket.OPEN){try{await call('Browser.close');}catch{}ws.close();}
  if(child?.pid&&child.exitCode===null){await Promise.race([new Promise(r=>child.once('exit',r)),delay(3000)]);if(child.exitCode===null)child.kill();}
  // Only the unique profile we created under this explicitly resolved temp root.
  const absolute=path.resolve(profile),relative=path.relative(tempRoot,absolute);
  if(relative&&!relative.startsWith('..')&&!path.isAbsolute(relative)){
    for(let n=0;n<5;n++){try{await fs.rm(absolute,{recursive:true,force:true});break;}catch(e){if(n===4)throw e;await delay(300);}}
  }
}
