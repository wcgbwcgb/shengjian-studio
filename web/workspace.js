'use strict';

// The workspace owns product-level decisions. Existing app.js retains API, settings,
// publishing, versions and the advanced editor during the incremental migration.
const W = {tab:'research', topic:null, busy:false, saving:null, saveTimer:null, uploadScene:null, videoView:'scenes', intent:'idea', needsRender:false};
const workspaceTabs = {research:'研究与角度',script:'脚本',video:'视频'};
const beatNames = {hook:'开场钩子',context:'引入',evidence:'关键论据',turn:'转折',cta:'收尾 / CTA'};
const timecode = seconds => `${Math.floor((seconds||0)/60)}:${String(Math.floor((seconds||0)%60)).padStart(2,'0')}`;
const assetURL = a => a.id ? `/api/assets/${a.id}/file` : `/api/projects/${encodeURIComponent(a.project_id)}/files/${a.path.split('/').map(encodeURIComponent).join('/')}`;
const wproject = () => S.detail?.project;
const wcontext = () => wproject()?.workspace || {};
const activeTask = () => S.detail?.tasks.find(t=>['queued','running','cancelling'].includes(t.status));
const wversion = stage => {
  const key={script:'script_version',scenes:'scene_version',edit:'edit_version',research:'research_version'}[stage];
  const versions=S.detail?.versions || [];
  if(stage==='research')return versions.find(v=>v.stage==='research');
  return (stage==='edit'?versions.find(v=>v.id===wproject()?.adopted?.edit):null) || versions.find(v=>v.id===wcontext()[key]) || versions.find(v=>v.id===wproject()?.adopted?.[stage]) || versions.find(v=>v.stage===stage && (stage!=='script'||v.result.paragraphs));
};
const draftKey = id => `studio-v2-draft:${id}`;
function readDraft(id=S.selected) {try{return JSON.parse(localStorage.getItem(draftKey(id))||'null');}catch{return null;}}
function scriptResult() {return readDraft()?.result || wversion('script')?.result;}
function vbutton(action,label,cls='',attrs='') {return button(`v2-${action}`,label,cls,attrs);}
function sourceChip(url,index) {return vbutton('evidence',`↗ 来源 ${index+1}`,'citation',`data-url="${esc(url)}"`);}
function sourcesHTML(urls=[]) {return urls.map(sourceChip).join('');}
function defaultIdea() {return `帮我找今天值得做的选题，面向${S.defaults.audience||'我的观众'}，${S.defaults.style||'自然易懂'}。` ;}

function renderV2() {
  const screens={home:renderHome,inspiration:renderInspiration,projects:renderWorks,workspace:renderWorkspace,library:renderLibrary,films:renderFilms};
  document.body.classList.toggle('in-workspace',S.page==='workspace');
  if(!screens[S.page])return false;
  $('#content').innerHTML=screens[S.page]();
  $$('.chat-thread').forEach(el=>{el.scrollTop=el.scrollHeight;});
  if(S.page==='workspace')$$('[data-page="projects"]').forEach(el=>el.classList.add('active'));
  return true;
}

function projectRow(p) {
  const stage=p.workspace?.scene_version||p.workspace?.intent==='video'||p.adopted?.edit?'video':p.workspace?.script_version||p.adopted?.script?'script':'research';
  const t=p.last_task, running=t&&['queued','running','cancelling'].includes(t.status), failed=t&&['failed','interrupted'].includes(t.status);
  const state=running?'<span class="row-state working">制作中</span>':failed?`<span class="row-state failed" title="${esc(explainError(t.error,t.kind).title)}">上次未完成 · 可重试</span>`:`<span class="row-state">${esc(!t&&p.status==='构思中'?'尚未开始':p.status)}</span>`;
  return `<article class="work-row"><div class="work-symbol">${stage==='video'?'▷':stage==='script'?'≡':'∿'}</div><div class="work-info"><h3 title="${esc(p.name)}">${esc(p.name)}</h3><p>${state} <span>·</span> ${esc(p.requirements?.aspect||p.defaults?.aspect||'9:16')} <span>·</span> ${date(p.updated_at)}</p></div><div class="work-progress" aria-label="创作进度">${['research','script','video'].map(s=>`<i class="${s===stage?'current':''}"></i>`).join('')}</div><div class="row-actions">${vbutton('rename-project','重命名','text-button small',`data-id="${p.id}"`)}${vbutton('delete-project','删除','text-button small danger-text',`data-id="${p.id}"`)}</div>${vbutton('open',failed?'查看并重试 →':'继续创作 →','small ghost',`data-id="${p.id}" data-tab="${stage}"`)}</article>`;
}
function renderWorks() {return `<div class="collection-shell">${heading('我的作品','每一个想法，都有自己的创作空间。',false,'YOUR STORIES')}<div class="work-list">${S.projects.length?S.projects.map(projectRow).join(''):empty('∿','你的第一条视频，从一个念头开始','写下想法，研究、脚本和视频会接着展开。',vbutton('home','开始创作','primary'))}</div></div>`;}

function renderWorkspace() {
  if(!S.detail)return heading('创作空间','从一个想法开始。')+vbutton('home','开始创作','primary');
  const p=wproject(), script=wversion('script'), scenes=wversion('scenes'), cut=wversion('edit');
  const direct=['video','assets'].includes(p.workspace?.intent),tabs=direct?{video:'素材与视频'}:workspaceTabs;
  const done={research:!!p.adopted.research,script:!!script,video:!!cut?.preview};
  return `<div class="studio-shell"><div class="studio-heading"><div><h1 title="${esc(p.name_custom?p.name:p.selected_topic?.title||p.name)}">${esc(p.name_custom?p.name:p.selected_topic?.title||p.name)}</h1><div class="studio-meta"><span>${esc(p.requirements?.aspect||p.defaults.aspect)}</span><span>约 ${esc(p.requirements?.duration||p.defaults.duration)} 秒</span><span id="draft-status">${readDraft()?'编辑已暂存':'所有进展自动保存'}</span></div></div><div class="button-row">${vbutton('rename-project','重命名','ghost small',`data-id="${p.id}"`)}${vbutton('history','◷ 版本记录','ghost small')}</div></div>
    <div class="studio-nav" role="tablist" aria-label="创作阶段">${Object.entries(tabs).map(([tab,title],i)=>vbutton('tab',`<span class="tab-number">${done[tab]?'✓':String(i+1).padStart(2,'0')}</span>${title}`,W.tab===tab?'selected':'',`role="tab" aria-selected="${W.tab===tab}" data-tab="${tab}"`)).join('')}<span class="studio-nav-note">随时回到前面，继续打磨</span></div>
    <div class="studio-layout"><section class="studio-main" aria-label="${workspaceTabs[W.tab]}"><div id="creation-status" aria-live="polite">${workspaceStatus()}</div>${W.tab==='research'?workspaceTools('research')+workspaceResearch():W.tab==='script'?workspaceTools('script')+workspaceScript(script):workspaceVideo(scenes,cut)}</section><aside class="creative-context">${workspaceContext(p,script,scenes,cut)}</aside></div>${!direct&&['research','script'].includes(W.tab)&&!(W.tab==='research'&&!wversion('research')&&activeTask()?.kind!=='research')?chatDrawer():''}</div>`;
}

function workspaceContext(p,script,scenes,cut) {
  const introduction=`<div class="companion-avatar">∿</div><h3>我们正在做的这条视频</h3><p class="context-idea">${esc(wcontext().idea||p.name)}</p>`;
  if(p.workspace?.intent==='video'||cut?.result.engine==='claude_cli')return introduction+`<div class="context-divider"></div><span class="quiet-label">制作方式</span><p>用文字描述内容、画面与节奏。Claude 会结合项目素材完成制作，你可以继续用文字提出修改。</p><span class="quiet-label">项目素材</span><p>${S.detail.assets.filter(a=>!a.generated).length} 个素材 · 原文件保留</p><div class="context-divider"></div><span class="quiet-label">下一步</span><p>${activeTask()?'正在制作并检查成片，完成后会出现在这里。':cut?'播放这一版，再在上方输入修改要求。每次制作都会保存为新版本。':'添加素材或输入制作要求，开始第一版视频。'}</p><div class="context-trail"><span class="complete">你的要求</span><span class="${S.detail.assets.length?'complete':''}">项目素材</span><span class="${cut?.preview?'complete':''}">视频与版本</span></div>${p.stale_stages?.includes('edit')&&cut?'<p class="small-note">项目已有修改。继续制作可生成新版本，旧视频保留。</p>':''}`;
  return introduction+`${vbutton('idea','修改想法','text-button small')}<div class="context-divider"></div><span class="quiet-label">创作方向</span><p>${esc(wcontext().angle?.title||'读完研究，选一个最想讲的角度。')}</p>${wcontext().angle?.audience?`<span class="quiet-label">讲给谁听</span><p>${esc(wcontext().angle.audience)}</p>`:''}<div class="context-divider"></div><span class="quiet-label">下一步</span><p>${activeTask()?'我在整理内容，完成后会直接出现在这里。':W.tab==='research'?(wversion('research')?'选一个方向，或说说哪里不满意，我再想几个。':'回答几个问题把需求聊清楚，或直接开始研究。'):W.tab==='script'?'把表达调整到你满意，它会作为视频制作的参考。':cut?.preview?'播放检查这一版，需要修改就在下方写下要求。':'写下制作要求，Claude 会制作完整视频。'}</p><div class="context-trail"><span class="${p.adopted.research?'complete':''}">研究与证据</span><span class="${script?'complete':''}">你的表达</span><span class="${S.detail.tasks.some(t=>t.kind==='cli_video')||scenes?'complete':''}">制作要求</span><span class="${cut?.preview?'complete':''}">完整视频</span></div>${p.stale_stages?.includes('edit')&&cut?'<p class="small-note">已有修改。旧版视频保留，继续制作即可生成新版本。</p>':''}`;
}

