// This test starts its own isolated backend; it never writes to creator data.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {browser,sleep} from './browser.mjs';
const root=path.resolve(import.meta.dirname,'..'),data=path.join(root,'artifacts',`key-manager-test-${Date.now()}`),base='http://127.0.0.1:8878';
await fs.mkdir(data,{recursive:true});
const backend=spawn(path.join(root,'.venv','Scripts','python.exe'),['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8878'],{cwd:root,env:{...process.env,MEDIA_DATA_DIR:data,ANTHROPIC_API_KEY:''},windowsHide:true,stdio:'ignore'});
let b;
try {
  let ready=false;for(let i=0;i<80;i++){try{if((await fetch(base+'/api/settings')).ok){ready=true;break;}}catch{}await sleep(100);}
  assert.equal(ready,true);
  b=await browser(root,9236);
  const {evaluate,waitFor}=b, click=s=>evaluate(`document.querySelector(${JSON.stringify(s)}).click()`),fill=(s,t)=>evaluate(`document.querySelector(${JSON.stringify(s)}).value=${JSON.stringify(t)}`);
  await b.cdp('Page.navigate',{url:base});await waitFor(`!!document.querySelector('#creation-idea')`);
  await click('#open-settings');await waitFor(`!!document.querySelector('.key-manager')`);
  assert.equal(await evaluate(`document.querySelectorAll('#connection-key').length`),0);
  await click('[data-action="connection-add"]');assert.equal(await evaluate(`document.querySelectorAll('[data-profile-model] option[value="gpt-6-luna"]').length`),3);assert.equal(await evaluate(`document.querySelectorAll('[data-profile-model] option[value^="claude-"]').length`),0);await fill('#profile-name','My OpenAI');await fill('#profile-key','not-a-real-key');
  await click('[data-action="connection-save"]');await waitFor(`document.querySelectorAll('.key-profile').length===1`);
  assert.equal(await evaluate(`document.querySelector('.key-manager').textContent.includes('not-a-real-key')`),false);
  await click('[data-action="connection-add"]');await fill('#profile-name','Second gateway');await fill('#profile-key','second-test-key');
  await click('[data-action="connection-save"]');await waitFor(`document.querySelectorAll('.key-profile').length===2`);
  await click('[data-action="connection-activate"]');await waitFor(`document.querySelector('.key-profile:nth-of-type(2)').textContent.includes('当前使用')`);
  await click('[data-action="connection-edit"]');assert.equal(await evaluate(`document.querySelector('#profile-key').value`),'');
  await fill('#profile-name','Renamed OpenAI');await click('[data-action="connection-save"]');await waitFor(`document.querySelector('.key-manager').textContent.includes('Renamed OpenAI')`);
  await click('[data-action="connection-delete"]');await click('[data-action="connection-delete-confirm"]');await waitFor(`document.querySelectorAll('.key-profile').length===1`);
  await b.shot('v2-key-manager');
  await b.cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),true);await b.shot('v2-key-manager-mobile');
  assert.deepEqual(b.errors,[]);
  console.log('Isolated API Key UI: add, switch, edit, retain secret, delete and mobile: PASS');
} finally {b?.close();backend.kill();}
