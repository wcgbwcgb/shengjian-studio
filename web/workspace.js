'use strict';

// Every action is a prompt sent to Claude Code. Prompt buttons only fill the text box,
// so what you see in the box is exactly what Claude receives.
const draftKey = id => `studio-composer:${id}`;
const kindIcon = {video:'▷',audio:'♫',image:'▧',text:'≡',other:'◇'};
function chips(scope, target) {
  const list=promptsFor(scope);
  return list.length?`<div class="brief-presets prompt-chips" aria-label="常用提示词">${list.map(p=>button('use-prompt',esc(p.label),'rewrite-chip',`data-id="${esc(p.id)}" data-target="${target}" title="${esc(p.description||'填入这段提示词')}"`)).join('')}<a class="chip-edit" href="#prompts" title="修改这些按钮背后的提示词">✎ 编辑</a></div>`:'';
}
function insertPrompt(target, body) {
  const input=$(target), current=input.value.trim();
  input.value=current?body.trimEnd()+'\n'+current:body.trimEnd()+(body.trimEnd().endsWith('：')?'':'\n');
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.focus();input.setSelectionRange(input.value.length,input.value.length);
}

// Name a project after the creator's own words, not the prompt template filled in around them.
function projectName(prompt) {
  let own=prompt;
  for(const p of S.prompts){const body=p.body.trim();if(body&&own.includes(body))own=own.replace(body,'');}
  const first=text=>text.split('\n').map(l=>l.trim()).find(Boolean);
  const line=first(own)||first(prompt)||'新作品';
  return line.length>40?line.slice(0,40)+'…':line;
}

// Home --------------------------------------------------------------------
function renderHome() {
  return `<div class="home-shell"><section class="creation-hero"><div class="hero-eyebrow">YOUR NEXT STORY STARTS HERE</div><h1>今天想做什么视频？</h1><p class="hero-description">直接写给 Claude。想先理清需求、先调研，还是直接出片，都只是不同的一段话。</p>
    <div class="idea-composer"><label class="sr-only" for="creation-idea">要发给 Claude 的内容</label>${chips('home','#creation-idea')}<textarea id="creation-idea" maxlength="20000" placeholder="例如：用 60 秒讲清楚为什么有些歌一听就让人放松，面向不懂乐理的观众">${esc(sessionStorage.getItem('studio-idea')||'')}</textarea>
    <div class="composer-bottom"><span class="small-note">框里的文字会原样发给 Claude，不附加任何规则 · Ctrl+Enter 发送</span><div class="button-row">${button('create-project','发送给 Claude ↗','primary create-button')}</div></div></div>
    <div class="quick-starts">${button('home-assets','＋ 从已有素材开始','quick-action')}${button('go-inspiration','⌕ 没有想法？浏览灵感','quick-action')}</div><input id="home-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></section>
    <section class="continue-section"><div class="section-heading"><h2>继续最近的作品</h2>${button('go-projects','查看全部 →','text-button')}</div>${S.projects.length?S.projects.slice(0,5).map(projectRow).join(''):empty('∿','开始第一条作品','每个作品有自己的工作文件夹，Claude 做出的文件、视频和对话都保存在里面。')}</section></div>`;
}

// Works -------------------------------------------------------------------
function projectRow(p) {
  const t=p.last_task, running=t&&['queued','running','cancelling'].includes(t.status), failed=t&&['failed','interrupted'].includes(t.status);
  const state=running?`<span class="row-state working">${esc(t.phase||'制作中')}</span>`:failed?`<span class="row-state failed" title="${esc(explainError(t.error).title)}">上次未完成 · 可重试</span>`:`<span class="row-state">${t?'上次更新':'还没开始'}</span>`;
  return `<article class="work-row"><div class="work-symbol">∿</div><div class="work-info"><h3 title="${esc(p.name)}">${esc(p.name)}</h3><p>${state} <span>·</span> ${date(p.updated_at)}</p></div><div class="row-actions">${button('rename-project','重命名','text-button small',`data-id="${p.id}"`)}${button('delete-project','删除','text-button small danger-text',`data-id="${p.id}"`)}</div>${button('open-project',failed?'查看并重试 →':'继续创作 →','small ghost',`data-id="${p.id}"`)}</article>`;
}
function renderWorks() {return `<div class="collection-shell">${heading('我的作品','每个作品是一个工作文件夹，加上你和 Claude 的对话。','YOUR STORIES')}<div class="work-list">${S.projects.length?S.projects.map(projectRow).join(''):empty('∿','你的第一条视频，从一句话开始','写下想法发给 Claude，它会在这个作品的工作文件夹里完成。',button('go-home','开始创作','primary'))}</div></div>`;}