const TASK_COPY = {
  research:{title:'正在研究你的想法',expect:'通常需要 1–3 分钟'},
  script:{title:'正在写脚本',expect:'通常需要 30 秒–2 分钟'},
  first_cut:{title:'正在生成第一版视频',expect:'通常需要几十秒'},
  cli_video:{title:'Claude 正在制作视频',expect:'通常需要 3–15 分钟'},
  angles:{title:'正在构思新的方向',expect:'通常需要 20–60 秒'},
  final:{title:'正在导出高清成片',expect:'通常需要 1 分钟左右'},
  preview:{title:'正在渲染预览',expect:'通常需要几十秒'},
};
function workspaceStatus() {
  const task=activeTask(), last=S.detail.tasks[0];
  // Conversation turns show their progress inside the conversation itself.
  if(task?.kind==='chat')return '';
  if(task){
    const copy=TASK_COPY[task.kind]||{title:'正在处理',expect:''}, steps=[...new Set((task.logs||[]).map(l=>l.text))].slice(-5);
    const cancelling=task.status==='cancelling';
    return `<div class="creative-status working"><span class="status-orbit">∿</span><div><strong>${cancelling?'正在停止…':esc(copy.title)}</strong><p>已用时 <span data-since="${esc(task.started_at||task.created_at)}">${elapsedText(task.started_at||task.created_at)}</span>${copy.expect?` · ${copy.expect}`:''} · 可以离开这个页面，完成后会自动显示。</p>${steps.length?`<ol class="task-steps">${steps.map((text,i)=>`<li class="${i===steps.length-1?'current':'done'}">${esc(text)}</li>`).join('')}</ol>`:'<ol class="task-steps"><li class="current">排队中，马上开始</li></ol>'}</div>${cancelling?'':button('cancel-task','停止','text-button small',`data-id="${task.id}" title="停止后已有内容会保留"`)}</div>`;
  }
  if(last&&last.status==='cancelled')return `<div class="creative-status interrupted"><span>↳</span><div><strong>已停止</strong><p>已有内容都还在。需要时可以重新开始。</p></div>${button('retry-task','重新开始','small',`data-id="${last.id}"`)}</div>`;
  if(last&&['failed','interrupted'].includes(last.status)){
    const e=last.status==='interrupted'?{title:'上次处理被中断了',why:'工作台在处理过程中关闭或重启了。',next:'已有内容都还在，点「继续」重新开始这一步。',action:'retry',detail:''}:explainError(last.error,last.kind);
    const actions=e.action==='settings'?vbutton('connect','打开设置','primary small')+button('retry-task','重试','small',`data-id="${last.id}"`):button('retry-task',last.status==='interrupted'?'继续':'重试','primary small',`data-id="${last.id}"`);
    return `<div class="creative-status interrupted" role="alert"><span>!</span><div><strong>${esc(e.title)}</strong><p>${e.why?esc(e.why)+' ':''}${esc(e.next)}</p>${e.detail?`<details class="error-detail"><summary>技术细节</summary><code>${esc(e.detail)}</code></details>`:''}</div><div class="button-row">${actions}</div></div>`;
  }
  if(!(S.env.creation_configured??S.env.api_configured)&&W.tab!=='video')return `<div class="creative-status"><span>∿</span><div><strong>想法已经收好</strong><p>在设置中连接 Claude 订阅或 API 服务，即可开始调研与写稿。</p></div>${vbutton('connect','连接服务 →','small')}</div>`;
  const message=S.detail.messages.find(m=>m.role==='assistant'&&!m.version_id);
  if(last?.output?.conversation&&message)return `<div class="creative-status"><span>∿</span><div><strong>需要补充一点信息</strong><p>${esc(message.text)}</p></div>${vbutton('idea','补充想法','small')}</div>`;
  return '';
}

function researchWait() {
  return `<div class="research-wait"><div class="research-orbit">⌕</div><div class="eyebrow">A GOOD STORY STARTS WITH A GOOD QUESTION</div><h2>${activeTask()?'好角度，藏在细节里。':'先找到值得讲的部分。'}</h2><p>${activeTask()?'正在查找事实、不同观点和可用证据，再为你提出三个创作角度。':'我会围绕你的想法查找资料，把事实、观点和内容机会整理在一起。'}</p><div class="research-checklist"><span>事实与证据</span><span>观众在意什么</span><span>三个创作角度</span></div>${!activeTask()&&!['failed','interrupted','cancelled'].includes(S.detail.tasks[0]?.status)?vbutton('research','开始研究 →','primary'):''}</div>`;
}

const CARD_FIELDS = {audience:'给谁看',goal:'想让观众得到什么',core_message:'核心观点',tone:'语气风格',format:'形式与时长',must_include:'必须包含',avoid:'要避免',notes:'其他'};
const ACTION_LABELS = {research:'开始研究',angles:'重新构思方向',custom_angle:'按这个方向写脚本',revise_script:'按这个要求改脚本'};
function allAngles(topic) {
  const base=(topic.angles||[]).slice(0,3).map((a,i)=>typeof a==='string'?{id:`angle-${i+1}`,title:a}:{...a,id:a.id||`angle-${i+1}`});
  return base.concat(wcontext().extra_angles?.[topic.id]||[]);
}
function angleTools(v,topic) {
  const busy=!!activeTask();
  return `<div class="angle-tools"><div class="angle-feedback"><label for="angle-feedback" class="quiet-label">都不太满意？</label><div class="angle-feedback-row"><input id="angle-feedback" maxlength="2000" placeholder="说说哪里不对，比如：都太严肃了，想要更个人化的讲法（可留空）" value="${esc(W.angleFeedback||'')}">${vbutton('more-angles','再给几个方向','small',`data-version="${v.id}" data-topic="${topic.id}" ${busy?'disabled':''}`)}</div><p class="small-note">只用已有的研究资料重新构思，不重新搜索，通常不到一分钟。</p></div><div class="button-row">${vbutton('custom-angle','✎ 自己写一个方向','small ghost',`data-version="${v.id}" data-topic="${topic.id}" ${busy?'disabled':''}`)}${vbutton('chat-toggle','∿ 和 Claude 聊聊','text-button small')}</div></div>`;
}
function chatMessages() {return (S.detail?.messages||[]).filter(m=>m.stage==='chat').slice().reverse();}
function chatThread() {
  const messages=chatMessages(), task=activeTask(), busy=!!task, last=messages.filter(m=>m.role==='assistant').at(-1);
  const bubble=m=>m.role==='user'?`<div class="chat-bubble user">${esc(m.text)}</div>`:`<div class="chat-bubble assistant"><p>${esc(m.text)}</p>${m.question?`<p class="chat-question">${esc(m.question)}</p>`:''}${m===last&&m.options?.length&&!busy?`<div class="chat-options">${m.options.map(o=>vbutton('chat-option',esc(o),'chat-chip',`data-text="${esc(o)}"`)).join('')}</div>`:''}${m.action&&m.action!=='none'?(m.action_task_id?`<span class="chat-done">✓ 已执行：${esc(m.action_label||ACTION_LABELS[m.action])}</span>`:vbutton('chat-action',esc(m.action_label||ACTION_LABELS[m.action])+' →','primary small',`data-id="${m.id}" ${busy?'disabled':''}`)):''}</div>`;
  const intro=messages.length?'':`<div class="chat-bubble assistant"><p>先聊几句，把这条视频想清楚：给谁看、想让观众得到什么、用什么语气。</p>${vbutton('chat-start','让 Claude 先问我几个问题','small',busy?'disabled':'')}</div>`;
  const thinking=task?.kind==='chat'?'<div class="chat-bubble assistant thinking"><span class="status-orbit">∿</span>正在思考…</div>':'';
  const draft=sessionStorage.getItem(`studio-chat:${S.selected}`)||'';
  return `<div class="chat-thread" role="log" aria-live="polite">${intro}${messages.map(bubble).join('')}${thinking}</div><div class="chat-input"><label for="chat-input" class="sr-only">发给 Claude 的消息</label><textarea id="chat-input" rows="2" maxlength="4000" placeholder="${busy&&task.kind!=='chat'?'正在处理当前任务，完成后可以继续对话':'回答问题，或说说你的想法…'}">${esc(draft)}</textarea>${vbutton('chat-send','发送','primary small',busy?'disabled':'')}</div><p class="small-note chat-hint">Enter 发送 · Shift+Enter 换行</p>`;
}
function requirementsCard() {
  const card=wcontext().card||{};
  return `<div class="requirements-card"><div class="requirements-head"><span class="quiet-label">需求卡</span><small>对话中确认的内容会自动填入，也可以直接修改</small></div>${Object.entries(CARD_FIELDS).map(([key,label])=>`<label class="card-field"><span>${label}</span><textarea data-card-field="${key}" rows="1" maxlength="1000" placeholder="还没确定">${esc(card[key]||'')}</textarea></label>`).join('')}</div>`;
}
function clarifyView() {
  const busy=!!activeTask();
  return `<section class="clarify-view"><div class="section-heading"><div><span class="eyebrow">LET'S GET IT CLEAR FIRST</span><h2>先把这条视频聊清楚</h2></div></div><p class="section-caption">Claude 一次问一个问题。点选项或直接打字回答；觉得够了，随时开始研究。</p><div class="clarify-layout"><div class="clarify-chat">${chatThread()}</div>${requirementsCard()}</div><div class="clarify-actions"><span class="small-note">研究会按需求卡和对话内容进行。</span>${vbutton('research','够了，开始研究 →','primary',busy?'disabled':'')}</div></section>`;
}
function chatDrawer() {
  const count=chatMessages().length;
  return `<button class="chat-fab" data-action="v2-chat-toggle" aria-expanded="${!!W.chatOpen}">∿ 和 Claude 聊聊${count?`<small>${count}</small>`:''}</button><aside class="chat-drawer ${W.chatOpen?'open':''}" aria-label="和 Claude 对话"><div class="drawer-head"><div><strong>和 Claude 聊聊</strong><small>说说哪里不满意，或换个想法</small></div>${vbutton('chat-toggle','✕','text-button','aria-label="关闭对话"')}</div><details class="drawer-card"><summary>需求卡</summary>${requirementsCard()}</details>${chatThread()}</aside>`;
}

