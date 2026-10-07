'use strict';

// Home: start a new project, or pick up one you are working on.
function renderHome() {
  const durations=[...new Set([Number(S.defaults.duration)||60,30,60,90,180])].sort((a,b)=>a-b), aspect=S.defaults.aspect||'9:16';
  const create=`<section class="card new-project-card" aria-labelledby="new-project-title"><h2 id="new-project-title">＋ 新建项目</h2><p class="section-note">起个名字，写几句想法。建好后进入项目：我的idea、调研、文案、视频，不分先后，用到哪个打开哪个。</p>
    <div class="field"><label for="new-project-name">项目名称</label><input id="new-project-name" maxlength="80" placeholder="例如：老歌为什么在短视频翻红" value="${esc(sessionStorage.getItem('studio-new-name')||'')}"></div>
    <div class="field"><label for="new-project-idea">想做什么 <span class="muted">可选</span></label><textarea id="new-project-idea" rows="3" maxlength="12000" placeholder="讲什么、给谁看、想要什么感觉。会放进「我的idea」的灵感碎片里。">${esc(sessionStorage.getItem('studio-new-idea')||'')}</textarea></div>
    <div class="publishing-grid"><div class="field"><label for="new-project-duration">时长</label><select id="new-project-duration">${durations.map(n=>`<option value="${n}" ${n===(Number(S.defaults.duration)||60)?'selected':''}>约 ${n<120?n+' 秒':n/60+' 分钟'}</option>`).join('')}</select></div><div class="field"><label for="new-project-aspect">画幅</label><select id="new-project-aspect">${[['9:16','竖屏 9:16'],['16:9','横屏 16:9'],['1:1','方形 1:1']].map(([v,l])=>`<option value="${v}" ${v===aspect?'selected':''}>${l}</option>`).join('')}</select></div></div>
    ${vbutton('new-project','创建并进入项目 →','primary new-project-submit')}
    <div class="new-project-alt"><span>或者</span>${vbutton('quick','＋ 从已有素材开始','text-button small','data-intent="assets"')}${vbutton('browse-inspiration','⌕ 没有想法？浏览灵感','text-button small')}</div><input id="home-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></section>`;
  const resume=`<section class="card continue-card" aria-labelledby="continue-title"><div class="section-heading"><h2 id="continue-title">继续项目 <span class="muted">${S.projects.length}</span></h2></div>${S.projects.length>5?`<input id="project-search" type="search" aria-label="搜索项目" placeholder="搜索项目名称" value="${esc(W.projectQuery||'')}">`:''}<div class="work-list" id="project-list">${projectListHTML()}</div></section>`;
  return `<div class="projects-home">${heading('项目','新建一个项目，或者回到正在做的项目。',false,'YOUR PROJECTS')}<div class="projects-grid-home">${create}${resume}</div></div>`;
}
function projectListHTML() {
  const q=(W.projectQuery||'').trim().toLowerCase(), list=S.projects.filter(p=>!q||p.name.toLowerCase().includes(q));
  if(list.length)return list.map(projectRow).join('');
  return q?`<p class="small-note">没有名字里包含「${esc(W.projectQuery)}」的项目。</p>`:empty('∿','还没有项目','在左边新建第一个项目，之后会出现在这里。');
}

