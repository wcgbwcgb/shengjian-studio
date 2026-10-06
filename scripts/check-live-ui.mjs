// Read-only smoke check against the actual local application and existing data.
import assert from 'node:assert/strict';
import path from 'node:path';
import {browser} from './browser.mjs';
const root=path.resolve(import.meta.dirname,'..'),base=process.env.STUDIO_URL||'http://127.0.0.1:8765';
const projects=await(await fetch(base+'/api/projects')).json();
const b=await browser(root,9234);
try{
  const {cdp,evaluate,waitFor,shot}=b;
  await cdp('Page.navigate',{url:base});await waitFor(`!!document.querySelector('#creation-idea')`);
  await shot('v2-live-home');
  assert.equal(await evaluate(`document.querySelectorAll('.metric').length`),0);
  await evaluate(`document.querySelector('[data-page="projects"]').click()`);
  assert.equal(await evaluate(`document.querySelectorAll('.work-row').length`),projects.length);
  if(projects.length){
    await evaluate(`document.querySelector('[data-action="v2-open"]').click()`);
    await waitFor(`!!document.querySelector('.studio-nav')`);
    assert.equal(await evaluate('S.selected'),projects[0].id);
    await shot('v2-live-existing-workspace');
  }
  await evaluate(`document.querySelector('[data-page="settings"]').click()`);
  assert.equal(await evaluate(`!!document.querySelector('.key-manager')`),true);
  assert.equal(await evaluate(`document.querySelectorAll('#connection-key').length`),0);
  assert.deepEqual(b.errors,[]);
  console.log(JSON.stringify({liveService:base,existingProjects:projects.length,errors:b.errors,status:'PASS'}));
}finally{b.close();}