function workspaceResearch() {
  const v=wversion('research'), topics=v?.result.topics||[];
  if(!v)return activeTask()?.kind==='research'?researchWait():clarifyView();
  const topic=topics.find(t=>t.id===W.topic)||topics.find(t=>t.id===wproject().selected_topic_id)||topics[0];
  if(!topic)return empty('⌕','这次没有找到合适的选题',v.result.summary||'试着缩小范围，或补充一个参考链接。',vbutton('idea','调整想法','primary'));
  const angles=allAngles(topic);
  const facts=topic.key_facts||[];
  return `<div class="research-overview"><div class="section-heading"><div><span class="eyebrow">RESEARCH BRIEF</span><h2>这里，有一个值得讲的故事。</h2></div>${vbutton('research','↻ 重新研究','text-button small',activeTask()?'disabled':'')}</div>${topics.length>1?`<div class="topic-switch">${topics.map(t=>vbutton('topic',esc(t.title),t.id===topic.id?'selected':'',`data-id="${t.id}"`)).join('')}</div>`:''}<h3 class="research-title">${esc(topic.title)}</h3><p class="research-summary">${esc(topic.what_happened||topic.question||v.result.summary)}</p><div class="research-insights"><div><span class="insight-label">01 / 为什么值得讲</span><p>${esc(topic.why_now||topic.reason||'从具体问题出发，找到观众关心的解释。')}</p></div><div><span class="insight-label">02 / 你可以补上的一块</span><p>${esc(topic.content_gap||topic.preparation||'用自己的实例与体验，补充不同视角。')}</p></div></div>${facts.length?`<div class="fact-list">${facts.map(f=>`<div><span class="fact-dot"></span><p>${esc(typeof f==='string'?f:f.claim)}<span class="source-chips">${sourcesHTML(f.source_urls||[])}</span></p></div>`).join('')}</div>`:''}
    <details class="quiet-details"><summary>不同观点与观众反应</summary><div class="research-insights"><div><h3>常见叙事</h3><p>${esc((topic.narratives||[]).join('；')||'暂无可引用的创作者观点')}</p></div><div><h3>观众反应</h3><p>${esc((topic.audience_reactions||[]).join('；')||'暂无可引用的观众反应')}</p></div></div></details><div class="evidence-strip"><span>研究依据</span>${sourcesHTML(topic.source_urls||[])}${vbutton('sources',`查看全部 ${S.detail.sources.length} 个来源 →`,'text-button small')}</div></div>
    <section class="angle-section"><div class="section-heading"><div><div class="eyebrow">YOUR CREATIVE DECISION</div><h2>选一个角度，接下来交给我。</h2></div><span class="quiet-label">${angles.length} 个讲法</span></div><div class="angle-grid">${angles.map((a,i)=>`<article class="angle-card ${wcontext().angle?.title===a.title?'chosen':''}"><div class="angle-number">${String(i+1).padStart(2,'0')}<span>${i<3?['从好奇心切入','把问题讲透','换一个视角'][i]:a.custom?'你的方向':'新方向'}</span></div><h3>${esc(a.title)}</h3><p>${esc(a.reason||topic.reason||'围绕这个问题展开一个具体故事')}</p>${a.hook?`<blockquote>“${esc(a.hook)}”</blockquote>`:''}<dl><dt>讲给谁</dt><dd>${esc(a.audience||S.defaults.audience)}</dd><dt>不同之处</dt><dd>${esc(a.difference||'用你的体验和实例讲述')}</dd></dl>${vbutton('angle','用这个角度写脚本 ↗','primary',`data-version="${v.id}" data-topic="${topic.id}" data-angle="${esc(a.id)}" ${activeTask()?'disabled':''}`)}${vbutton('adjust-angle','调整后再用','text-button small',`data-version="${v.id}" data-topic="${topic.id}" data-angle="${esc(a.id)}" ${activeTask()?'disabled':''}`)}</article>`).join('')}</div>${angleTools(v,topic)}</section>
    ${v.result.limitations?.length?`<details class="quiet-details research-limits"><summary>研究范围与待核实事项</summary><p>${v.result.limitations.map(esc).join('<br>')}</p><p>${(topic.verify||[]).map(esc).join('<br>')}</p></details>`:''}`;
}

function workspaceScript(version) {
  const r=scriptResult();
  const draft=readDraft(), conflict=draft&&version&&draft.parent_id!==version.id;
  if(!r?.paragraphs)return empty('✎',activeTask()?'第一版脚本正在路上':'好脚本，从一个明确的角度开始',activeTask()?'开场、关键内容和结尾会一起准备好。':'回到研究，选择你最想讲的那个角度。',vbutton('tab','查看研究与角度 →','primary',`data-tab="research"`));
  return `<section class="script-document"><div class="section-heading"><div><span class="eyebrow">MAKE IT SOUND LIKE YOU</span><h2>脚本与拍摄准备</h2></div><span class="quiet-label">约 ${Math.round(r.estimated_sec||0)} 秒 · ${r.paragraphs.length} 个段落</span></div>${wproject().stale_stages?.includes('script')?'<div class="inline-notice">创作方向已改变。这份脚本仍然保留，请从新角度继续写稿。</div>':''}<p class="section-caption">直接修改文字，或让 AI 帮你打磨。每一处修改都会被记住。</p><div class="script-beats">${r.paragraphs.map((p,i)=>`<span>${beatNames[p.beat]|| (i===0?'开场':i===r.paragraphs.length-1?'收尾':'推进')}</span>`).join('<i>→</i>')}</div>
    ${conflict?`<div class="inline-notice"><span>脚本已有更新，你的编辑仍在这里。选择要继续使用的表达。</span><div class="button-row">${vbutton('keep-draft','保留我的编辑','small')}${vbutton('use-latest','使用最新脚本','small ghost')}</div></div>`:''}<div class="script-paragraphs">${r.paragraphs.map((p,i)=>`<article class="script-block" data-paragraph="${esc(p.id)}"><div class="script-block-heading"><span class="beat-label">${String(i+1).padStart(2,'0')} / ${beatNames[p.beat]||(i===0?'开场钩子':i===r.paragraphs.length-1?'收尾':'故事推进')}</span><select class="speaker-label" data-script-speaker="${esc(p.id)}" aria-label="第 ${i+1} 段角色">${[...new Set(['旁白','A','B','音乐',p.speaker||'旁白'])].map(v=>`<option ${v===(p.speaker||'旁白')?'selected':''}>${esc(v)}</option>`).join('')}</select>${r.paragraphs.length>1?vbutton('remove-paragraph','删除','text-button small',`data-id="${esc(p.id)}"`):''}<label class="lock-label"><input type="checkbox" data-script-lock="${esc(p.id)}" ${p.locked?'checked':''}>锁定</label></div><textarea data-script-text="${esc(p.id)}" aria-label="第 ${i+1} 段脚本" rows="${Math.max(2,Math.min(6,Math.ceil(p.text.length/40)))}">${esc(p.text)}</textarea><div class="script-block-footer"><div class="rewrite-actions">${['更自然','更有冲击力','缩短'].map(instruction=>vbutton('rewrite',instruction,'rewrite-chip',`data-id="${esc(p.id)}" data-instruction="${instruction}" ${p.locked||activeTask()?'disabled':''}`)).join('')}<select class="rewrite-more" data-rewrite-more="${esc(p.id)}" aria-label="更多段落修改" ${p.locked||activeTask()?'disabled':''}><option value="">更多修改 ⌄</option>${['更口语化','更专业','展开','换个表达','重新写这一段'].map(a=>`<option>${a}</option>`).join('')}</select></div><span class="paragraph-duration">约 ${p.estimated_sec||Math.round(p.text.length/4.2)} 秒</span></div><div class="paragraph-evidence">${sourcesHTML(p.source_urls||[])}<span>${p.evidence_status==='review'?'文字已修改 · 请复核引用':p.source_urls?.length?'引用研究资料 · 可查看原文':p.claim_type==='opinion'?'个人观点':'暂无关联证据 · 事实请核实'}</span></div><details class="quiet-details cue-details"><summary>画面想法</summary><input data-script-cue="${esc(p.id)}" aria-label="第 ${i+1} 段画面建议" value="${esc(p.cue)}"></details></article>`).join('')}</div><div class="script-actions">${vbutton('add-paragraph','＋ 添加段落','small')}${vbutton('export-script','↓ 导出拍摄包','small')}</div><div class="script-bottom"><span class="small-note">脚本会作为视频制作的参考，Claude 会按整体创意制作，不会机械地逐段切镜头。</span>${vbutton('to-video','脚本就这样，去制作视频 →','primary')}</div></section>`;
}