// Workspace ---------------------------------------------------------------
const conversationTasks = () => (S.detail?.tasks||[]).filter(t=>['agent','cli_video'].includes(t.kind)).slice().reverse();
// The 调研 and 文案 tabs only display files Claude writes (research.json, directions.json, 脚本.md);
// their buttons are prompts that fill the input box below.
const TABS = {conversation:'对话',research:'调研',script:'文案'};
function renderWorkspace() {
  if(!S.detail)return heading('创作空间','从一个想法开始。')+button('go-home','开始创作','primary');
  const p=S.detail.project;
  return `<div class="studio-shell"><div class="studio-heading"><div><h1 title="${esc(p.name)}">${esc(p.name)}</h1><div class="studio-meta" id="studio-meta">${studioMeta()}</div></div><div class="button-row">${button('rename-project','重命名','ghost small',`data-id="${p.id}"`)}${button('go-publish','↗ 发布记录','ghost small')}</div></div>
    <div class="studio-nav" role="tablist" id="workspace-tabs">${tabsHTML()}</div>
    <div class="agent-layout"><section class="agent-main"><div id="tab-body" aria-live="polite">${tabBody()}</div>${composerHTML()}</section><aside id="agent-side" class="agent-side">${sideHTML()}</aside></div></div>`;
}
function tabsHTML() {
  const ready={research:!!(S.detail.research||S.detail.directions.length),script:S.detail.script!=null};
  return Object.entries(TABS).map(([tab,title])=>button('workspace-tab',`${title}${ready[tab]?' <span class="tab-number">●</span>':''}`,W.tab===tab?'selected':'',`role="tab" aria-selected="${W.tab===tab}" data-tab="${tab}"`)).join('');
}
function tabBody() {
  if(W.tab==='research')return researchHTML();
  if(W.tab==='script')return scriptHTML();
  return `<div id="conversation">${conversationHTML()}</div>`;
}

// 调研 ---------------------------------------------------------------------
function sourceLink(url, index, read) {
  const unread=!read.has(url);
  return `<a class="citation" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(url)}${unread?' · 对话记录里没有 Claude 读取过这个链接，请自己核对':''}">↗ 来源 ${index+1}${unread?' · 未核实':''}</a>`;
}
function researchHTML() {
  const r=S.detail.research, list=S.detail.directions, busy=!!activeTask();
  const read=new Set(S.detail.tasks.flatMap(t=>t.output?.web||[]));
  const tools=chips('research','#composer');
  if(!r&&!list.length)return tools+empty('⌕','还没有调研','点上面的「调研与方向」，把想法接在后面发送。Claude 会把结果写进 research.json，显示在这里。');
  const sources=r?.sources||[], index=url=>{const i=sources.findIndex(s=>s.url===url);return i<0?sources.length:i;};
  const research=r?`<div class="research-overview"><span class="eyebrow">RESEARCH BRIEF</span>${r.title?`<h3 class="research-title">${esc(r.title)}</h3>`:''}${r.summary?`<p class="research-summary">${esc(r.summary)}</p>`:''}
    ${[['what_happened','01 / 发生了什么'],['why_now','02 / 为什么值得讲'],['content_gap','03 / 你可以补上的一块']].some(([k])=>r[k])?`<div class="research-insights">${[['what_happened','01 / 发生了什么'],['why_now','02 / 为什么值得讲'],['content_gap','03 / 你可以补上的一块']].filter(([k])=>r[k]).map(([k,label])=>`<div><span class="insight-label">${label}</span><p>${esc(r[k])}</p></div>`).join('')}</div>`:''}
    ${r.facts.length?`<div class="fact-list">${r.facts.map(f=>`<div><span class="fact-dot"></span><p>${esc(f.claim)}<span class="source-chips">${f.sources.map(u=>sourceLink(u,index(u),read)).join('')}</span></p></div>`).join('')}</div>`:''}
    ${r.viewpoints.length||r.audience_reactions.length?`<details class="quiet-details"><summary>不同观点与观众反应</summary><div class="research-insights"><div><h3>常见观点</h3><p>${r.viewpoints.map(esc).join('<br>')||'暂无'}</p></div><div><h3>观众反应</h3><p>${r.audience_reactions.map(esc).join('<br>')||'暂无'}</p></div></div></details>`:''}
    ${r.to_verify.length?`<details class="quiet-details"><summary>还需要核实的地方</summary><p>${r.to_verify.map(esc).join('<br>')}</p></details>`:''}
    ${sources.length?`<details class="quiet-details"><summary>全部 ${sources.length} 个来源</summary><p>${sources.map(s=>link(s.url,s.title||s.url)+(read.has(s.url)?'':' <span class="chip warn">未核实</span>')).join('<br>')}</p></details>`:''}</div>`:'';
  const directions=list.length?`<section class="angle-section"><div class="section-heading"><div><div class="eyebrow">YOUR CREATIVE DECISION</div><h2>选一个方向，接着写脚本</h2></div><span class="quiet-label">${list.length} 个方向 · 来自 directions.json</span></div><div class="angle-grid">${list.map((d,i)=>`<article class="angle-card"><div class="angle-number">${String(i+1).padStart(2,'0')}</div><h3>${esc(d.title)}</h3>${d.reason?`<p>${esc(d.reason)}</p>`:''}${d.hook?`<blockquote>“${esc(d.hook)}”</blockquote>`:''}${d.audience?`<dl><dt>讲给谁</dt><dd>${esc(d.audience)}</dd></dl>`:''}${button('use-direction','用这个方向写脚本','primary',`data-index="${i}" ${busy?'disabled':''}`)}</article>`).join('')}</div><p class="small-note">点按钮会把「写脚本」提示词和这个方向填进下面的输入框，确认后再发送。</p></section>`:'';
  return tools+research+directions;
}

