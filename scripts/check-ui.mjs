// End-to-end browser check against the real server with a fake Claude Code.
// Run: node scripts/check-ui.mjs  (needs Chrome; set CHROME_PATH if it is elsewhere)
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {browser,sleep} from './browser.mjs';

const root=path.resolve(import.meta.dirname,'..'),port=8878,base=`http://127.0.0.1:${port}`;
const data=path.join(root,'artifacts','ui-check-'+Date.now());
await fs.mkdir(data,{recursive:true});
const server=spawn(path.join(root,'.venv','Scripts','python.exe'),[path.join(root,'scripts','cli_fixture.py')],{
  cwd:root,env:{...process.env,MEDIA_DATA_DIR:data,CLI_TEST_PORT:String(port)},windowsHide:true,stdio:['ignore','pipe','pipe']});
let log='';server.stdout.on('data',d=>{log+=d});server.stderr.on('data',d=>{log+=d});
let b;const checks=[];
const click=selector=>`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('missing '+${JSON.stringify(selector)});el.click();})()`;
try {
  let ready=false;
  for(let i=0;i<200;i++){try{ready=(await fetch(base+'/api/environment')).ok;if(ready)break;}catch{}await sleep(100);}
  assert.ok(ready,log);
  b=await browser(root,9238);
  const {cdp,evaluate,waitFor,shot,errors}=b;
  await cdp('Page.navigate',{url:base});
  await waitFor(`!!document.querySelector('#creation-idea')&&!!document.querySelector('[data-action="use-prompt"]')`);
  await shot('ui-home');

  // A prompt button only fills the box; the box is what gets sent.
  await evaluate(`document.querySelector('#creation-idea').value='城市夜跑';document.querySelector('[data-action="use-prompt"][data-id="research"]').click()`);
  const filled=await evaluate(`document.querySelector('#creation-idea').value`);
  assert.ok(filled.startsWith('围绕下面的想法做调研')&&filled.trimEnd().endsWith('城市夜跑'),filled);
  checks.push('prompt chip fills the home box');
  await evaluate(click('[data-action="create-project"]'));
  await waitFor(`location.hash.startsWith('#workspace/')&&!!document.querySelector('#conversation .turn')`);
  await waitFor(`!!document.querySelector('#conversation .turn-video video')`,30000);
  assert.equal(await evaluate(`S.detail.tasks[0].payload.prompt`),filled);
  assert.equal(await evaluate(`S.detail.project.name`),'城市夜跑');
  assert.match(await evaluate(`document.querySelector('.turn-reply').textContent`),/调研写在/);
  assert.ok(await evaluate(`[...document.querySelectorAll('.file-row')].some(el=>el.textContent.includes('需求.md'))`));
  await evaluate(`document.querySelector('#conversation video').muted=true;document.querySelector('#conversation video').play()`);
  await waitFor(`document.querySelector('#conversation video').currentTime>.2`);
  await evaluate(`document.querySelector('#conversation video').pause()`);
  await shot('ui-workspace');checks.push('run shows reply, files and a playable video');

  // Editing a file Claude wrote.
  await evaluate(`[...document.querySelectorAll('.file-row')].find(el=>el.textContent.includes('需求.md')).click()`);
  await waitFor(`!!document.querySelector('#file-editor')`);
  await evaluate(`document.querySelector('#file-editor').value='# 我改过的需求';document.querySelector('[data-action="save-file"]').click()`);
  await waitFor(`!document.querySelector('#modal').open`);
  assert.equal(await evaluate(`fetch(workURL('需求.md')).then(r=>r.text())`),'# 我改过的需求');
  checks.push('file editor saves back to the working folder');

  // 调研 tab: research.json and directions.json.
  await evaluate(click('[data-action="workspace-tab"][data-tab="research"]'));
  await waitFor(`location.hash.endsWith('/research')&&!!document.querySelector('.research-overview')`);
  assert.equal(await evaluate(`document.querySelector('.research-title').textContent`),'城市夜跑为什么流行');
  assert.equal(await evaluate(`document.querySelectorAll('.fact-list>div').length`),2);
  assert.match(await evaluate(`document.querySelector('.fact-list').textContent`),/未核实/);
  assert.equal(await evaluate(`document.querySelectorAll('.angle-card').length`),2);
  assert.ok(await evaluate(`!!document.querySelector('#tab-body [data-action="use-prompt"][data-id="more-directions"]')`));
  await shot('ui-research');checks.push('调研 tab shows research.json and direction cards');

  // A direction card fills the composer with the script prompt; nothing is sent yet.
  const before=await evaluate(`S.detail.tasks.length`);
  await evaluate(click('[data-action="use-direction"]'));
  const composer=await evaluate(`document.querySelector('#composer').value`);
  assert.ok(composer.includes('从一次夜跑讲城市的松弛感')&&composer.includes('脚本.md'),composer);
  assert.equal(await evaluate(`S.detail.tasks.length`),before);
  assert.equal(await evaluate(`document.querySelector('#composer-continue').checked`),true);
  await evaluate(click('[data-action="send"]'));
  await waitFor(`S.detail.tasks.length===${before+1}&&location.hash.endsWith('/conversation')&&document.querySelectorAll('#conversation .turn').length===2`);
  await waitFor(`!S.detail.tasks.some(t=>['queued','running'].includes(t.status))`,30000);
  assert.equal(await evaluate(`S.detail.tasks[0].payload.prompt`),composer.trim());
  assert.equal(await evaluate(`S.detail.tasks[0].payload.fresh`),false);
  assert.equal(await evaluate(`document.querySelector('#composer').value`),'');
  checks.push('direction → composer → follow-up run in the same conversation');

  // 文案 tab: 脚本.md rendered, rewrite buttons are prompts, whole-document edit.
  await evaluate(click('[data-action="workspace-tab"][data-tab="script"]'));
  await waitFor(`!!document.querySelector('.markdown-body h3')`);
  assert.equal(await evaluate(`document.querySelector('.markdown-body h3').textContent`),'01 开场');
  assert.ok(await evaluate(`!!document.querySelector('.markdown-body a[href="https://example.org/a"]')`));
  await evaluate(click('#tab-body [data-action="use-prompt"][data-id="script-shorter"]'));
  assert.match(await evaluate(`document.querySelector('#composer').value`),/压缩/);
  await evaluate(`document.querySelector('#composer').value=''`);
  await shot('ui-script');
  await evaluate(click('[data-action="script-edit"]'));
  await waitFor(`!!document.querySelector('#script-editor')`);
  await evaluate(`document.querySelector('#script-editor').value='# 脚本\\n\\n## 改过的开场\\n\\n大家好';document.querySelector('#script-editor').dispatchEvent(new Event('input',{bubbles:true}))`);
  await evaluate(click('[data-action="script-save"]'));
  await waitFor(`document.querySelector('.markdown-body h3')?.textContent==='改过的开场'`);
  assert.equal(await evaluate(`fetch(workURL('脚本.md')).then(r=>r.text())`),'# 脚本\n\n## 改过的开场\n\n大家好');
  checks.push('文案 tab renders 脚本.md, rewrite buttons fill the box, edits save');

  // Prompt library: edit a prompt and see the chip change.
  await evaluate(click('[data-page="prompts"]'));
  await waitFor(`!!document.querySelector('[data-action="edit-prompt"]')&&document.querySelectorAll('.library-item').length>8`);
  await shot('ui-prompts');
  await evaluate(click('[data-action="edit-prompt"][data-id="from-scratch"]'));
  await waitFor(`!!document.querySelector('#prompt-body')`);
  await evaluate(`document.querySelector('#prompt-label').value='纯动画';document.querySelector('#prompt-body').value='只用动画，主题：';document.querySelector('[data-action="save-prompt"]').click()`);
  await waitFor(`S.prompts.find(p=>p.id==='from-scratch').label==='纯动画'`);
  await evaluate(click('[data-page="home"]'));
  await waitFor(`[...document.querySelectorAll('[data-action="use-prompt"]')].some(el=>el.textContent==='纯动画')`);
  checks.push('prompt edits change the buttons');

  for(const page of ['inspiration','projects','films','library','settings']){
    await evaluate(click(`[data-page="${page}"]`));
    await waitFor(`S.page===${JSON.stringify(page)}&&!!document.querySelector('.page-heading,.collection-shell')`);
    if(page==='films')assert.equal(await evaluate(`document.querySelectorAll('.library-card video').length`),2);
  }
  await evaluate(click('[data-page="settings"]'));
  await waitFor(`!!document.querySelector('#cli-path')&&!!document.querySelector('#ffmpeg-path')`);
  await shot('ui-settings');checks.push('every page renders');

  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:2,mobile:true});
  await evaluate(`location.hash='#workspace/'+S.selected`);
  await waitFor(`!!document.querySelector('#composer')`);
  assert.ok(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth+1`),'horizontal overflow on phone width');
  await shot('ui-workspace-phone');checks.push('workspace fits a phone screen');

  assert.deepEqual(errors,[]);
  console.log('UI check passed:\n- '+checks.join('\n- '));
} catch(error) {
  console.error(error);console.error(log.slice(-3000));process.exitCode=1;
} finally {b?.close();server.kill();}