function cliComposer(cut) {
  const cli=S.env.claude_cli||{}, task=activeTask(), last=S.detail?.tasks.find(t=>t.kind==='cli_video');
  const prompt=sessionStorage.getItem(`studio-cli-prompt:${S.selected}`)??(cut?'':wcontext().idea||wproject()?.name||'');
  return `<section class="card cli-composer"><div class="card-head"><h2>${cut?'继续打磨这条视频':'描述你想制作的视频'}</h2><span class="chip">Claude · ${esc(cli.options?.model||'opus')}</span></div><label for="cli-prompt" class="sr-only">视频制作或修改要求</label><textarea id="cli-prompt" maxlength="20000" rows="4" placeholder="例如：用上传的素材制作 30 秒竖屏视频，开头先展示最精彩的一幕，音乐示例完整保留。">${esc(prompt)}</textarea><div class="cli-composer-footer"><span class="small-note">${cli.installed?'使用这台电脑上的 Claude Code':'先在设置中连接本机 Claude Code'}</span>${vbutton('cli-run',cut?'按要求生成新版 →':'让 Claude 制作视频 →','primary small',task?'disabled':'')}</div>${!cli.installed?vbutton('connect','设置本机 Claude Code →','text-button small'):''}${last?.status==='completed'&&last.cli_result?.num_turns?`<p class="small-note">上次制作完成 · ${last.cli_result.num_turns} 轮执行 · ${Math.round((last.elapsed_sec||0))} 秒</p>`:''}</section>`;
}

function cliSettingsCard() {
  const cli=S.env.claude_cli||{}, o=cli.options||{}, check=cli.last_check;
  return `<section class="card cli-settings"><div class="card-head"><h2>Claude Code 订阅连接</h2><span class="chip ${check?.logged_in&&check?.supported?'green':'warn'}">${check?.logged_in&&check?.supported?'已就绪':cli.installed?'待检测登录':'等待连接'}</span></div><p class="section-note">调用本机已安装并登录的 Claude Code。调研、文案和视频可以共用订阅登录；API 连接单独管理。</p><div class="field"><label for="cli-path">Claude Code 路径</label><input id="cli-path" value="${esc(o.path||'')}" placeholder="自动查找；也可填写 claude.exe 完整路径"></div><div class="publishing-grid"><div class="field"><label for="cli-model">视频制作模型</label><select id="cli-model">${[...new Set(['opus','sonnet','haiku',o.model||'opus'])].map(m=>`<option value="${esc(m)}" ${m===(o.model||'opus')?'selected':''}>${esc({opus:'Opus',sonnet:'Sonnet',haiku:'Haiku'}[m]||m)}</option>`).join('')}</select></div><div class="field"><label for="cli-timeout">单个 CLI 任务超时（分钟）</label><input id="cli-timeout" type="number" min="1" max="360" value="${Math.round((o.timeout_sec||7200)/60)}"></div></div><details class="details"><summary>执行上限</summary><div class="field"><label for="cli-turns">最多执行轮数</label><input id="cli-turns" type="number" min="1" max="2000" value="${o.max_turns||300}"></div></details><div class="button-row">${vbutton('cli-check','保存并检测安装与登录','primary small')}${vbutton('cli-save','仅保存','small')}</div><div class="connection-result" role="status">${esc(check?.message||cli.error||'先安装 Claude Code，在终端运行 claude auth login 登录，再点击检测。')}${check?.version?` · v${esc(check.version)}`:''}</div><p class="section-note">需要 Claude Code 2.1.248 或以上。调研与写稿不需要媒体工具；视频制作另外需要 FFmpeg / ffprobe 和 Windows Bash，建议安装 Node.js 以便制作动态图形。检测不会生成内容，模型调用需要联网。</p><p class="section-note">视频制作时 Claude 可以联网、安装 Python / Node 依赖、使用全部工具；只拦截删除或移动任务目录以外的文件、修改系统设置、读取凭据和对外发布等操作。依赖与模板保存在 .runtime/agent-toolbox，可重复使用。</p></section>`;
}

function renderCliEdit(v) {
  const r=v.result,a=v.final||v.preview;
  return `<section class="card"><div class="card-head"><h2>视频与制作记录</h2><span class="chip">Claude 制作</span></div>${versionBar('edit')}<video id="preview-player" controls preload="metadata" style="width:100%;max-height:520px" src="${fileURL(a.video)}"></video><p>${esc(r.summary)}</p>${artifactLinks(a)}</section>${projectMaterials()}<input id="workspace-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden>`;
}
const BRIEF_PRESETS = [
  ['剪辑我的素材','用这些素材剪一条约 60 秒的竖屏视频：开头 3 秒抓住注意力，节奏紧凑，画面和音乐卡点，结尾有记忆点。'],
  ['参考一个视频','参考素材里那条视频的节奏、镜头长度、转场和画面风格，用我的其他素材做一条风格相近的视频。'],
  ['从零生成','不使用任何素材，用动态图形和文字动画做一条约 30 秒的视频，主题是：'],
];
function briefAsset(a) {
  const type=mediaType(a), frame=a.analysis?.frames?.[0];
  const thumb=type==='Image'?`<img src="${assetURL(a)}" alt="">`:frame?`<img src="/api/assets/${a.id}/frames/0" alt="">`:`<span>${type==='Music'||type==='Audio'?'♫':'▷'}</span>`;
  return `<div class="brief-asset" data-brief-asset="${a.id}"><div class="brief-thumb">${thumb}</div><div class="brief-asset-main"><div class="brief-asset-title"><strong title="${esc(a.name)}">${esc(a.name)}</strong><small>${type}${a.analysis?.duration&&type!=='Image'?' · '+timecode(a.analysis.duration):''}</small></div></div></div>`;
}
function videoBrief(cut) {
  const cli=S.env.claude_cli||{}, task=activeTask(), last=S.detail?.tasks.find(t=>t.kind==='cli_video');
  const own=S.detail.assets.filter(a=>!a.generated);
  const prompt=sessionStorage.getItem(`studio-cli-prompt:${S.selected}`)??(cut?'':wcontext().idea||wproject()?.name||'');
  return `<section class="card video-brief"><div class="card-head"><h2>${cut?'继续打磨这条视频':'这条视频，你想要什么样？'}</h2><span class="chip">Claude · ${esc(cli.options?.model||'opus')}</span></div>
    <p class="section-note">写下要求，Claude 会自己完成整条视频。发给 Claude 的只有这段要求，不附加其他规则。</p>
    <div class="brief-presets" aria-label="快速填写">${BRIEF_PRESETS.map(([label],i)=>vbutton('brief-preset',label,'rewrite-chip',`data-index="${i}"`)).join('')}</div>
    <label for="cli-prompt" class="sr-only">视频制作或修改要求</label><textarea id="cli-prompt" maxlength="20000" rows="5" placeholder="例如：参考那条风格视频，用我的素材剪一条 45 秒的竖屏视频，开头先放最精彩的一幕，配乐完整保留。">${esc(prompt)}</textarea>
    <div class="brief-options">${cut?`<label class="brief-toggle"><input type="checkbox" id="brief-continue" checked> 在当前版本上修改（取消则重新制作一版）</label>`:''}</div>
    <div class="brief-materials"><div class="brief-materials-head"><h3>素材 <span class="muted">${own.length}</span></h3><div class="button-row">${vbutton('public-picker','从公共库添加','text-button small')}</div></div>${assetUploadControl()}
    ${own.length?`<div class="brief-asset-list">${own.map(briefAsset).join('')}</div><p class="small-note brief-legend">素材会放在 Claude 工作文件夹的「素材」目录里，想怎么用直接写在要求中。</p>`:'<p class="muted brief-empty">没有素材也可以开始：Claude 会用动态图形和文字动画从零制作。</p>'}</div>
    <div class="cli-composer-footer"><span class="small-note">${cli.installed?'使用这台电脑上的 Claude Code · 可联网、安装制作工具':'先在设置中连接本机 Claude Code'}</span>${vbutton('cli-run',cut?'按要求生成新版 →':'让 Claude 制作完整视频 →','primary',task?'disabled':'')}</div>${!cli.installed?vbutton('connect','设置本机 Claude Code →','text-button small'):''}${last?.status==='completed'&&last.cli_result?.num_turns?`<p class="small-note">上次制作完成 · ${last.cli_result.num_turns} 轮执行 · ${Math.max(1,Math.round((last.elapsed_sec||0)/60))} 分钟</p>`:''}</section>`;
}
function advancedScenes(scenes,cut) {
  if(!scenes&&!wversion('script')?.result?.paragraphs)return '';
  const stale=wproject().stale_stages||[];
  const body=scenes?`<div class="video-toolbar"><p class="small-note">把脚本按段落变成分镜，逐个镜头选择画面，再由本地工具拼接初剪。</p>${vbutton('first-cut',cut?'按分镜重新组装':'按分镜生成初剪','small',activeTask()||stale.includes('scenes')?'disabled':'')}</div>${stale.includes('scenes')?`<div class="inline-notice">脚本已有新版，需要同步分镜。${vbutton('build-scenes','同步最新脚本','small')}</div>`:''}<div class="scene-list">${scenes.result.scenes.map(sceneCard).join('')}</div><div class="scene-footnote"><span>∿</span><p>没有合适素材的镜头会先制作成文字卡。<br><small>保留素材原声；没有原声的镜头暂不含配音。</small></p></div>`
    :`<p class="small-note">把脚本按段落变成分镜，逐个镜头选择画面，再由本地工具拼接初剪。适合需要逐镜头控制的情况。</p>${vbutton('build-scenes','从当前脚本生成分镜','small',activeTask()?'disabled':'')}`;
  return `<details class="details advanced-scenes" data-advanced ${W.advancedOpen?'open':''}><summary>高级选项：按分镜手动组装${scenes?` · ${scenes.result.scenes.length} 个分镜`:''}</summary>${body}</details>`;
}
function workspaceVideo(scenes,cut) {
  return (cut?cutPreview(cut):'')+videoBrief(cut)+advancedScenes(scenes,cut)+`<input id="workspace-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden>`;
}