function inspireStatus() {
  const job=S.inspirations?.job;
  if(!job)return '';
  if(['queued','running','cancelling'].includes(job.status))return `<div class="creative-status working"><span class="status-orbit">∿</span><div><strong>${job.status==='cancelling'?'正在停止…':job.payload?.web?'正在联网寻找近期话题':'正在构思新一批灵感'}</strong><p>已用时 <span data-since="${esc(job.started_at||job.created_at)}">${elapsedText(job.started_at||job.created_at)}</span> · ${job.payload?.web?'通常需要 1–3 分钟':'通常需要 10–40 秒'}</p><ol class="task-steps"><li class="current">${esc(job.phase||'准备中')}</li></ol></div>${job.status==='cancelling'?'':vbutton('inspire-cancel','停止','text-button small')}</div>`;
  if(job.status==='failed')return `<div class="creative-status interrupted" role="alert"><span>!</span><div><strong>这一批没有生成成功</strong><p>${esc(job.error||'请稍后再试。')}</p></div></div>`;
  if(job.status==='interrupted')return `<div class="creative-status interrupted"><span>↳</span><div><strong>上次生成被中断了</strong><p>工作台在生成过程中关闭了，再点一次「换一批」即可。</p></div></div>`;
  return '';
}
function inspirationCard(item,saved) {
  const sources=(item.sources||[]).slice(0,3);
  return `<article class="opportunity-card idea-card"><div class="opportunity-body"><span class="quiet-label">${item.kind==='research'?'已保存研究':item.web?'联网 · 结合近期话题':'灵感'}${item.format?' · '+esc(item.format):''}${item.saved&&!saved?' · 已收藏':''}</span><h3>${esc(item.title)}</h3>${item.description?`<p>${esc(item.description)}</p>`:''}${item.hook?`<blockquote>“${esc(item.hook)}”</blockquote>`:''}${(item.tags||[]).length?`<div class="inspiration-tags">${item.tags.map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>`:''}${sources.length?`<p class="idea-sources">来源：${sources.map(s=>link(s.url,s.title||s.url)+(s.verification==='unverified'?' <span class="chip warn">未核实</span>':'')).join(' · ')}</p>`:''}<div class="button-row">${vbutton('favorite',item.saved?'★ 已收藏':'☆ 收藏','small ghost',`data-id="${esc(item.id)}" data-favorite="${esc(item.favorite_id||'')}"`)}${saved?'':vbutton('inspire-dismiss','不感兴趣','text-button small',`data-id="${esc(item.id)}"`)}${vbutton('inspiration-create','用这个开始 →','small primary',`data-id="${esc(item.id)}"`)}</div></div></article>`;
}
function renderInspiration() {
  const data=S.inspirations||{}, tab=W.inspirationTab||'ideas', q=(W.inspirationQuery||'').toLowerCase(), saved=tab==='saved';
  const running=['queued','running','cancelling'].includes(data.job?.status);
  if(running)queueMicrotask(watchInspiration);
  const list=((saved?data.favorites:data.ideas)||[]).filter(i=>(i.title+' '+(i.description||'')+' '+(i.tags||[]).join(' ')).toLowerCase().includes(q));
  const disabled=running||data.configured===false?'disabled':'';
  return `<div class="collection-shell inspiration-shell">${heading('灵感发现','不喜欢就换一批；收藏的会一直留着。',false,'IDEAS ON DEMAND')}
    <section class="card inspire-controls"><div class="inspire-row"><label for="inspire-direction" class="sr-only">想探索的方向</label><input id="inspire-direction" maxlength="500" placeholder="想探索的方向（可选）：例如 音乐、职场、城市生活" value="${esc(W.inspireDirection||'')}"><div class="button-row">${vbutton('inspire','↻ 换一批','primary small',`data-web="0" ${disabled}`)}${vbutton('inspire','⌕ 联网找近期热点','small',`data-web="1" ${disabled}`)}</div></div>
    <label for="inspire-feedback" class="sr-only">这一批哪里不喜欢</label><input id="inspire-feedback" maxlength="500" placeholder="这一批哪里不喜欢？（可选，之后的灵感会避开）" value="${esc(W.inspireFeedback||'')}">
    <p class="small-note">换一批：不联网，几十秒内凭经验发散 · 联网找近期热点：结合最近的真实讨论，约 1–3 分钟。没收藏的会被换掉。${data.previous&&!running?' '+vbutton('inspire-undo','↶ 换回上一批','text-button small'):''}</p>${data.configured===false?'<p class="small-note">先在设置中连接 Claude 订阅或 API 服务，才能生成灵感。</p>':''}</section>
    <div id="inspire-status">${inspireStatus()}</div>
    <div class="library-toolbar"><div class="view-switch">${vbutton('inspiration-tab',`当前灵感 ${(data.ideas||[]).length}`,!saved?'selected':'','data-tab="ideas"')}${vbutton('inspiration-tab',`收藏 ${(data.favorites||[]).length}`,saved?'selected':'','data-tab="saved"')}</div><input id="inspiration-search" type="search" aria-label="搜索灵感" placeholder="搜索标题或标签" value="${esc(W.inspirationQuery||'')}"></div>
    <div class="opportunity-grid">${list.map(item=>inspirationCard(item,saved)).join('')}</div>${!list.length?(q?empty('⌕','没有匹配的灵感','试试其他关键词。'):saved?empty('☆','还没有收藏','在当前灵感里点「收藏」，喜欢的题目会一直留在这里。'):empty('∿','还没有灵感','写一个想探索的方向（也可以不写），点「换一批」。')):''}</div>`;
}
async function watchInspiration() {
  if(W.inspireTimer)return;
  const tick=async()=>{
    W.inspireTimer=null;
    try{
      S.inspirations=await api('/inspirations');
      const running=['queued','running','cancelling'].includes(S.inspirations.job?.status);
      if(S.page==='inspiration'){
        const typing=document.activeElement?.matches('#inspiration-search,#inspire-direction,#inspire-feedback');
        if(typing&&running){const slot=$('#inspire-status');if(slot)slot.innerHTML=inspireStatus();}
        else{const id=typing?document.activeElement.id:null,caret=typing?document.activeElement.selectionStart:null;render();if(id){const input=$('#'+id);input?.focus();input?.setSelectionRange(caret,caret);}}
      }
      if(running)W.inspireTimer=setTimeout(tick,2000);
    }catch{W.inspireTimer=setTimeout(tick,4000);}
  };
  W.inspireTimer=setTimeout(tick,1500);
}

function workspaceModel(stage) {
  const chosen=currentModel(stage);
  return S.models.some(m=>m.id===chosen)?chosen:S.defaults['model_'+stage];
}
