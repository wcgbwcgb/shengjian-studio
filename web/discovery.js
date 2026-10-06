'use strict';

function inspireStatus() {
  const job=S.inspirations?.job;
  if(!job)return '';
  if(['queued','running','cancelling'].includes(job.status))return `<div class="creative-status working"><span class="status-orbit">∿</span><div><strong>${job.status==='cancelling'?'正在停止…':job.payload?.web?'正在联网寻找近期话题':'正在构思新一批灵感'}</strong><p>已用时 <span data-since="${esc(job.started_at||job.created_at)}">${elapsedText(job.started_at||job.created_at)}</span> · ${job.payload?.web?'通常需要 1–3 分钟':'通常需要几十秒'} · ${esc(job.phase||'准备中')}</p></div>${job.status==='cancelling'?'':button('inspire-cancel','停止','text-button small')}</div>`;
  if(job.status==='failed')return `<div class="creative-status interrupted" role="alert"><span>!</span><div><strong>这一批没有生成成功</strong><p>${esc(job.error||'请稍后再试。')}</p></div></div>`;
  if(job.status==='interrupted')return `<div class="creative-status interrupted"><span>↳</span><div><strong>上次生成被中断了</strong><p>工作台在生成过程中关闭了，再点一次「换一批」即可。</p></div></div>`;
  return '';
}
function inspirationCard(item,saved) {
  const sources=(item.sources||[]).slice(0,3);
  return `<article class="opportunity-card idea-card"><div class="opportunity-body"><span class="quiet-label">${item.web?'联网 · 结合近期话题':'灵感'}${item.format?' · '+esc(item.format):''}${item.saved&&!saved?' · 已收藏':''}</span><h3>${esc(item.title||item.topic?.title)}</h3>${item.description?`<p>${esc(item.description)}</p>`:''}${item.hook?`<blockquote>“${esc(item.hook)}”</blockquote>`:''}${(item.tags||[]).length?`<div class="inspiration-tags">${item.tags.map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>`:''}${sources.length?`<p class="idea-sources">来源：${sources.map(s=>link(s.url,s.title||s.url)+(s.verification==='unverified'?' <span class="chip warn" title="这次运行中没有读取过这个链接">未核实</span>':'')).join(' · ')}</p>`:''}<div class="button-row">${button('favorite',item.saved?'★ 已收藏':'☆ 收藏','small ghost',`data-id="${esc(item.id)}" data-favorite="${esc(item.favorite_id||'')}"`)}${saved?'':button('inspire-dismiss','不感兴趣','text-button small',`data-id="${esc(item.id)}"`)}${button('inspiration-create','用这个开始 →','small primary',`data-id="${esc(item.id)}"`)}</div></div></article>`;
}
function renderInspiration() {
  const data=S.inspirations||{}, tab=W.inspirationTab||'ideas', q=(W.inspirationQuery||'').toLowerCase(), saved=tab==='saved';
  const running=['queued','running','cancelling'].includes(data.job?.status);
  if(running)queueMicrotask(watchInspiration);
  const list=((saved?data.favorites:data.ideas)||[]).filter(i=>((i.title||'')+' '+(i.description||'')+' '+(i.tags||[]).join(' ')).toLowerCase().includes(q));
  const disabled=running||data.configured===false?'disabled':'';
  const sent=data.job?.payload?.prompt;
  return `<div class="collection-shell inspiration-shell">${heading('灵感发现','不喜欢就换一批；收藏的会一直留着。','IDEAS ON DEMAND')}
    <section class="card inspire-controls"><div class="inspire-row"><label for="inspire-direction" class="sr-only">想探索的方向</label><input id="inspire-direction" maxlength="500" placeholder="想探索的方向（可选）：例如 音乐、职场、城市生活" value="${esc(W.inspireDirection||'')}"><div class="button-row">${button('inspire','↻ 换一批','primary small',`data-web="0" ${disabled}`)}${button('inspire','⌕ 联网找近期热点','small',`data-web="1" ${disabled}`)}</div></div>
    <label for="inspire-feedback" class="sr-only">这一批哪里不喜欢</label><input id="inspire-feedback" maxlength="500" placeholder="这一批哪里不喜欢？（可选，之后的灵感会避开）" value="${esc(W.inspireFeedback||'')}">
    <p class="small-note">两个按钮各对应一段提示词（<a href="#prompts">可以修改</a>），你写的方向和意见会接在后面。没收藏的灵感会被换掉。${data.previous&&!running?' '+button('inspire-undo','↶ 换回上一批','text-button small'):''}</p>${sent?`<details class="quiet-details"><summary>上一次发给 Claude 的内容</summary><pre class="file-text">${esc(sent)}</pre></details>`:''}${data.configured===false?'<p class="small-note">先在设置中连接本机 Claude Code，才能生成灵感。</p>':''}</section>
    <div id="inspire-status">${inspireStatus()}</div>
    <div class="library-toolbar"><div class="view-switch">${button('inspiration-tab',`当前灵感 ${(data.ideas||[]).length}`,!saved?'selected':'','data-tab="ideas"')}${button('inspiration-tab',`收藏 ${(data.favorites||[]).length}`,saved?'selected':'','data-tab="saved"')}</div><input id="inspiration-search" type="search" aria-label="搜索灵感" placeholder="搜索标题或标签" value="${esc(W.inspirationQuery||'')}"></div>
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

Object.assign(HANDLERS, {
  'favorite':async el=>{if(el.dataset.favorite)await api(`/inspirations/favorites/${el.dataset.favorite}`,{},'DELETE');else await api('/inspirations/favorites',{id:el.dataset.id});S.inspirations=await api('/inspirations');render();},
  'inspiration-create':async el=>{const result=await api('/inspirations/create',{id:el.dataset.id});await openProject(result.project.id);toast('已建好作品。选一个提示词按钮，或直接写下要 Claude 做的事。');},
  'inspire':async el=>{
    await api('/inspirations/generate',{web:el.dataset.web==='1',direction:$('#inspire-direction')?.value||'',feedback:$('#inspire-feedback')?.value||''});
    W.inspireFeedback='';S.inspirations=await api('/inspirations');W.inspirationTab='ideas';render();watchInspiration();
  },
  'inspire-cancel':async()=>{await api('/inspirations/cancel',{});S.inspirations=await api('/inspirations');render();},
  'inspire-undo':async()=>{S.inspirations=await api('/inspirations/undo',{});render();toast('已换回上一批灵感。');},
  'inspire-dismiss':async el=>{await api(`/inspirations/${el.dataset.id}/dismiss`,{});S.inspirations=await api('/inspirations');render();toast('已移除，之后的灵感会避开类似题目。');},
  'inspiration-tab':el=>{W.inspirationTab=el.dataset.tab;render();},
});
document.addEventListener('input',event=>{
  const el=event.target;
  if(el.id==='inspiration-search'){W.inspirationQuery=el.value;const position=el.selectionStart;render();const input=$('#inspiration-search');input.focus();input.setSelectionRange(position,position);}
  if(el.id==='inspire-direction')W.inspireDirection=el.value;
  if(el.id==='inspire-feedback')W.inspireFeedback=el.value;
});