function sceneCard(scene,i) {
  const asset=S.detail.assets.find(a=>a.id===scene.asset_id), frame=asset?.analysis?.frames?.[0];
  return `<article class="scene-card"><div class="scene-visual">${asset?.kind==='image'?`<img src="${assetURL(asset)}" alt="${esc(asset.name)}">`:frame?`<img src="${assetURL({...asset,path:frame.path})}" alt="${esc(asset.name)}">`:`<div class="scene-placeholder"><span>${asset?'▷':'Aa'}</span><small>${asset?'已选视频素材':'文字卡'}</small></div>`}<span class="scene-index">${String(i+1).padStart(2,'0')}</span></div><div class="scene-content"><div class="scene-top"><span class="beat-label">SCENE ${String(i+1).padStart(2,'0')}</span><span>${timecode(scene.start)} — ${timecode(scene.end)}</span></div><h3>${esc(scene.narration)}</h3><p class="scene-direction">${esc(scene.visual)}</p><div class="scene-meta"><span>${asset?'▧ '+esc(asset.name):'Aa 自动制作文字卡'}</span><span>${esc(scene.editing)}</span></div><div class="scene-actions">${vbutton('asset-picker',asset?'替换画面':'选择画面','small',`data-id="${scene.id}"`)}${vbutton('scene-duration','调整时长','text-button small',`data-id="${scene.id}"`)}<span class="quiet-label">分镜来源：脚本</span></div></div></article>`;
}

function cutPreview(cut) {
  const r=cut.result, artifacts=cut.final||cut.preview;
  return `<div class="cut-heading"><div><span class="eyebrow">YOUR VIDEO, VERSION ${versionNumber(cut)}</span><h2>视频已经就位，看看感觉。</h2></div>${wproject().stale_stages.includes('edit')?'<span class="chip warn">有修改待同步</span>':`<span class="chip green">${r.engine==='claude_cli'?'Claude 已完成':'已与分镜同步'}</span>`}</div><div class="cut-player"><video id="cut-player" controls preload="metadata" src="${fileURL(artifacts.video)}"></video></div><div class="cut-meta"><span>${timecode(r.duration)} · ${esc(r.aspect)} · ${r.engine==='claude_cli'?'Claude 制作':r.segments.length+' 个镜头'}</span><span>${r.engine==='claude_cli'?(r.has_recorded_audio?'含音频':'暂无声音'):r.has_recorded_audio===false?'文字卡初剪 · 暂无配音':'保留素材原声'}</span></div><div class="scene-timeline">${r.segments.map((s,i)=>`<button class="timeline-scene" data-action="v2-seek" data-time="${s.output_start_sec}" style="flex:${s.output_end_sec-s.output_start_sec}" title="跳到镜头 ${i+1}"><span>${String(i+1).padStart(2,'0')}</span><small>${timecode(s.output_start_sec)}</small></button>`).join('')}</div>${r.notes?.length?`<details class="quiet-details"><summary>${r.engine==='claude_cli'?'制作与检查记录':'这版用了哪些画面'}</summary><p>${r.notes.map(esc).join('<br>')}</p></details>`:''}<div class="cut-actions">${vbutton('advanced-edit',r.engine==='claude_cli'?'查看版本与制作记录':'精细调整时间线','text-button')}${artifacts?.bundle?`<a class="download-primary" href="${fileURL(artifacts.bundle)}" download>↓ 下载发布包</a>`:vbutton('export','导出高清成片 ↗','primary',activeTask()?'disabled':'')}</div>${artifactLinks(cut.final)}<p class="small-note">发布前播放检查声音、画面和事实引用。需要修改时在下方写下要求，会生成新版本，旧版本保留。</p>`;
}

function renderLibrary() {return renderAssetLibrary();}

function renderFilms() {
  return `<div class="collection-shell">${heading('做出来的每一版，都值得留下。','预览与高清成片集中保存，随时回到创作继续打磨。',false,'THE SCREENING ROOM')}<div class="library-grid">${S.library.films.map(v=>`<article class="library-card"><div class="film-thumbnail"><video controls preload="metadata" src="${assetURL({project_id:v.project_id,path:(v.final||v.preview).video})}"></video></div><h3>${esc(v.project_name)}</h3><p>${v.final?'高清成片':'初剪预览'} · ${date(v.created_at)}</p>${vbutton('open','回到这个作品 →','text-button',`data-id="${v.project_id}" data-tab="video"`)}<a href="${assetURL({project_id:v.project_id,path:(v.final||v.preview).video})}" download>下载视频 ↓</a></article>`).join('')}</div>${!S.library.films.length?empty('▷','下一条，就是你的第一条。','写下制作要求，Claude 会制作完整视频。',vbutton('works','继续创作','primary')):''}</div>`;
}

async function navigateV2(page) {
  capture();
  if(S.page==='workspace'&&readDraft())await flushScript();
  if(page==='research'||page==='script')return openWorkspace(S.selected,page);
  S.page=page;
  location.hash=page;
  render();
  if(page==='home')$('#creation-idea')?.focus();
}
async function routeV2() {
  const parts=location.hash.slice(1).split('/');
  if(['research','script'].includes(parts[0])&&S.selected)return openWorkspace(S.selected,parts[0]);
  if(parts[0]==='workspace') {
    if(!parts[1])return navigateV2('home');
    const changed=S.selected!==parts[1];
    S.selected=parts[1];S.page='workspace';W.tab=workspaceTabs[parts[2]]?parts[2]:'research';
    localStorage.setItem('studio-project',S.selected);
    if(changed||S.detail?.project.id!==S.selected)await refresh(false);
  } else if(stageNames[parts[0]])S.page=parts[0];
  else S.page='home';
  render();
}
async function openWorkspace(id,tab='research') {
  if(S.page==='workspace'&&readDraft())await flushScript();
  S.selected=id;S.page='workspace';S.versions={};W.tab=tab;W.topic=null;
  localStorage.setItem('studio-project',id);
  await refresh(false);
  if(tab==='video'&&wversion('edit')?.preview)W.videoView='cut';else W.videoView='scenes';
  history.pushState(null,'',`#workspace/${id}/${tab}`);
  render();
}

function clarifyPreference() {const box=$('#creation-clarify');if(box)return box.checked;try{return localStorage.getItem('studio-clarify')!=='0';}catch{return true;}}
async function createIdea(idea,intent='idea') {
  if(W.busy)return;
  if(!idea.trim()) {$('#creation-idea')?.focus();throw new Error('写下一句话，或粘贴一个参考链接，就能开始。');}
  W.busy=true;
  try {
    const result=await api('/creations',{idea,intent,duration:Number($('#creation-duration')?.value||60),aspect:$('#creation-aspect')?.value||'9:16',clarify:!['video','assets'].includes(intent)&&clarifyPreference()});
    sessionStorage.removeItem('studio-idea');
    $('#toast').className='';
    await openWorkspace(result.project.id,['video','assets'].includes(intent)?'video':'research');
    if(result.needs_cli)toast('想法已保存。请在设置中检测本机 Claude Code，然后开始制作。');
    return result;
  } finally {W.busy=false;}
}

function evidenceModal(url) {
  const source=S.detail.sources.find(s=>s.url===url);
  modal('这句话背后的资料',source?`<span class="chip">${esc({fetched:'已读取网页',search_only:'搜索摘录',user_supplied:'你提供的参考',unverified:'待核实'}[source.verification]||'待核实')}</span><h3 class="source-modal-title">${esc(source.title||source.url)}</h3><p>${esc(source.evidence_note||'暂无摘录，请查看原文。')}</p><div class="source-modal-meta">${esc(source.platform||'Web')} · ${source.published_at?date(source.published_at):'原文日期未核实'}</div><p>${link(source.url,'打开原始来源')}</p><p class="small-note">引用用于追溯依据。请对照原文确认这句话是否被支持。</p>`:`<p>该引用尚未关联到研究资料。</p>${link(url,'打开链接')}`);
}

function captureScript() {
  if(!$$('[data-script-text]').length)return readDraft();
  const previous=readDraft(), version=wversion('script');
  if(!previous&&!version)return null;
  const result=structuredClone(previous?.result||version.result);
  result.paragraphs.forEach(p=>{
    const el=$$('[data-script-text]').find(el=>el.dataset.scriptText===p.id);
    const cue=$$('[data-script-cue]').find(el=>el.dataset.scriptCue===p.id);
    const lock=$$('[data-script-lock]').find(el=>el.dataset.scriptLock===p.id);
    if(el&&p.text!==el.value){p.text=el.value;p.evidence_status='review';}
    const speaker=$$('[data-script-speaker]').find(el=>el.dataset.scriptSpeaker===p.id);if(speaker)p.speaker=speaker.value;
    if(cue)p.cue=cue.value;
    if(lock)p.locked=lock.checked;
  });
  const draft={parent_id:previous?.parent_id||version.id,result,revision:Date.now()};
  localStorage.setItem(draftKey(S.selected),JSON.stringify(draft));
  if($('#draft-status'))$('#draft-status').textContent='正在保存…';
  return draft;
}
async function flushScript() {
  clearTimeout(W.saveTimer);
  if(W.saving)await W.saving;
  const id=S.selected,draft=readDraft(id);
  if(!draft)return wversion('script');
  W.saving=(async()=>{
    const saved=await api(`/projects/${id}/workspace/script`,{parent_id:draft.parent_id,result:draft.result});
    const latest=readDraft(id);
    if(latest?.revision===draft.revision)localStorage.removeItem(draftKey(id));
    else if(latest){latest.parent_id=saved.id;localStorage.setItem(draftKey(id),JSON.stringify(latest));}
    if(S.selected===id){
      S.detail.versions.unshift(saved);wproject().workspace={...wcontext(),script_version:saved.id};
      if($('#draft-status'))$('#draft-status').textContent=readDraft(id)?'正在保存…':'已保存';
    }
    return saved;
  })();
  try{return await W.saving;}
  catch(error){
    if(error.message.includes('脚本已有更新')&&S.selected===id){
      S.detail=await api(`/projects/${id}`);
      if(S.page==='workspace'&&W.tab==='script')render();
    }
    throw error;
  }finally{W.saving=null;}
}