// 文案 ---------------------------------------------------------------------
function inlineMarkdown(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1 ↗</a>')
    .replace(/(^|[\s(（])(https?:\/\/[^\s<)）]+)/g,'$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}
// A small Markdown subset: headings, lists, quotes, code blocks, paragraphs, bold, links.
function renderMarkdown(source) {
  const out=[];let list=null,para=[],code=null;
  const flush=()=>{if(para.length){out.push(`<p>${para.map(inlineMarkdown).join('<br>')}</p>`);para=[];}if(list){out.push(`<${list.tag}>${list.items.map(i=>`<li>${inlineMarkdown(i)}</li>`).join('')}</${list.tag}>`);list=null;}};
  for(const line of source.replace(/\r\n/g,'\n').split('\n')){
    if(code!==null){if(/^```/.test(line)){out.push(`<pre>${esc(code.join('\n'))}</pre>`);code=null;}else code.push(line);continue;}
    if(/^```/.test(line)){flush();code=[];continue;}
    const heading=line.match(/^(#{1,4})\s+(.*)/), bullet=line.match(/^\s*[-*]\s+(.*)/), number=line.match(/^\s*\d+[.)]\s+(.*)/), quote=line.match(/^>\s?(.*)/);
    if(heading){flush();const level=Math.min(heading[1].length+1,5);out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);}
    else if(bullet||number){if(para.length)flush();const tag=bullet?'ul':'ol';if(list&&list.tag!==tag)flush();list??={tag,items:[]};list.items.push((bullet||number)[1]);}
    else if(quote){flush();out.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);}
    else if(!line.trim())flush();
    else{if(list)flush();para.push(line);}
  }
  if(code!==null)out.push(`<pre>${esc(code.join('\n'))}</pre>`);
  flush();
  return out.join('');
}
function scriptHTML() {
  const text=S.detail.script, busy=!!activeTask(), tools=chips('script','#composer');
  if(text==null)return tools+empty('✎','还没有脚本','在「调研」里选一个方向，或点上面的「写脚本」把方向接在后面发送。Claude 会把脚本写进 脚本.md，显示在这里。');
  if(W.scriptEditing)return tools+`<section class="card script-document"><div class="section-heading"><h2>编辑 脚本.md</h2><span class="quiet-label">保存后，Claude 下次读到的就是这一版</span></div><textarea id="script-editor" class="file-editor" spellcheck="false">${esc(W.scriptDraft??text)}</textarea><div class="button-row end">${button('script-cancel','取消','ghost small')}${button('script-save','保存','primary small',busy?'disabled title="Claude 正在工作，完成后再保存"':'')}</div></section>`;
  return tools+`<section class="card script-document"><div class="section-heading"><div><span class="eyebrow">脚本.md · MAKE IT SOUND LIKE YOU</span></div><div class="button-row">${button('script-edit','✎ 编辑','small',busy?'disabled title="Claude 正在工作"':'')}<a class="small" href="${workURL('脚本.md')}" download="脚本.md">↓ 下载</a></div></div><div class="markdown-body">${renderMarkdown(text)||'<p class="muted">脚本.md 是空的。</p>'}</div><p class="small-note">约 ${Math.round(text.replace(/\s|[#*>\-`]/g,'').length/4.2)} 秒（按每秒 4.2 字粗略估算，含画面说明）</p></section>`;
}
function studioMeta() {return `<span title="${esc(S.detail.work_path||'')}">工作文件夹 · ${S.detail.files.length} 个文件</span><span>${S.detail.versions.length} 个视频版本</span>`;}
function composerOptions() {
  const cli=S.env.claude_cli||{}, hasSession=S.detail.tasks.some(t=>t.cli_session_id);
  const keep=$('#composer-continue')?.checked??true;
  return `${hasSession?`<label class="brief-toggle" title="取消后 Claude 从头开始，不带之前的对话；工作文件夹里的文件仍然都在"><input type="checkbox" id="composer-continue" ${keep?'checked':''}> 接着之前的对话</label>`:''}<span class="small-note">${cli.installed?`Claude Code · ${esc(cli.options?.model||'opus')} · 原样发送，不附加规则`:'先在设置中连接本机 Claude Code'}</span>`;
}
function composerHTML() {
  const cli=S.env.claude_cli||{}, busy=!!activeTask();
  const saved=sessionStorage.getItem(draftKey(S.selected));
  const text=saved??(conversationTasks().length?'':S.detail.project.idea||'');
  return `<section class="card agent-composer"><label for="composer" class="sr-only">发给 Claude 的内容</label>${W.tab==='conversation'?chips('project','#composer'):''}<textarea id="composer" maxlength="20000" rows="5" placeholder="${conversationTasks().length?'继续说：哪里要改、下一步做什么…':'写下要 Claude 做的事；也可以先点上面的按钮填入常用提示词。'}">${esc(text)}</textarea>
    <div class="cli-composer-footer"><div class="composer-options" id="composer-options">${composerOptions()}</div>${button('send',busy?'Claude 正在工作…':'发送 ↗','primary',busy?'disabled':'')}</div>${!cli.installed?button('go-settings','设置本机 Claude Code →','text-button small'):''}</section>`;
}
function turnHTML(t) {
  const out=t.output||{}, running=['queued','running','cancelling'].includes(t.status);
  const version=S.detail.versions.find(v=>v.id===out.version_id);
  const reply=out.reply||(t.kind==='cli_video'&&version?version.result.summary:'');
  const prompt=t.payload?.prompt||'';
  const user=`<div class="turn-user"><span class="quiet-label">你 · ${date(t.created_at)}${t.payload?.fresh?' · 新对话':''}</span>${prompt.length>500?`<details class="turn-long"><summary>${esc(prompt.slice(0,160))}…</summary><div class="turn-text">${esc(prompt)}</div></details>`:`<div class="turn-text">${esc(prompt)}</div>`}</div>`;
  let body='';
  if(running){
    const steps=[...new Set((t.logs||[]).map(l=>l.text))].slice(-5);
    body=`<div class="creative-status working"><span class="status-orbit">∿</span><div><strong>${t.status==='cancelling'?'正在停止…':esc(t.phase||'排队中')}</strong><p>已用时 <span data-since="${esc(t.started_at||t.created_at)}">${elapsedText(t.started_at||t.created_at)}</span> · 可以离开这个页面，完成后会自动显示。</p>${steps.length?`<ol class="task-steps">${steps.map((s,i)=>`<li class="${i===steps.length-1?'current':'done'}">${esc(s)}</li>`).join('')}</ol>`:''}</div>${t.status==='cancelling'?'':button('cancel-task','停止','text-button small',`data-id="${t.id}"`)}</div>`;
  } else if(['failed','interrupted'].includes(t.status)){
    const e=t.status==='interrupted'?{title:'上次处理被中断了',why:'工作台在处理过程中关闭或重启了。',next:'工作文件夹里的文件都还在，点「继续」重新开始这一步。',action:'retry',detail:''}:explainError(t.error);
    body=`<div class="creative-status interrupted" role="alert"><span>!</span><div><strong>${esc(e.title)}</strong><p>${e.why?esc(e.why)+' ':''}${esc(e.next)}</p>${e.detail?`<details class="error-detail"><summary>详细信息</summary><code>${esc(e.detail)}</code></details>`:''}</div><div class="button-row">${e.action==='settings'?button('go-settings','打开设置','small'):''}${t===conversationTasks().at(-1)?button('retry-task',t.status==='interrupted'?'继续':'重试','primary small',`data-id="${t.id}"`):''}</div></div>`;
  } else if(t.status==='cancelled') body=`<p class="small-note">已停止。工作文件夹里已有的文件都还在。</p>`;
  const files=out.files||[];
  const extra=[
    (out.warnings||[]).map(w=>`<div class="inline-notice">${esc(w)}</div>`).join(''),
    reply?`<div class="turn-reply">${esc(reply)}</div>`:'',
    version?`<div class="cut-player turn-video"><video controls preload="metadata" src="${fileURL(version.preview.video)}"></video></div><div class="cut-meta"><span>视频版本 V${versionNumber(version)} · ${Math.round(version.result.duration||0)} 秒 · ${esc(version.result.aspect||'')}</span><a href="${fileURL(version.preview.video)}" download>↓ 下载</a></div>`:'',
    files.length?`<div class="turn-files"><span class="quiet-label">这次写出的文件</span>${files.slice(0,12).map(f=>button('preview-file',`${kindIcon[fileKind(f)]} ${esc(f)}`,'file-chip',`data-path="${esc(f)}"`)).join('')}${files.length>12?`<span class="small-note">还有 ${files.length-12} 个</span>`:''}</div>`:'',
    (out.web||[]).length?`<details class="quiet-details"><summary>查阅了 ${out.web.length} 个网页</summary><p>${out.web.map(u=>link(u)).join('<br>')}</p></details>`:'',
    t.status==='completed'?`<p class="turn-meta">${esc(t.model_config?.model||'')}${t.cli_result?.num_turns?` · ${t.cli_result.num_turns} 轮`:''}${t.elapsed_sec?` · ${Math.max(1,Math.round(t.elapsed_sec/60))} 分钟`:''}</p>`:''
  ].join('');
  return `<article class="turn">${user}<div class="turn-claude"><span class="quiet-label">Claude</span>${body}${extra}</div></article>`;
}
function conversationHTML() {
  const tasks=conversationTasks();
  if(!tasks.length)return `<div class="conversation-empty"><div class="companion-avatar">∿</div><h2>这个作品还没有开始</h2><p>在下面写下要 Claude 做的事。它会在这个作品的工作文件夹里工作：调研、写稿、剪辑、生成视频，都是不同的一段话。</p></div>`;
  return tasks.map(turnHTML).join('');
}
function fileKind(path) {
  const ext=(path.split('.').pop()||'').toLowerCase();
  return ['mp4','mov','m4v','mkv','webm','avi'].includes(ext)?'video':['mp3','wav','m4a','flac','aac','ogg'].includes(ext)?'audio':['png','jpg','jpeg','webp','gif'].includes(ext)?'image':['md','txt','json','csv','srt','vtt','yaml','yml','html','htm','css','js','jsx','ts','tsx','py','xml','svg','log'].includes(ext)?'text':'other';
}
function filesHTML() {
  const files=S.detail.files;
  return `<section class="side-section"><div class="side-head"><h3>工作文件夹 <span class="muted">${files.length}</span></h3></div>${files.length?`<div class="file-list">${files.slice(0,60).map(f=>button('preview-file',`<span>${kindIcon[f.kind]}</span><span class="file-name">${esc(f.path)}</span>`,'file-row',`data-path="${esc(f.path)}" title="${esc(f.path)}"`)).join('')}</div>${files.length>60?`<p class="small-note">还有 ${files.length-60} 个文件</p>`:''}`:'<p class="small-note">Claude 写出的文件会出现在这里。</p>'}${S.detail.work_path?`<details class="quiet-details"><summary>在电脑上找到这个文件夹</summary><p class="small-note">也可以在终端进入这个文件夹，直接运行 claude 继续工作。</p><code class="path-code">${esc(S.detail.work_path)}</code></details>`:''}</section>`;
}
function materialsHTML() {
  const own=S.detail.assets.filter(a=>!a.generated);
  return `<section class="side-section"><div class="side-head"><h3>素材 <span class="muted">${own.length}</span></h3><div class="button-row">${button('upload-material','＋ 上传','text-button small')}${button('public-picker','从素材库','text-button small')}</div></div>${own.length?`<div class="file-list">${own.map(a=>`<a class="file-row" href="${assetURL(a)}" target="_blank" rel="noopener"><span>${mediaType(a)==='Image'?'▧':mediaType(a)==='Video'?'▷':'♫'}</span><span class="file-name">${esc(a.name)}</span></a>`).join('')}</div>`:''}<p class="small-note">素材会按原文件名放进工作文件夹的「素材」目录。想怎么用，直接写在要求里。</p><input id="material-input" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></section>`;
}
function sideHTML() {return filesHTML()+materialsHTML();}
function afterWorkspaceRender() {
  if(W.scrollToEnd&&W.tab==='conversation'){W.scrollToEnd=false;requestAnimationFrame(()=>$('#composer')?.scrollIntoView({block:'end'}));}
}
function updateWorkspace(latest) {
  // Refresh the shown tab and side panel only; the text being typed stays untouched.
  const signature=d=>JSON.stringify([d?.tasks.map(t=>[t.id,t.status,t.phase]),d?.files.map(f=>[f.path,f.mtime])]);
  const before=signature(S.detail), wasBusy=!!activeTask();
  S.detail=latest;
  if(before===signature(latest)||$('#modal').open)return;
  const playing=$$('#tab-body video').some(v=>!v.paused);
  if(!playing&&!W.scriptEditing&&$('#tab-body'))$('#tab-body').innerHTML=tabBody();
  if($('#workspace-tabs'))$('#workspace-tabs').innerHTML=tabsHTML();
  if($('#agent-side'))$('#agent-side').innerHTML=sideHTML();
  if(wasBusy!==!!activeTask()){const send=$('[data-action="send"]');if(send){send.disabled=!!activeTask();send.textContent=activeTask()?'Claude 正在工作…':'发送 ↗';}}
  if($('#composer-options'))$('#composer-options').innerHTML=composerOptions();
  if($('#studio-meta'))$('#studio-meta').innerHTML=studioMeta();
  if(wasBusy&&!activeTask())refresh(false).catch(()=>{});
}

async function previewFile(path) {
  const kind=fileKind(path), url=workURL(path), editable=/\.(md|txt|json|csv|srt|vtt|ya?ml)$/i.test(path);
  const open=`<a href="${url}" target="_blank" rel="noopener">在新标签页打开 ↗</a>`;
  if(kind==='video')return modal(esc(path),`<video controls style="width:100%;max-height:520px" src="${url}"></video>`,open);
  if(kind==='audio')return modal(esc(path),`<audio controls style="width:100%" src="${url}"></audio>`,open);
  if(kind==='image')return modal(esc(path),`<img src="${url}" alt="${esc(path)}" style="max-width:100%;max-height:70vh">`,open);
  if(/\.(html?)$/i.test(path))return modal(esc(path),`<iframe class="file-frame" sandbox="allow-scripts" src="${url}" title="${esc(path)}"></iframe>`,open);
  if(kind!=='text')return modal(esc(path),'<p>这种文件不能在这里预览。</p>',`<a href="${url}" download>↓ 下载</a>`);
  const response=await fetch(url);
  if(!response.ok)throw new Error('文件读取失败，它可能已经被删除。');
  const text=await response.text();
  if(!editable)return modal(esc(path),`<pre class="file-text">${esc(text)}</pre>`,open);
  modal(esc(path),`<p class="small-note">可以直接修改。保存后，Claude 下次读到的就是你改过的内容。</p><textarea id="file-editor" class="file-editor" data-path="${esc(path)}" spellcheck="false">${esc(text)}</textarea>`,open+button('close-modal','关闭','ghost')+button('save-file','保存修改','primary',activeTask()?'disabled title="Claude 正在工作，完成后再修改"':''));
}

async function uploadFiles(files, projectId) {
  for(const [index,file] of files.entries()){
    toast(`正在收好素材 ${index+1}/${files.length}：${file.name}`);
    const form=new FormData();form.append('file',file);
    await api(`/projects/${projectId}/assets`,form);
  }
}

Object.assign(HANDLERS, {
  'go-home':()=>navigate('home'),
  'go-inspiration':()=>navigate('inspiration'),
  'go-settings':()=>navigate('settings'),
  'go-publish':()=>navigate('publish'),
  'use-prompt':el=>{const p=S.prompts.find(p=>p.id===el.dataset.id);if(!p)throw new Error('这个提示词已经被删除，可以在「提示词」页面重新添加。');insertPrompt(el.dataset.target,p.body);},
  'use-direction':el=>{
    const d=S.detail.directions[Number(el.dataset.index)], template=S.prompts.find(p=>p.id==='script');
    const text=[d.title,d.hook&&`开场：${d.hook}`,d.audience&&`讲给：${d.audience}`].filter(Boolean).join('\n');
    const input=$('#composer');input.value='';
    insertPrompt('#composer',(template?template.body.trimEnd():'按这个方向写一条完整的短视频脚本，写进 脚本.md：')+'\n'+text);
  },
  'create-project':async()=>{
    const prompt=$('#creation-idea').value.trim();
    if(!prompt){$('#creation-idea').focus();throw new Error('写下一句话，就能开始。');}
    const result=await api('/projects',{prompt,name:projectName(prompt)});
    sessionStorage.removeItem('studio-idea');
    await openProject(result.project.id);
    if(result.needs_cli)toast('想法已保存到这个作品。请在设置中连接本机 Claude Code，再点「发送」。',true);
  },
  'home-assets':()=>$('#home-assets').click(),
  'send':async()=>{
    const input=$('#composer'), prompt=input.value.trim();
    if(!prompt){input.focus();throw new Error('先写下要 Claude 做的事。');}
    const fresh=$('#composer-continue')?!$('#composer-continue').checked:false;
    await api(`/projects/${S.selected}/run`,{prompt,fresh});
    sessionStorage.removeItem(draftKey(S.selected));
    // Follow the run in the conversation; the tabs update when Claude finishes.
    W.tab='conversation';W.scriptEditing=false;history.replaceState(null,'',`#workspace/${S.selected}/conversation`);
    W.scrollToEnd=true;await refresh();
  },
  'preview-file':el=>{
    // Files a tab displays open in that tab rather than as raw text.
    const tab={'research.json':'research','directions.json':'research','脚本.md':'script'}[el.dataset.path];
    return tab?showTab(tab):previewFile(el.dataset.path);
  },
  'workspace-tab':el=>showTab(el.dataset.tab),
  'script-edit':()=>{W.scriptEditing=true;W.scriptDraft=null;$('#tab-body').innerHTML=tabBody();$('#script-editor')?.focus();},
  'script-cancel':()=>{W.scriptEditing=false;W.scriptDraft=null;$('#tab-body').innerHTML=tabBody();},
  'script-save':async()=>{
    const text=$('#script-editor').value;
    await api(`/projects/${S.selected}/work/${encodeURIComponent('脚本.md')}`,{text},'PUT');
    W.scriptEditing=false;W.scriptDraft=null;S.detail.script=text;$('#tab-body').innerHTML=tabBody();
    toast('脚本已保存。下次发给 Claude 时，它会读到这一版。');
  },
  'save-file':async()=>{const editor=$('#file-editor');await api(`/projects/${S.selected}/work/${editor.dataset.path.split('/').map(encodeURIComponent).join('/')}`,{text:editor.value},'PUT');$('#modal').close();toast('已保存。下次发给 Claude 时，它会读到修改后的内容。');},
  'upload-material':()=>$('#material-input').click(),
  'public-picker':()=>{modal('从素材库添加',`<div class="picker-list">${S.library.assets.filter(a=>a.scope==='public').map(a=>`<div class="picker-asset"><div><strong>${esc(a.name)}</strong><small>${mediaType(a)}</small></div>${button('add-public-asset','添加到作品','small',`data-id="${a.id}"`)}</div>`).join('')||'<p>公共素材库还没有素材，请先到「素材库」上传。</p>'}</div>`);},
  'add-public-asset':async el=>{await api(`/projects/${S.selected}/import-asset`,{asset_id:el.dataset.id});$('#modal').close();await refresh();toast('已添加。下次发给 Claude 时会放进「素材」目录。');},
  'rename-project':el=>{const p=S.projects.find(p=>p.id===el.dataset.id)||S.detail?.project;modal('重命名作品',`<label for="project-name">作品名称</label><input id="project-name" maxlength="120" value="${esc(p.name)}">`,button('close-modal','取消','ghost')+button('save-project-name','保存','primary',`data-id="${p.id}"`));$('#project-name').select();},
  'save-project-name':async el=>{const name=$('#project-name').value.trim();if(!name)throw new Error('请填写作品名称。');await api(`/projects/${el.dataset.id}`,{name},'PATCH');$('#modal').close();await refresh();toast('已重命名。');},
  'delete-project':el=>{const p=S.projects.find(p=>p.id===el.dataset.id);if(!p)throw new Error('这个作品已经不存在，请刷新页面。');modal('删除这个作品？',`<p>将永久删除「${esc(p.name)}」的工作文件夹、对话、项目素材和所有视频版本。此操作无法撤销。</p><p class="small-note">公共素材库中的素材不受影响。</p>`,button('close-modal','取消','ghost')+button('delete-project-confirm','永久删除','danger',`data-id="${p.id}"`));},
  'delete-project-confirm':async el=>{
    const id=el.dataset.id;await api(`/projects/${id}`,{},'DELETE');$('#modal').close();sessionStorage.removeItem(draftKey(id));
    if(S.selected===id){S.selected=null;S.detail=null;localStorage.removeItem('studio-project');if(S.page==='workspace'){S.page='projects';history.replaceState(null,'','#projects');}}
    await refresh();toast('作品已删除。');
  },
});

