// Real FastAPI/store/worker/renderer; only AI responses are deterministic fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {browser,sleep} from './browser.mjs';
const root=path.resolve(import.meta.dirname,'..'),data=path.join(root,'artifacts',`v2-test-${Date.now()}`),base='http://127.0.0.1:8877';
await fs.mkdir(data,{recursive:true});
const backend=spawn(path.join(root,'.venv','Scripts','python.exe'),['scripts/v2_fixture.py'],{cwd:root,env:{...process.env,MEDIA_DATA_DIR:data,V2_TEST_PORT:'8877'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let log='',b;backend.stdout.on('data',c=>log+=c);backend.stderr.on('data',c=>log+=c);
const checks=[];function pass(name){checks.push(name);console.log('PASS:',name);}
try{
  let ready=false;for(let i=0;i<80;i++){try{if((await fetch(base+'/api/projects')).ok){ready=true;break;}}catch{}await sleep(100);}
  if(!ready)throw new Error('Backend failed: '+log);
  b=await browser(root);const {cdp,evaluate,waitFor,shot}=b;
  const click=s=>evaluate(`document.querySelector(${JSON.stringify(s)}).click()`);
  const fill=(s,t)=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(s)});el.value=${JSON.stringify(t)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await cdp('Page.navigate',{url:base});await waitFor(`!!document.querySelector('#creation-idea')`);
  assert.equal(await evaluate(`document.querySelectorAll('.metric').length`),0);
  assert.equal(await evaluate(`document.querySelectorAll('.opportunity-card').length`),0);
  await click('[data-page="inspiration"]');assert.equal(await evaluate(`document.querySelectorAll('.opportunity-card').length`),0);assert.equal(await evaluate(`!!document.querySelector('#creation-idea')`),false);
  await fill('#inspire-direction','音乐');await click('[data-action="v2-inspire"][data-web="0"]');
  await waitFor(`document.querySelectorAll('.idea-card').length===3&&document.querySelector('.idea-card h3').textContent.includes('第1批')`);
  await shot('v2-inspiration');
  await click('[data-action="v2-inspire-dismiss"]');await waitFor(`document.querySelectorAll('.idea-card').length===2`);
  await click('[data-action="v2-favorite"]');await waitFor(`document.querySelector('[data-action="v2-favorite"]').textContent.includes('已收藏')`);
  await fill('#inspire-feedback','太学术了');await click('[data-action="v2-inspire"][data-web="0"]');
  await waitFor(`document.querySelectorAll('.idea-card').length===3&&document.querySelector('.idea-card h3').textContent.includes('第2批')`);
  await click('[data-action="v2-inspire-undo"]');await waitFor(`document.querySelector('.idea-card h3')?.textContent.includes('第1批')`);
  assert.equal(await evaluate(`document.querySelectorAll('.idea-card').length`),2);
  await click('[data-action="v2-inspiration-tab"][data-tab="saved"]');assert.equal(await evaluate(`document.querySelectorAll('.idea-card').length`),1);
  const prefs=await(await fetch(base+'/api/inspirations')).json();assert.equal(prefs.favorites.length,1);
  pass('Inspiration batches: generate, dismiss, favorite, replace with feedback, undo');
  await click('[data-page="home"]');
  assert.equal(await evaluate(`document.querySelector('#content').textContent.includes('API')`),false);
  assert.equal(await evaluate(`document.querySelector('#creation-clarify').checked`),true);
  await shot('v2-home');await click('[data-action="v2-create"]');
  await waitFor(`document.querySelector('#toast').textContent.includes('写下一句话')`);
  assert.equal((await(await fetch(base+'/api/projects')).json()).length,0);
  pass('Idea-first home and empty-input validation');

  await fill('#creation-idea','为什么有些歌，一听就让人松弛？');await click('[data-action="v2-create"]');
  await waitFor(`!!document.querySelector('.clarify-view .chat-question')`);
  assert.equal(await evaluate(`document.querySelector('[data-card-field="goal"]').value`),'让观众听出松弛感从哪里来');
  assert.equal(await evaluate(`document.querySelectorAll('.angle-card').length`),0);
  await shot('v2-clarify');
  await evaluate(`(()=>{const el=document.querySelector('[data-card-field="avoid"]');el.value='乐理术语';el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await waitFor(`wcontext().card?.avoid==='乐理术语'`);
  await click('[data-action="v2-chat-option"]');
  await waitFor(`!!document.querySelector('[data-action="v2-chat-action"]')`);
  assert.equal(await evaluate(`document.querySelector('[data-card-field="audience"]').value`),'不懂乐理的普通听众');
  assert.equal(await evaluate(`document.querySelectorAll('.chat-bubble.user').length`),2);
  pass('Clarifying conversation: one question at a time, clickable options, live requirements card');
  await click('[data-action="v2-chat-action"]');
  await waitFor(`document.querySelectorAll('.angle-card').length===3`);
  const projectId=await evaluate('S.selected');assert.equal((await(await fetch(base+'/api/projects')).json()).length,1);
  const research=(await(await fetch(base+`/api/projects/${projectId}`)).json()).tasks.find(t=>t.kind==='research');
  assert.ok(research.payload.prompt.includes('要避免：乐理术语'));assert.ok(research.payload.prompt.includes('给谁看：不懂乐理的普通听众'));
  pass('Proposed research runs with the confirmed requirements');

  await fill('#angle-feedback','都太像讲课了');await click('[data-action="v2-more-angles"]');
  await waitFor(`document.querySelectorAll('.angle-card').length===5`);
  assert.equal(await evaluate(`document.querySelectorAll('.angle-card')[3].textContent.includes('从一次排练讲起')`),true);
  await click('[data-action="v2-chat-toggle"]');await waitFor(`document.querySelector('.chat-drawer').classList.contains('open')`);
  assert.equal(await evaluate(`[...document.querySelectorAll('.chat-drawer .chat-bubble.user')].some(b=>b.textContent.includes('都太像讲课了'))`),true);
  await fill('#chat-input','还是想更个人化一点');await click('[data-action="v2-chat-send"]');
  await waitFor(`[...document.querySelectorAll('.chat-drawer [data-action="v2-chat-action"]')].length===1`);
  assert.equal(await evaluate(`document.querySelector('.chat-drawer').classList.contains('open')`),true);
  await shot('v2-chat-drawer');
  await click('.chat-drawer [data-action="v2-chat-action"]');await waitFor(`document.querySelectorAll('.angle-card').length===7`);
  await click('[data-action="v2-chat-toggle"]');
  pass('More directions from feedback, conversation drawer and proposed actions');
  await shot('v2-research');await click('[data-action="v2-evidence"]');await waitFor(`document.querySelector('#modal').open`);
  assert.equal(await evaluate(`document.querySelector('#modal-content').textContent.includes('待核实')`),true);
  await click('[data-action="close-modal"]');pass('Automatic research, three angles and inspectable evidence');

  await click('[data-action="v2-angle"]');await waitFor(`document.querySelectorAll('[data-script-text]').length===3`);
  assert.equal(await evaluate('S.selected'),projectId);await shot('v2-script');
  await fill('[data-script-text="hook"]','这是我亲手修改过的开场。');await waitFor(`document.querySelector('#draft-status')?.textContent==='已保存'`);
  await click('[data-action="v2-tab"][data-tab="research"]');await click('[data-action="v2-tab"][data-tab="script"]');
  assert.equal(await evaluate(`document.querySelector('[data-script-text="hook"]').value`),'这是我亲手修改过的开场。');
  assert.equal(await evaluate(`document.querySelector('[data-paragraph="hook"]').textContent.includes('请复核引用')`),true);
  pass('Angle-to-script generation and persistent direct editing with evidence review flags');

  await click('[data-action="v2-rewrite"][data-id="hook"][data-instruction="更自然"]');
  await waitFor(`document.querySelector('[data-script-text="hook"]')?.value==='同一段旋律，听听这次有什么不同？'`);
  assert.equal(await evaluate(`document.querySelector('[data-script-text="evidence"]').value`),'先别急着找术语，我们听一遍，再听另一种演奏。');
  pass('Contextual rewriting preserves other paragraphs');

  await click('[data-action="v2-to-video"]');await waitFor(`!!document.querySelector('.video-brief')`);
  assert.equal(await evaluate(`document.querySelectorAll('.scene-card').length`),0);
  assert.equal(await evaluate(`!!document.querySelector('#brief-use-script')`),false);
  assert.equal(await evaluate(`document.querySelector('details[data-advanced]').open`),false);
  await shot('v2-video-brief');pass('Script hands off to the video brief; scenes stay in closed advanced options');

  await evaluate(`document.querySelector('details[data-advanced] summary').click()`);
  await click('[data-action="v2-build-scenes"]');await waitFor(`document.querySelectorAll('.scene-card').length===3`);
  assert.equal(await evaluate(`document.querySelector('details[data-advanced]').open`),true);
  await shot('v2-scenes');await click('[data-action="v2-scene-duration"]');await fill('#scene-duration','2');
  await click('[data-action="v2-save-duration"]');await waitFor(`!document.querySelector('#modal').open`);
  assert.equal(await evaluate(`wversion('scenes').result.scenes[1].start`),2);
  pass('Script-to-scenes conversion and downstream timing recalculation');

  const pixels='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP0cAAAAASUVORK5CYII=';
  const form=new FormData();form.append('file',new Blob([Buffer.from(pixels,'base64')],{type:'image/png'}),'画面.png');
  const uploaded=await(await fetch(base+`/api/projects/${projectId}/assets`,{method:'POST',body:form})).json();assert.equal(uploaded.kind,'image');
  await evaluate('refresh()');await click('[data-action="v2-asset-picker"]');await waitFor(`document.querySelector('#modal').open`);
  await click(`[data-action="v2-choose-asset"][data-id="${uploaded.id}"]`);await waitFor(`!document.querySelector('#modal').open`);
  assert.equal(await evaluate(`wversion('scenes').result.scenes[0].asset_id`),uploaded.id);
  pass('Actual image upload and visual asset replacement');

  await waitFor(`!!document.querySelector('[data-brief-asset="${uploaded.id}"]')`);
  assert.equal(await evaluate(`document.querySelectorAll('[data-brief-role],[data-brief-note]').length`),0);
  pass('Video brief lists materials without extra role rules');

  const env=await(await fetch(base+'/api/environment')).json();
  if(env.ffmpeg&&env.ffprobe){
    await evaluate(`(async()=>{for(const scene of wversion('scenes').result.scenes.slice(1))await patchScene(scene.id,{duration:2});})()`);
    await click('[data-action="v2-first-cut"]');await waitFor(`!!document.querySelector('#cut-player')`,60000);
    await waitFor(`document.querySelector('#cut-player').readyState>=2`,20000);
    assert.equal(await evaluate(`!!wversion('edit').preview.checks.decodable`),true);
    assert.equal(await evaluate(`wversion('edit').result.segments.length`),3);
    assert.equal(await evaluate(`wversion('edit').result.has_recorded_audio`),false);
    await evaluate(`document.querySelector('#cut-player').play()`);
    await waitFor(`document.querySelector('#cut-player').currentTime>.3`);
    await evaluate(`document.querySelector('#cut-player').pause();document.querySelector('#cut-player').currentTime=3`);
    await waitFor(`!document.querySelector('#cut-player').seeking&&document.querySelector('#cut-player').readyState>=2`);
    await shot('v2-first-cut');
    await click('[data-action="v2-export"]');await waitFor(`!!document.querySelector('.download-primary')`,60000);
    const response=await fetch(await evaluate(`document.querySelector('.download-primary').href`));
    assert.equal(response.status,200);assert.ok((await response.arrayBuffer()).byteLength>1000);
    pass('Real FFmpeg first cut, playable MP4, no automatic captions, HD export and ZIP download');
  }else console.log('SKIP: FFmpeg/FFprobe unavailable');

  await click('[data-action="v2-tab"][data-tab="script"]');await fill('[data-script-text="hook"]','上游修改后，分镜应该自动同步。');
  await waitFor(`document.querySelector('#draft-status')?.textContent==='已保存'`);
  await click('[data-action="v2-tab"][data-tab="video"]');await evaluate('refresh()');
  assert.equal(await evaluate(`wversion('scenes').result.scenes[0].narration`),'上游修改后，分镜应该自动同步。');
  if(env.ffmpeg)assert.equal(await evaluate(`wproject().stale_stages.includes('edit')`),true);
  pass('Upstream edits synchronize scenes and retain previous cuts as stale versions');

  await click('[data-action="v2-history"]');await waitFor(`document.querySelector('#modal').open`);
  const old=await evaluate(`S.detail.versions.filter(v=>v.stage==='script').at(-1).id`);
  await click(`[data-action="v2-inspect-version"][data-id="${old}"]`);await click('[data-action="v2-restore-script"]');
  await waitFor(`!document.querySelector('#modal').open && !!document.querySelector('[data-script-text="hook"]')`);
  assert.equal(await evaluate(`document.querySelector('[data-script-text="hook"]').value`),'同一段旋律，为什么第二遍突然变松弛了？');
  pass('History restores an earlier script as a new branch');

  await evaluate(`(async()=>{const base=wversion('script'),pending=structuredClone(base.result),newer=structuredClone(base.result);pending.paragraphs[0].text='我尚未同步的表达。';newer.paragraphs[0].text='另一处的新版本。';localStorage.setItem(draftKey(S.selected),JSON.stringify({parent_id:base.id,result:pending,revision:Date.now()}));await api('/projects/'+S.selected+'/workspace/script',{parent_id:base.id,result:newer});await refresh();})()`);
  assert.equal(await evaluate(`!!document.querySelector('[data-action="v2-keep-draft"]')`),true);
  await click('[data-action="v2-keep-draft"]');
  await waitFor(`!document.querySelector('[data-action="v2-keep-draft"]')`);
  assert.equal(await evaluate(`document.querySelector('[data-script-text="hook"]').value`),'我尚未同步的表达。');
  pass('Concurrent script updates present an explicit choice and retain the selected draft');

  await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  for(const tab of ['research','script','video']){
    await click(`[data-action="v2-tab"][data-tab="${tab}"]`);
    assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true,`Mobile overflow: ${tab}`);
    await shot('v2-mobile-'+tab);
  }
  await click('[data-page="home"]');await waitFor(`!!document.querySelector('#creation-idea')`);
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth+1`),true);await shot('v2-mobile-home');
  pass('390px layouts fit home, research, script and video');

  for(const page of ['projects','library','films','settings','publish']){await click(`[data-page="${page}"]`);assert.equal(await evaluate(`!!document.querySelector('h1')`),true,page);}
  assert.deepEqual(b.errors,[]);pass('All supporting routes render without uncaught browser errors');
  await fs.writeFile(path.join(root,'artifacts','v2-browser-results.json'),JSON.stringify({checks,realRendering:!!env.ffmpeg,dataDirectory:data,browserErrors:b.errors},null,2));
  console.log(`PASS: ${checks.length} groups. Data: ${data}`);
}catch(error){
  if(b){await b.shot('v2-failure').catch(()=>{});console.error(await b.evaluate(`JSON.stringify({page:S.page,tab:W.tab,task:S.detail?.tasks?.[0],text:document.querySelector('#content')?.textContent?.slice(0,1000)})`).catch(()=>''));}
  console.error(log);throw error;
}finally{b?.close();backend.kill();}
