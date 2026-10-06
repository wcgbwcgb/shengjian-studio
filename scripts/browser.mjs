import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';

export const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export async function browser(root,port=9233) {
  const artifacts=path.join(root,'artifacts');
  await fs.mkdir(artifacts,{recursive:true});
  const chrome=process.env.CHROME_PATH||'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const proc=spawn(chrome,['--headless=new','--no-sandbox','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking',`--remote-debugging-port=${port}`,`--user-data-dir=${path.join(artifacts,'v2-browser-'+port)}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
  let pages,ws;
  try {
    for(let i=0;i<80;i++){try{pages=await(await fetch(`http://127.0.0.1:${port}/json`)).json();break;}catch{await sleep(100);}}
    if(!pages)throw new Error('Chrome did not start');
    ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    let id=0;const pending=new Map(),errors=[];
    ws.onmessage=event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);pending.delete(data.id);data.error?p.reject(new Error(data.error.message)):p.resolve(data.result);}else if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.exception?.description||data.params.exceptionDetails.text);};
    const cdp=(method,params={})=>new Promise((resolve,reject)=>{const ident=++id;pending.set(ident,{resolve,reject});ws.send(JSON.stringify({id:ident,method,params}));});
    const evaluate=async expression=>{const result=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;};
    const waitFor=async(expression,ms=15000)=>{for(let i=0;i<ms/100;i++){if(await evaluate(expression))return;await sleep(100);}throw new Error('Timed out: '+expression+'\n'+await evaluate(`document.querySelector('#toast')?.textContent`));};
    const shot=async name=>{const img=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});await fs.writeFile(path.join(artifacts,name+'.png'),Buffer.from(img.data,'base64'));};
    await cdp('Page.enable');await cdp('Runtime.enable');
    await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1060,deviceScaleFactor:1,mobile:false});
    return {cdp,evaluate,waitFor,shot,errors,close:()=>{ws.close();proc.kill();}};
  }catch(error){ws?.close();proc.kill();throw error;}
}