async function patchScene(sceneId,body) {
  await api(`/projects/${S.selected}/workspace/scenes`,{version_id:wversion('scenes').id,scene_id:sceneId,...body},'PATCH');
  await refresh();
}
function assetPicker(sceneId) {
  const scene=wversion('scenes').result.scenes.find(s=>s.id===sceneId), own=S.detail.assets.filter(a=>!a.generated);
  const shared=S.library.assets.filter(a=>a.scope==='public');
  const row=(a,shared=false)=>`<div class="picker-asset"><span class="asset-kind">${a.kind==='image'?'▧':'▷'}</span><div><strong>${esc(a.name)}</strong><small>${shared?'来自 '+esc(a.project_name):scene.recommendations?.find(r=>r.asset_id===a.id)?.reason||'项目素材'}</small></div>${vbutton(shared?'import-asset':'choose-asset','使用','small',`data-id="${a.id}" data-scene="${sceneId}"`)}</div>`;
  modal('给这个镜头选一幅画面',`<p>${esc(scene.visual)}</p><div class="picker-list">${own.map(a=>row(a)).join('')}${shared.length?'<h3>素材库</h3>'+shared.map(a=>row(a,true)).join(''):''}${!own.length&&!shared.length?'<p class="muted">还没有可用素材，上传视频或图片就能放进这个镜头。</p>':''}</div><div class="picker-asset"><span class="asset-kind">Aa</span><div><strong>自动文字卡</strong><small>用文字画面表达这个镜头</small></div>${vbutton('choose-asset','使用','small',`data-id="" data-scene="${sceneId}"`)}</div>`,vbutton('upload','＋ 上传画面','primary',`data-scene="${sceneId}"`));
}