// Films -------------------------------------------------------------------
function renderFilms() {
  const films=S.library.films;
  return `<div class="collection-shell">${heading('做出来的每一版，都值得留下。','Claude 做出的视频都保存在这里，随时回到作品继续打磨。','THE SCREENING ROOM')}<div class="library-grid">${films.map(v=>`<article class="library-card"><div class="film-thumbnail"><video controls preload="metadata" src="${fileURL((v.final||v.preview).video,v.project_id)}"></video></div><h3>${esc(v.project_name)}</h3><p>${date(v.created_at)}</p>${button('open-project','回到这个作品 →','text-button',`data-id="${v.project_id}"`)}<a href="${fileURL((v.final||v.preview).video,v.project_id)}" download>下载视频 ↓</a></article>`).join('')}</div>${!films.length?empty('▷','下一条，就是你的第一条。','在作品里让 Claude 做出视频，就会出现在这里。',button('go-projects','继续创作','primary')):''}</div>`;
}

document.addEventListener('input',event=>{
  const el=event.target;
  if(el.id==='creation-idea')sessionStorage.setItem('studio-idea',el.value);
  if(el.id==='composer')sessionStorage.setItem(draftKey(S.selected),el.value);
  if(el.id==='script-editor')W.scriptDraft=el.value;
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try{
    if(el.id==='home-assets'&&el.files.length){
      const files=[...el.files], result=await api('/projects',{name:'从素材开始：'+files.map(f=>f.name).join('、').slice(0,60)});
      await uploadFiles(files,result.project.id);await openProject(result.project.id);toast('素材已保存。写下要求，或点上面的按钮填入常用提示词。');
    }
    if(el.id==='material-input'&&el.files.length){await uploadFiles([...el.files],S.selected);await refresh();toast('素材已保存，想怎么用写在要求里即可。');}
  }catch(error){toast(error.message,true);}
});
document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){
    if(event.target.id==='creation-idea'){event.preventDefault();$('[data-action="create-project"]')?.click();}
    if(event.target.id==='composer'){event.preventDefault();$('[data-action="send"]:not([disabled])')?.click();}
  }
  if(event.target.id==='project-name'&&event.key==='Enter'){event.preventDefault();$('[data-action="save-project-name"]')?.click();}
});
