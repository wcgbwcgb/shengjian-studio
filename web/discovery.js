'use strict';

function renderHome() {
  return `<div class="home-shell"><section class="creation-hero"><div class="hero-eyebrow">YOUR NEXT STORY STARTS HERE</div><h1>今天想做什么视频？</h1><p class="hero-description">用一句话描述你想做的视频，比如主题、给谁看、想要的感觉。</p><div class="idea-composer"><label class="sr-only" for="creation-idea">视频要求</label><textarea id="creation-idea" maxlength="12000" placeholder="例如：用 60 秒讲清楚为什么有些歌一听就让人放松，面向不懂乐理的观众">${esc(sessionStorage.getItem('studio-idea')||'')}</textarea><div class="composer-bottom"><div class="composer-options"><select id="creation-duration" aria-label="目标时长">${[...new Set([Number(S.defaults.duration)||60,30,60,90])].map(n=>`<option value="${n}" ${n===Number(S.defaults.duration)?'selected':''}>约 ${n} 秒</option>`).join('')}</select><select id="creation-aspect" aria-label="画幅">${['9:16','16:9','1:1'].map(a=>`<option ${a===S.defaults.aspect?'selected':''}>${a}</option>`).join('')}</select></div><label class="clarify-toggle" title="先由 Claude 问几个问题，把受众、目的和语气聊清楚"><input type="checkbox" id="creation-clarify" ${clarifyPreference()?'checked':''}> 先帮我理清需求</label><div class="button-row">${vbutton('create','先研究与写稿','ghost small','title="先聊清楚需求，再查资料、选方向、写脚本"')}${vbutton('direct-video','直接制作视频 ↗','primary create-button','title="按你的描述和素材直接做出视频（Ctrl+Enter）"')}</div></div></div><p class="mode-hint"><b>先研究与写稿</b>：先聊清楚需求，再查资料、给出方向和完整脚本 <span>·</span> <b>直接制作视频</b>：已经想清楚了，按描述直接出片</p><div class="quick-starts">${vbutton('quick','＋ 从已有素材开始','quick-action','data-intent="assets"')}${vbutton('browse-inspiration','⌕ 没有想法？浏览灵感','quick-action')}</div><input id="home-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></section><section class="continue-section"><div class="section-heading"><h2>继续最近项目</h2>${vbutton('works','查看全部 →','text-button')}</div>${S.projects.length?S.projects.slice(0,5).map(projectRow).join('')+(S.projects.length>5?`<p class="small-note">还有 ${S.projects.length-5} 个作品，${vbutton('works','查看全部','text-button small')}</p>`:''):empty('∿','开始第一条作品','项目会保存想法、研究、脚本、素材和视频。')}</section></div>`;
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
function workspaceTools(stage) {
  const templates=S.templates.filter(t=>t.stage===stage);
  return `<details class="workspace-tools"><summary>这次${stage==='script'?'写稿':'研究'}的模型与模板</summary><div class="settings-grid"><div class="field"><label>模型</label><select data-workspace-model="${stage}">${modelOptions(workspaceModel(stage))}</select></div><div class="field"><label>阶段模板</label><select id="workspace-template"><option value="">选择模板</option>${templates.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></div></div><textarea id="workspace-stage-prompt" data-stage="${stage}" rows="2" placeholder="这次任务的补充要求">${esc(S.prompts[`${S.selected}:${stage}`]||'')}</textarea><div class="button-row">${vbutton('load-workspace-template','载入模板','small',`data-stage="${stage}"`)}${vbutton('save-workspace-template','保存为模板','small ghost',`data-stage="${stage}"`)}${stage==='script'?vbutton('regenerate-script','按要求生成新版脚本','small',activeTask()?'disabled':''):''}</div></details>`;
}