async function handleV2(action,el) {
  const a=action.slice(3);
  if(a==='browse-inspiration')return navigate('inspiration');
  if(a==='favorite') {if(el.dataset.favorite)await api(`/inspirations/favorites/${el.dataset.favorite}`,{},'DELETE');else await api('/inspirations/favorites',{id:el.dataset.id});S.inspirations=await api('/inspirations');render();return;}
  if(a==='inspiration-create'){const result=await api('/inspirations/create',{id:el.dataset.id,clarify:clarifyPreference()});return openWorkspace(result.project.id,'research');}
  if(a==='inspire'){
    await api('/inspirations/generate',{web:el.dataset.web==='1',direction:$('#inspire-direction')?.value||'',feedback:$('#inspire-feedback')?.value||''});
    W.inspireFeedback='';S.inspirations=await api('/inspirations');W.inspirationTab='ideas';render();watchInspiration();return;
  }
  if(a==='inspire-cancel'){await api('/inspirations/cancel',{});S.inspirations=await api('/inspirations');render();return;}
  if(a==='inspire-undo'){S.inspirations=await api('/inspirations/undo',{});render();toast('已换回上一批灵感。');return;}
  if(a==='inspire-dismiss'){await api(`/inspirations/${el.dataset.id}/dismiss`,{});S.inspirations=await api('/inspirations');render();toast('已移除，之后的灵感会避开类似题目。');return;}
  if(a==='inspiration-tab'){W.inspirationTab=el.dataset.tab;render();return;}
  if(a==='chat-send'||a==='chat-option'||a==='chat-start'){
    const input=$('#chat-input'),message=(a==='chat-option'?el.dataset.text:a==='chat-start'?(wcontext().idea||wproject().name):input?.value||'').trim();
    if(!message){input?.focus();throw new Error('先写下你想说的话。');}
    await api(`/projects/${S.selected}/workspace/chat`,{message});
    if(a==='chat-send'){sessionStorage.removeItem(`studio-chat:${S.selected}`);if(input)input.value='';}
    await refresh();$('#chat-input')?.focus();return;
  }
  if(a==='chat-action'){
    const task=await api(`/projects/${S.selected}/workspace/chat/action`,{message_id:el.dataset.id});
    if(task.kind==='script'&&W.tab!=='script'){W.tab='script';W.chatOpen=false;history.pushState(null,'',`#workspace/${S.selected}/script`);}
    else if(['research','angles'].includes(task.kind)&&W.tab!=='research'){W.tab='research';history.pushState(null,'',`#workspace/${S.selected}/research`);}
    await refresh();return;
  }
  if(a==='chat-toggle'){W.chatOpen=!W.chatOpen;const drawer=$('.chat-drawer');if(drawer){drawer.classList.toggle('open',W.chatOpen);$('.chat-fab')?.setAttribute('aria-expanded',String(W.chatOpen));if(W.chatOpen){const thread=drawer.querySelector('.chat-thread');if(thread)thread.scrollTop=thread.scrollHeight;$('#chat-input')?.focus();}}return;}
  if(a==='more-angles'){
    await api(`/projects/${S.selected}/workspace/angles`,{version_id:el.dataset.version,topic_id:el.dataset.topic,feedback:$('#angle-feedback')?.value||'',model:workspaceModel('script')});
    W.angleFeedback='';await refresh();return;
  }
  if(a==='custom-angle'){modal('写下你自己的方向',`<label for="custom-angle-title">方向（一句话）</label><input id="custom-angle-title" maxlength="200" placeholder="例如：用合唱团排练的一晚，讲松弛感从哪里来"><label for="custom-angle-detail">补充说明（可选）</label><textarea id="custom-angle-detail" rows="4" maxlength="2000" placeholder="想怎么开场、重点讲什么、希望观众最后记住什么"></textarea><p class="small-note">会结合已有研究资料，直接写成脚本。</p>`,button('close-modal','取消','ghost')+vbutton('save-custom-angle','用这个方向写脚本','primary',`data-version="${el.dataset.version||''}" data-topic="${el.dataset.topic||''}"`));$('#custom-angle-title').focus();return;}
  if(a==='save-custom-angle'){
    const title=$('#custom-angle-title').value.trim();if(!title)throw new Error('请写下你的方向。');
    await api(`/projects/${S.selected}/workspace/angle/custom`,{title,detail:$('#custom-angle-detail').value,version_id:el.dataset.version||null,topic_id:el.dataset.topic||null,model:workspaceModel('script')});
    $('#modal').close();W.tab='script';history.pushState(null,'',`#workspace/${S.selected}/script`);await refresh();return;
  }
  if(a==='adjust-angle'){
    const topic=wversion('research').result.topics.find(t=>t.id===el.dataset.topic),angle=allAngles(topic).find(x=>x.id===el.dataset.angle);
    modal('在这个方向上调整',`<p class="adjust-angle-title">${esc(angle.title)}</p><label for="angle-note">你想怎么调整？</label><textarea id="angle-note" rows="4" maxlength="2000" placeholder="例如：保留这个观点，但开场换成一个真实的故事，语气轻松一点"></textarea>`,button('close-modal','取消','ghost')+vbutton('angle','按调整写脚本','primary',`data-version="${el.dataset.version}" data-topic="${el.dataset.topic}" data-angle="${esc(el.dataset.angle)}" data-adjust="1"`));$('#angle-note').focus();return;
  }
  if(a==='library-scope'){W.libraryScope=el.dataset.scope;render();return;}
  if(a==='public-upload')return $('#public-assets').click();
  if(a==='manage-asset')return manageAssetModal(el.dataset.id);
  if(a==='save-asset'){const owner=$('#asset-owner').value;await api(`/assets/${el.dataset.id}`,{name:$('#asset-name').value,type:$('#asset-type').value,scope:owner?'project':'public',project_id:owner||null},'PATCH');$('#modal').close();await refresh();toast('素材类型与归属已保存');return;}
  if(a==='public-picker'){modal('公共素材库',`<div class="picker-list">${S.library.assets.filter(a=>a.scope==='public').map(a=>`<div class="picker-asset"><div><strong>${esc(a.name)}</strong><small>${mediaType(a)}</small></div>${vbutton('add-public-asset','添加到项目','small',`data-id="${a.id}"`)}</div>`).join('')||'<p>公共库还没有素材，请先到素材库上传。</p>'}</div>`);return;}
  if(a==='add-public-asset'){await api(`/projects/${S.selected}/workspace/import-asset`,{asset_id:el.dataset.id});$('#modal').close();await refresh();return;}
  if(a==='rename-project'){const p=S.projects.find(p=>p.id===el.dataset.id)||wproject();modal('重命名作品',`<label for="project-name">作品名称</label><input id="project-name" maxlength="120" value="${esc(p.name)}">`,button('close-modal','取消','ghost')+vbutton('save-project-name','保存','primary',`data-id="${p.id}"`));$('#project-name').select();return;}
  if(a==='save-project-name'){const name=$('#project-name').value.trim();if(!name)throw new Error('请填写作品名称。');await api(`/projects/${el.dataset.id}`,{name},'PATCH');$('#modal').close();await refresh();toast('已重命名。');return;}
  if(a==='delete-project'){const p=S.projects.find(p=>p.id===el.dataset.id);if(!p)throw new Error('这个作品已经不存在，请刷新页面。');modal('删除这个作品？',`<p>将永久删除「${esc(p.name)}」的研究、脚本、分镜、项目素材和所有视频版本。此操作无法撤销。</p><p class="small-note">公共素材库中的素材不受影响。</p>`,button('close-modal','取消','ghost')+vbutton('delete-project-confirm','永久删除','danger',`data-id="${p.id}"`));return;}
  if(a==='delete-project-confirm'){
    const id=el.dataset.id;await api(`/projects/${id}`,{},'DELETE');$('#modal').close();localStorage.removeItem(draftKey(id));
    if(S.selected===id){S.selected=null;S.detail=null;localStorage.removeItem('studio-project');if(S.page==='workspace'){S.page='projects';history.replaceState(null,'','#projects');}}
    await refresh();toast('作品已删除。');return;
  }
  if(a==='remove-paragraph'&&!el.dataset.confirmed&&$(`[data-script-text="${CSS.escape(el.dataset.id)}"]`)?.value.trim()){modal('删除这一段？',`<p>这一段的文字和画面想法会从脚本中移除。之前保存的脚本版本仍可在「版本记录」中找回。</p>`,button('close-modal','取消','ghost')+vbutton('remove-paragraph','删除这一段','danger',`data-id="${el.dataset.id}" data-confirmed="1"`));return;}
  if(a==='add-paragraph'||a==='remove-paragraph'){if($('#modal').open)$('#modal').close();
    const draft=captureScript()||{parent_id:wversion('script').id,result:structuredClone(scriptResult())};
    if(a==='add-paragraph')draft.result.paragraphs.push({id:crypto.randomUUID().replaceAll('-',''),text:'',speaker:'旁白',cue:'',locked:false,source_urls:[],claim_type:'opinion'});
    else draft.result.paragraphs=draft.result.paragraphs.filter(p=>p.id!==el.dataset.id);
    if(!draft.result.paragraphs.length)throw new Error('脚本至少保留一个段落');
    draft.revision=Date.now();localStorage.setItem(draftKey(S.selected),JSON.stringify(draft));await flushScript();await refresh();return;
  }
  if(a==='export-script'){const v=await flushScript()||wversion('script');const link=document.createElement('a');link.href=`/api/projects/${S.selected}/versions/${v.id}/shooting-pack`;link.download='shooting-pack.md';link.click();return;}
  if(a==='load-workspace-template'){
    const t=S.templates.find(t=>t.id===$('#workspace-template').value);if(!t)throw new Error('请选择模板');
    if(t.model&&!S.models.some(m=>m.id===t.model))throw new Error('模板模型与当前服务不兼容，请切换对应 Provider');
    const stage=el.dataset.stage;S.prompts[`${S.selected}:${stage}`]=t.prompt;S.settings[`${S.selected}:${stage}`]=structuredClone(t.settings||{});if(t.model)S.taskModels[`${S.selected}:${stage}`]=t.model;render();toast('模板已载入，下次任务生效');return;
  }
  if(a==='save-workspace-template'){modal('保存阶段模板','<label>模板名称</label><input id="workspace-template-name" maxlength="100">',vbutton('confirm-workspace-template','保存','primary',`data-stage="${el.dataset.stage}"`));return;}
  if(a==='confirm-workspace-template'){const stage=el.dataset.stage;await api('/templates',{name:$('#workspace-template-name').value,stage,prompt:S.prompts[`${S.selected}:${stage}`]||'',settings:S.settings[`${S.selected}:${stage}`]||{},model:workspaceModel(stage)});$('#modal').close();await refresh();return;}
  if(a==='regenerate-script'){if(readDraft())await flushScript();await api(`/projects/${S.selected}/tasks`,{kind:'script',prompt:S.prompts[`${S.selected}:script`]||'根据当前研究与角度生成完整脚本',model:workspaceModel('script'),settings:S.settings[`${S.selected}:script`]||{},base_version_id:wversion('script')?.id,workspace:true});await refresh();return;}
  if(a==='return'){location.hash=sessionStorage.getItem('studio-return')||'#home';sessionStorage.removeItem('studio-return');return;}
  if(a==='use-latest'){clearTimeout(W.saveTimer);localStorage.removeItem(draftKey(S.selected));await refresh();return;}
  if(a==='keep-draft'){const draft=readDraft();draft.parent_id=wversion('script').id;localStorage.setItem(draftKey(S.selected),JSON.stringify(draft));await flushScript();await refresh();return;}
  if(a==='home')return navigate('home');
  if(a==='works')return navigate('projects');
  if(a==='open')return openWorkspace(el.dataset.id,el.dataset.tab||'research');
  if(a==='create')return createIdea($('#creation-idea').value,W.intent);
  if(a==='direct-video')return createIdea($('#creation-idea').value,'video');
  if(a==='cli-run') {
    const prompt=$('#cli-prompt').value.trim();
    if(!prompt)throw new Error('请写下视频制作或修改要求。');
    const base=$('#brief-continue')&&!$('#brief-continue').checked?null:(S.page==='edit'?selectedVersion('edit'):wversion('edit'));
    await api(`/projects/${S.selected}/tasks`,{kind:'cli_video',prompt,base_version_id:base?.id});
    await refresh();toast('Claude 已开始制作，完成后会保存为新版本。');return;
  }
  if(a==='brief-preset'){const input=$('#cli-prompt'),text=BRIEF_PRESETS[Number(el.dataset.index)][1];input.value=input.value.trim()?input.value.trimEnd()+'\n'+text:text;sessionStorage.setItem(`studio-cli-prompt:${S.selected}`,input.value);input.focus();input.setSelectionRange(input.value.length,input.value.length);return;}
  if(a==='to-video'){await flushScript();W.tab='video';history.pushState(null,'',`#workspace/${S.selected}/video`);await refresh();return;}
  if(a==='cli-save'||a==='cli-check') {
    await api('/claude-code',{path:$('#cli-path').value,model:$('#cli-model').value,timeout_sec:Number($('#cli-timeout').value)*60,max_turns:Number($('#cli-turns').value)},'PUT');
    if(a==='cli-check')await api('/claude-code/check',{});
    await refreshSettings();toast(a==='cli-check'?(S.env.claude_cli?.last_check?.message||'环境检测完成'):'Claude Code 设置已保存。');return;
  }
  if(a==='seed')return createIdea(el.dataset.idea);
  if(a==='discover')return createIdea(defaultIdea(),'discover');
  if(a==='quick') {
    W.intent=el.dataset.intent;
    if(W.intent==='discover')return createIdea(defaultIdea(),'discover');
    if(W.intent==='assets')return $('#home-assets').click();
    const input=$('#creation-idea');input.placeholder={research:'想深入了解什么话题？我来找事实、不同观点和创作角度。',reference:'粘贴参考视频链接，我来研究开场、叙事方式与可借鉴的角度。',idea:'一个粗糙的想法也可以，先告诉我你想讲什么。'}[W.intent];input.focus();
    $$('.quick-action').forEach(b=>b.classList.toggle('selected',b===el));return;
  }
  if(a==='tab') {if(readDraft())await flushScript();W.tab=el.dataset.tab;history.pushState(null,'',`#workspace/${S.selected}/${W.tab}`);render();return;}
  if(a==='topic'){W.topic=el.dataset.id;render();return;}
  if(a==='connect'){sessionStorage.setItem('studio-return',`#workspace/${S.selected}/${W.tab}`);return navigate('settings');}
  if(a==='research'){await api(`/projects/${S.selected}/workspace/research`,{idea:wcontext().idea||wproject().name,model:workspaceModel('research'),prompt:S.prompts[`${S.selected}:research`]||'',settings:S.settings[`${S.selected}:research`]||{}});await refresh();return;}
  if(a==='idea') {modal('让这个想法更清楚一点',`<label for="workspace-idea">你想做什么视频？</label><textarea id="workspace-idea" rows="5">${esc(wcontext().idea||wproject().name)}</textarea><p class="small-note">重新研究会保留旧脚本和视频，并标记需要同步的内容。</p>`,vbutton('save-idea','保存并重新研究','primary'));return;}
  if(a==='save-idea'){await api(`/projects/${S.selected}/workspace/research`,{idea:$('#workspace-idea').value});$('#modal').close();W.tab='research';await refresh();return;}
  if(a==='angle') {const note=el.dataset.adjust?$('#angle-note').value.trim():'';if(el.dataset.adjust&&!note)throw new Error('写下你想怎么调整，或直接使用这个方向。');await api(`/projects/${S.selected}/workspace/angle`,{version_id:el.dataset.version,topic_id:el.dataset.topic,angle_id:el.dataset.angle,note,model:workspaceModel('script')});if($('#modal').open)$('#modal').close();W.tab='script';W.chatOpen=false;history.pushState(null,'',`#workspace/${S.selected}/script`);await refresh();return;}
  if(a==='evidence')return evidenceModal(el.dataset.url);
  if(a==='sources'){modal('研究资料',`<div class="source-list">${S.detail.sources.map(s=>`<article><span class="quiet-label">${esc(s.platform||'Web')}</span><h3>${link(s.url,s.title||s.url)}</h3><p>${esc(s.evidence_note||'暂无摘录')}</p><span class="chip">${esc({fetched:'已读取',search_only:'搜索摘录',user_supplied:'用户提供',unverified:'待核实'}[s.verification]||'待核实')}</span></article>`).join('')}</div><div class="source-form"><label for="source-url">补充参考链接</label><input id="source-url" type="url" placeholder="https://…"><input id="source-title" placeholder="这份资料有什么用？">${button('add-source','保存参考','small')}</div>`);return;}
  if(a==='rewrite') {const v=await flushScript()||wversion('script');await api(`/projects/${S.selected}/workspace/rewrite`,{version_id:v.id,paragraph_id:el.dataset.id,instruction:el.dataset.instruction,model:workspaceModel('script')});await refresh();return;}
  if(a==='build-scenes') {const v=await flushScript()||wversion('script');if(!v)throw new Error('先完成一份脚本，再生成分镜。');await api(`/projects/${S.selected}/workspace/scenes`,{version_id:v.id});W.tab='video';W.advancedOpen=true;history.pushState(null,'',`#workspace/${S.selected}/video`);await refresh();return;}
  if(a==='asset-picker')return assetPicker(el.dataset.id);
  if(a==='choose-asset'){await patchScene(el.dataset.scene,{asset_id:el.dataset.id||null});$('#modal').close();return;}
  if(a==='import-asset'){const asset=await api(`/projects/${S.selected}/workspace/import-asset`,{asset_id:el.dataset.id});await patchScene(el.dataset.scene,{asset_id:asset.id});$('#modal').close();return;}
  if(a==='scene-duration'){const scene=wversion('scenes').result.scenes.find(s=>s.id===el.dataset.id);modal('调整镜头时长',`<label for="scene-duration">时长（秒）</label><input id="scene-duration" type="number" min="1" max="120" step="0.1" value="${scene.duration}"><p class="small-note">后续镜头会自动顺延。受保护的音乐素材保持完整。</p>`,vbutton('save-duration','保存','primary',`data-id="${scene.id}"`));return;}
  if(a==='save-duration'){await patchScene(el.dataset.id,{duration:Number($('#scene-duration').value)});$('#modal').close();return;}
  if(a==='upload'){W.uploadScene=el.dataset.scene||null;$('#workspace-assets').click();return;}
  if(a==='first-cut'){await api(`/projects/${S.selected}/workspace/first-cut`,{});await refresh();return;}
  if(a==='video-view'){W.videoView=el.dataset.view;render();return;}
  if(a==='seek'){const player=$('#cut-player');player.currentTime=Number(el.dataset.time);await player.play();return;}
  if(a==='export'){await api(`/projects/${S.selected}/tasks`,{kind:'final',version_id:wversion('edit').id});await refresh();return;}
  if(a==='advanced-edit'){S.versions.edit=wversion('edit')?.id;return navigate('edit');}
  if(a==='history'){modal('每一步，都有迹可循',`<div class="history-list">${S.detail.versions.map(v=>`<article><div><span class="chip">${{research:'研究',script:v.result.angles?'角度':'脚本',scenes:'分镜',edit:'视频'}[v.stage]||v.stage}</span><strong>${esc(v.result.angle||v.result.summary||v.prompt||'已保存版本')}</strong><small>${date(v.created_at)}</small></div>${vbutton('inspect-version','查看','small',`data-id="${v.id}"`)}</article>`).join('')||'<p>第一个版本即将从这里开始。</p>'}</div>`);return;}
  if(a==='inspect-version'){const v=S.detail.versions.find(v=>v.id===el.dataset.id);modal('已保存的版本',`<div class="history-content">${v.result.paragraphs?v.result.paragraphs.map(p=>`<h3>${esc(p.speaker||'旁白')}</h3><p>${esc(p.text)}</p>`).join(''):v.preview?`<video controls style="width:100%;max-height:400px" src="${fileURL((v.final||v.preview).video)}"></video>`:`<pre>${esc(JSON.stringify(v.result,null,2))}</pre>`}</div>`,v.stage==='script'&&v.result.paragraphs?vbutton('restore-script','以这版继续创作','primary',`data-id="${v.id}"`):'');return;}
  if(a==='restore-script'){await api(`/projects/${S.selected}/workspace/restore`,{version_id:el.dataset.id});localStorage.removeItem(draftKey(S.selected));$('#modal').close();W.tab='script';await refresh();return;}
}

async function workspaceUpload(files,home=false) {
  const mediaType=$(`#${home?'home':'project'}-upload-type`)?.value||'';
  if(home)await createIdea(`从这些素材开始：${files.map(f=>f.name).join('、')}`,'assets');
  const id=S.selected,sceneId=W.uploadScene;let last;
  try {
    for(const [index,file] of files.entries()){
      toast(`正在收好素材 ${index+1}/${files.length}：${file.name}`);
      const form=new FormData();form.append('file',file);
      last=await api(`/projects/${id}/assets${mediaType?'?type='+encodeURIComponent(mediaType):''}`,form);
    }
    await refresh(false);
    if(sceneId&&last)await patchScene(sceneId,{asset_id:last.id});
    if($('#modal').open)$('#modal').close();
    if(home){const result=await api(`/projects/${id}/workspace/start-video`,{submit:false});W.tab='video';await refresh();toast(result.needs_cli?'素材已保存。连接本机 Claude Code 后开始制作。':'素材已保存。写下要求，就可以开始制作。');}
    else {await refresh();toast('素材已保存，想怎么用写在要求里即可。');}
  }finally{W.uploadScene=null;}
}

document.addEventListener('input',event=>{
  const el=event.target;
  if(el.id==='inspiration-search'||el.id==='library-search'){
    const id=el.id,value=el.value,position=el.selectionStart;W[id==='inspiration-search'?'inspirationQuery':'libraryQuery']=value;render();const input=$('#'+id);input.focus();input.setSelectionRange(position,position);return;
  }
  if(el.id==='workspace-stage-prompt')S.prompts[`${S.selected}:${el.dataset.stage}`]=el.value;
  if(event.target.id==='creation-idea')sessionStorage.setItem('studio-idea',event.target.value);
  if(event.target.id==='cli-prompt')sessionStorage.setItem(`studio-cli-prompt:${S.selected}`,event.target.value);
  if(el.id==='chat-input')sessionStorage.setItem(`studio-chat:${S.selected}`,el.value);
  if(el.id==='angle-feedback')W.angleFeedback=el.value;
  if(el.id==='inspire-direction')W.inspireDirection=el.value;
  if(el.id==='inspire-feedback')W.inspireFeedback=el.value;
  if(event.target.matches('[data-script-text],[data-script-cue],[data-script-lock]')){
    const draft=captureScript();clearTimeout(W.saveTimer);
    if(draft?.parent_id!==wversion('script')?.id){if($('#draft-status'))$('#draft-status').textContent='编辑已暂存 · 请选择要保留的表达';return;}
    W.saveTimer=setTimeout(()=>flushScript().catch(error=>{if($('#draft-status'))$('#draft-status').textContent='已暂存 · 等待同步';toast(error.message,true);}),900);
  }
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try{
    if(el.id==='public-assets'&&el.files.length){const type=$('#public-upload-type')?.value||'';for(const file of el.files){const form=new FormData();form.append('file',file);await api(`/library/assets${type?'?type='+encodeURIComponent(type):''}`,form);}await refresh();toast('公共素材已保存');}
    if(el.id==='inspiration-kind'){W.inspirationKind=el.value;render();}
    if(el.id==='inspiration-saved'){W.inspirationSaved=el.checked;render();}
    if(el.id==='library-type'){W.libraryType=el.value;render();}
    if(el.dataset.workspaceModel)S.taskModels[`${S.selected}:${el.dataset.workspaceModel}`]=el.value;
    if(el.dataset.scriptSpeaker){captureScript();await flushScript();}
    if(el.id==='home-assets'&&el.files.length)await workspaceUpload([...el.files],true);
    if(el.id==='workspace-assets'&&el.files.length)await workspaceUpload([...el.files]);
    if(el.dataset.rewriteMore&&el.value)await handleV2('v2-rewrite',{dataset:{id:el.dataset.rewriteMore,instruction:el.value}});
    if(el.dataset.cardField){const card=await api(`/projects/${S.selected}/workspace/card`,{card:{[el.dataset.cardField]:el.value}},'PATCH');wproject().workspace={...wcontext(),card};$$(`[data-card-field="${el.dataset.cardField}"]`).forEach(other=>{if(other!==el)other.value=el.value;});}
    if(el.id==='creation-clarify')localStorage.setItem('studio-clarify',el.checked?'1':'0');
  }catch(error){toast(error.message,true);}
});
document.addEventListener('keydown',event=>{if(event.target.id==='chat-input'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('[data-action="v2-chat-send"]:not([disabled])')?.click();return;}if(event.target.id==='project-name'&&event.key==='Enter'){event.preventDefault();$('[data-action="v2-save-project-name"]')?.click();return;}if(event.target.id==='creation-idea'&&(event.ctrlKey||event.metaKey)&&event.key==='Enter'){$('[data-action="v2-direct-video"]').click();event.preventDefault();}});
document.addEventListener('toggle',event=>{if(event.target.matches?.('details[data-advanced]'))W.advancedOpen=event.target.open;},true);
window.addEventListener('beforeunload',event=>{if(readDraft()){event.preventDefault();event.returnValue='';}});

async function pollV2(changed,completed) {
  if(S.page!=='workspace')return false;
  const slot=$('#creation-status');if(slot)slot.innerHTML=workspaceStatus();
  if(changed)W.needsRender=true;
  if(W.needsRender){
    if(completed.some(t=>['first_cut','cli_video'].includes(t.kind)))W.videoView='cut';
    const chatting=document.activeElement?.id==='chat-input';
    if(!readDraft()&&!W.saving&&!$('#modal').open&&(chatting||!document.activeElement?.matches('textarea,input,select'))){
      const player=$('#cut-player');if(!player||player.paused){const caret=chatting?document.activeElement.selectionStart:null;await refresh(false);render();W.needsRender=false;if(chatting){const input=$('#chat-input');input?.focus();input?.setSelectionRange(caret,caret);}}
    }
  }
  return true;
}

(async()=>{
  const parts=location.hash.slice(1).split('/');
  if(parts[0]==='workspace'&&parts[1]){S.selected=parts[1];S.page='workspace';W.tab=workspaceTabs[parts[2]]?parts[2]:'research';}
  else S.page=stageNames[parts[0]]?parts[0]:'home';
  render();await initialize();
  if(['research','script'].includes(S.page)&&S.selected)await openWorkspace(S.selected,S.page);
  if(S.page==='workspace'&&W.tab==='video'&&wversion('edit')?.preview){W.videoView='cut';render();}
  setTimeout(poll,1500);
})();
