'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stageNames = {home:'开始创作',inspiration:'灵感发现',workspace:'创作工作室',library:'素材库',films:'成片',projects:'我的作品',research:'研究',script:'脚本',edit:'精细剪辑',publish:'发布记录',settings:'设置'};
const S = {page: 'home', projects: [], selected: localStorage.getItem('studio-project'), detail: null, env: {}, defaults: {}, templates: [], versions: {}, prompts: {}, settings: {}, taskModels: {}, models: [], modelCatalog: [], inspirations: {}, connection: {}, loading: true, library:{assets:[],films:[]}};
let toastTimer;
function toast(text, error=false) { $('#toast').textContent=text; $('#toast').className='show'+(error?' error':''); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').className='',5000); }
async function api(path, body, method='POST') {
  const opts = body === undefined ? {} : body instanceof FormData ? {method,body} : {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  let response;
  try { response = await fetch('/api'+path, opts); } catch { throw new Error('无法连接本地服务，请运行启动脚本。'); }
  let data;
  try { data = await response.json(); } catch { throw new Error('后端未运行或返回了无效响应，请检查启动窗口。'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : validationMessage(data.detail));
  return data;
}
function validationMessage(detail) {
  // FastAPI validation errors are arrays; show which field needs attention instead of raw JSON.
  const first=Array.isArray(detail)?detail[0]:null, field=first?.loc?.[first.loc.length-1];
  const names={idea:'想法',name:'名称',prompt:'要求',instruction:'修改要求',duration:'时长'};
  if(!first)return '请求没有被接受，请检查输入后重试。';
  if(/at most|too_long/.test(first.type||first.msg))return `${names[field]||'输入'}太长了，请精简后再试。`;
  if(/at least|too_short|missing/.test(first.type||first.msg))return `请先填写${names[field]||'必填内容'}。`;
  return `${names[field]||'输入'}格式不正确，请检查后重试。`;
}
// Turns a backend/CLI error into what happened, why, and what the user can do next.
function explainError(text='', kind='') {
  const t=String(text), work={research:'研究',script:'写稿',first_cut:'初剪',cli_video:'视频制作',final:'高清导出',preview:'预览'}[kind]||'这一步';
  const rules=[
    [/登录|auth login|订阅账号|loggedIn/i,'Claude 还没有登录','这台电脑上的 Claude Code 没有检测到订阅登录。','打开设置，按提示登录后点「检测」，再回来重试。','settings'],
    [/未找到 Claude Code|claude\.exe|CLI 路径|Claude Code 路径/i,'没有找到 Claude Code','需要先在这台电脑上安装 Claude Code。','打开设置，填写 Claude Code 位置或安装后重新检测。','settings'],
    [/模型未配置|API 密钥|API Key|ANTHROPIC_API_KEY/i,'AI 服务还没有连接','当前选择了 API 方式，但还没有填写密钥。','打开设置，连接 Claude 订阅或添加 API 服务。','settings'],
    [/FFmpeg|ffprobe|视频处理组件|视频工具/i,'缺少视频处理工具','生成视频需要本机的 FFmpeg。','打开设置，在「本地工具」中填写 FFmpeg 路径。','settings'],
    [/超时|timeout|timed out/i,`${work}花的时间太久，已停止`,'可能是网络较慢，或要求的范围太大。','重试一次；如果仍然超时，把想法写得更具体一些。','retry'],
    [/overloaded|529|rate.?limit|429|额度|usage limit/i,'Claude 暂时繁忙或额度已用完','服务端临时限流，或订阅额度达到上限。','稍等几分钟后重试。','retry'],
    [/无法连接|network|ECONN|连接模型服务/i,'网络连接失败','这台电脑暂时连不上 AI 服务。','检查网络后重试。','retry'],
    [/结构|JSON|格式错误|缺少有效|未返回有效/i,`${work}结果没有成功整理出来`,'AI 返回的内容不完整，这通常是偶发情况。','直接重试；已有内容不会丢失。','retry'],
    [/预算/,'已达到本月 API 预算','为避免超支，新的 API 任务已暂停。','在设置中调高预算，或改用 Claude 订阅。','settings'],
    [/turn limit|max_turns|执行轮数/i,`${work}步骤太多，没能完成`,'任务超过了允许的执行步数。','把要求拆小一点再试，或在设置中提高「最多执行轮数」。','retry'],
    [/素材|asset/i,'素材有问题','有素材无法读取或格式不受支持。','检查素材后重试。','retry'],
  ];
  const hit=rules.find(([re])=>re.test(t));
  if(hit)return {title:hit[1],why:hit[2],next:hit[3],action:hit[4],detail:t};
  return {title:`${work}没有完成`,why:t?'':'原因未知。',next:'已有内容都还在，可以直接重试。',action:'retry',detail:t};
}
const elapsedText = since => {const s=Math.max(0,Math.round((Date.now()-new Date(since))/1000));return s<60?`${s} 秒`:`${Math.floor(s/60)} 分 ${String(s%60).padStart(2,'0')} 秒`;};
setInterval(()=>$$('[data-since]').forEach(el=>{el.textContent=elapsedText(el.dataset.since);}),1000);
const date = value => value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '未提供';
const fileURL = path => `/api/projects/${S.selected}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
const link = (url,title) => /^https?:\/\//i.test(url||'') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title||url)} ↗</a>` : esc(title||url);
const button = (action, label, cls='', attrs='') => `<button data-action="${action}" class="${cls}" ${attrs}>${label}</button>`;
const empty = (icon, title, description, action='') => `<div class="empty"><div class="empty-icon">${icon}</div><h3>${title}</h3><p>${description}</p>${action}</div>`;
function selectedVersion(stage) {
  const versions = S.detail?.versions.filter(v=>v.stage===stage) || [];
  return versions.find(v=>v.id===S.versions[stage]) || versions.find(v=>v.id===S.detail.project.adopted[stage]) || versions[0];
}
function capture() {
  const prompt=$('#workspace-stage-prompt');
  if(prompt)S.prompts[`${S.selected}:${prompt.dataset.stage}`]=prompt.value;
}
async function refresh(renderPage=true) {
  const ident=S.selected;
  const [projects,env,settings,templates,library,inspirations] = await Promise.all([api('/projects'),api('/environment'),api('/settings'),api('/templates'),api('/library'),api('/inspirations')]);
  Object.assign(S,{projects,env,defaults:settings.defaults,usage:settings.usage,models:settings.models||[],modelCatalog:settings.model_catalog||settings.models||[],inspirations,connection:settings.connection||{},textService:settings.text_service||{engine:'api',models:{research:'sonnet',script:'sonnet'}},templates,library,loading:false});
  if(ident!==S.selected)return;
  if(!S.projects.some(p=>p.id===ident)) S.selected=S.projects[0]?.id||null;
  if(S.selected){const selected=S.selected,detail=await api(`/projects/${selected}`);if(selected!==S.selected)return;S.detail=detail;}else S.detail=null;
  if(renderPage) render();
}
function heading(title, description, project=false, eyebrow='YOUR IDEA, YOUR STORY') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="muted">${description}</p></div>${project?`<select class="project-select" id="project-select" aria-label="切换项目">${S.projects.map(p=>`<option value="${p.id}" ${p.id===S.selected?'selected':''}>${esc(p.name)}</option>`).join('')}</select>`:''}</div>`;
}
function render() {
  document.title=`${stageNames[S.page]} · 声间`;
  $('#crumb').textContent=stageNames[S.page];
  $$('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===S.page));
  $('#env-dot').style.background=S.env.ffmpeg&&(S.env.api_configured||S.env.claude_cli?.last_check?.logged_in)?'#a4ce89':'#d7ad6f';
  if(S.loading){$('#content').innerHTML=heading('准备创作空间','正在读取本地项目…');return;}
  if(typeof renderV2==='function' && renderV2())return;
  if(S.page==='settings') $('#content').innerHTML=renderSettings();
  else if(!S.detail) $('#content').innerHTML=heading(stageNames[S.page],'每个好故事，都从一个项目开始。')+empty('＋','还没有创作项目','建立项目后，调研、稿件、素材和版本都会保存在同一个本地空间。',button('new','新建第一个作品','primary'));
  else {
    // Pin the displayed version. A task completion must not retarget an unsaved editor.
    if(['research','script','edit'].includes(S.page)) {
      const shown=selectedVersion(S.page);if(shown)S.versions[S.page]=shown.id;
    }
    $('#content').innerHTML=renderStage();
  }
}
function renderStage() {
  const descriptions={edit:'检查素材、调整时间线并保存新版本。',publish:'记录作品链接与真实发布数据。'};
  return (S.selected?vbutton('open','← 回到创作空间','text-button',`data-id="${S.selected}" data-tab="video"`):'')+heading(stageNames[S.page],descriptions[S.page],true,'STUDIO')+
    (S.detail.project.stale_stages.includes(S.page)?'<div class="warning-box">项目已有变化，当前成果待更新。旧版本保留。</div>':'')+
    `<div class="workspace-grid"><div class="result-stack">${taskPanel()}${S.page==='publish'?renderPublish():renderEdit()}</div>${S.page==='publish'?publishingAside():cliComposer(selectedVersion('edit'))}</div>`;
}
function taskPanel() {
  // Completed tasks are internal history; only show work in progress or needing a retry.
  const tasks=S.detail.tasks.slice(0,3).filter(t=>t.status!=='completed').slice(0,1);
  if(!tasks.length)return '<div id="task-slot"></div>';
  return `<div id="task-slot">${tasks.map(t=>`<div class="task-panel" style="margin-bottom:10px"><div class="task-row"><span>${['queued','running','cancelling'].includes(t.status)?'<span class="running"></span>':''}${esc(t.phase)} <span class="inline-label">${t.model_config?.model?esc(modelName(t.model_config.model))+' · ':''}${t.elapsed_sec?`${t.elapsed_sec}s`:''}</span></span>${['queued','running'].includes(t.status)?button('cancel-task','取消','small',`data-id="${t.id}"`):['failed','interrupted','cancelled'].includes(t.status)?button('retry-task',t.status==='interrupted'?'恢复':'重试','small',`data-id="${t.id}"`):'<span class="chip green">已保存</span>'}</div>${t.error?`<p class="error-text">${esc(explainError(t.error,t.kind).title)} · ${esc(explainError(t.error,t.kind).next)}</p>`:''}${t.logs.length?`<details><summary>查看处理阶段</summary>${t.logs.map(l=>`<div>${date(l.at)} · ${esc(l.text)}</div>`).join('')}</details>`:''}</div>`).join('')}</div>`;
}
function modelName(id) {return id==='local'?'本地处理':(S.modelCatalog||S.models).find(m=>m.id===id)?.name||id;}
function modelOptions(chosen) {return S.models.map(m=>`<option value="${m.id}" ${m.id===chosen?'selected':''}>${esc(m.name)} · ${esc(m.tag)}</option>`).join('');}
function currentModel(stage=S.page) {return S.taskModels[`${S.selected}:${stage}`]||S.defaults['model_'+stage];}
function versionBar(stage) {
  const versions=S.detail.versions.filter(v=>v.stage===stage), chosen=selectedVersion(stage);
  if(!versions.length)return '';
  return `<div class="version-bar"><select id="version-select" data-stage="${stage}" aria-label="选择版本">${versions.map((v,i)=>`<option value="${v.id}" ${v.id===chosen?.id?'selected':''}>V${versions.length-i} · ${date(v.created_at)} ${S.detail.project.adopted[stage]===v.id?'· 已采用':''}${v.stale?' · 基于旧输入':''}</option>`).join('')}</select>${button('compare','比较','small',`data-stage="${stage}"`)}${stage!=='research'?button('adopt','采用 / 恢复','small',`data-stage="${stage}"`):''}</div>`;
}
function renderEdit() {
  const v=selectedVersion('edit'),r=v?.result;
  if(r?.engine==='claude_cli')return renderCliEdit(v);
  return `<section class="card"><div class="card-head"><h2>视频与时间线</h2></div>${versionBar('edit')}${v?.preview?.video?`<video id="preview-player" controls preload="metadata" style="width:100%;max-height:520px" src="${fileURL(v.preview.video)}"></video>`:'<p class="muted">先分析素材，再生成本地预览。</p>'}${r?`<p id="preview-time">成片 ${r.duration.toFixed(1)} 秒</p><details class="details"><summary>编辑时间线 JSON</summary><p>片段起止时间使用原素材秒数。保存时校验范围与 Music 保护。</p><textarea id="timeline-json" class="json-editor">${esc(JSON.stringify({...r,captions:[]},null,2))}</textarea>${button('save-timeline','保存为新版本','primary small')}</details><div class="button-row">${button('render-preview','渲染预览','small')}${button('export-final','导出高清','primary small')}${button('publish-page','发布记录','small')}</div>${artifactLinks(v.final)}`:''}</section>${projectMaterials()}<div class="button-row">${button('analyze','分析素材','small')}${button('local-edit','完整保留，生成本地预览','small')}</div><input id="workspace-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden>`;
}
function versionNumber(v) {const list=S.detail.versions.filter(x=>x.stage===v.stage);return list.length-list.findIndex(x=>x.id===v.id);}
function artifactLinks(artifacts) {if(!artifacts)return '';return `<div class="file-links">${Object.entries({bundle:'完整发布包 ZIP',video:'成片 MP4',subtitles:'字幕 SRT',cover:'封面 PNG',publishing:'发布文案',manifest:'版本说明'}).filter(([key])=>artifacts[key]).map(([key,name])=>`<a href="${fileURL(artifacts[key])}" download>↓ ${name}</a>`).join('')}</div>`;}
function renderPublish() {
  const finals=S.detail.versions.filter(v=>v.stage==='edit'&&v.final);
  return `<section class="card"><div class="card-head"><h2>发布到你的平台</h2><span class="chip">手动发布</span></div>${finals.length?`<div class="field"><label>已导出成片</label><select id="publication-version">${finals.map(v=>`<option value="${v.id}">V${versionNumber(v)} · ${date(v.created_at)}</option>`).join('')}</select></div><div class="publishing-grid"><div class="field"><label>发布平台</label><select id="publication-platform"><option>抖音</option><option>B站</option><option>小红书</option><option>视频号</option></select></div><div class="field"><label>发布时间</label><input id="publication-date" type="datetime-local"></div></div><div class="field"><label>作品链接</label><input id="publication-url" placeholder="https://…"></div><div class="publishing-grid">${['views','likes','comments','completion_rate'].map((k,i)=>`<div class="field"><label>${['播放量','点赞','评论','完播率（%）'][i]}</label><input data-metric="${k}" type="number" min="0" placeholder="未获取时留空"></div>`).join('')}</div><div class="button-row end">${button('save-publication','保存发布记录','primary small')}</div>`:empty('↗','等待第一份发布包','在素材与成片页面采用满意的版本，导出高清成片后就可以记录发布。',button('edit-page','前往素材与成片','primary small'))}</section><section class="card"><div class="card-head"><h2>发布与数据快照</h2><a class="muted" href="/api/projects/${S.selected}/export/records">↓ 导出记录</a></div>${S.detail.publications.length?S.detail.publications.map(p=>`<article class="publication"><div class="card-head"><h3>${esc(p.platform)} · V${versionNumber(S.detail.versions.find(v=>v.id===p.version_id))}</h3>${button('update-snapshot','添加快照','small',`data-id="${p.id}"`)}</div>${link(p.url,'查看发布作品')}<p class="section-note">发布于 ${date(p.published_at)}</p><div class="snapshots">${p.snapshots.map(s=>`${date(s.at)} · ${Object.entries(s.metrics).map(([k,x])=>`${esc({views:'播放',likes:'点赞',comments:'评论',completion_rate:'完播率'}[k]||k)} ${x??'未获取'}`).join(' / ')}`).join('<br>')}</div></article>`).join(''):empty('▤','发布记录会保存在这里','发布后 24 小时和 7 天各添加一份快照。不同平台分别记录，缺失数据保持为空。')}</section>`;
}
function publishingAside() {
  const v=S.detail.versions.find(v=>v.id===S.detail.project.adopted.edit);
  return `<aside class="card assistant" style="padding:22px"><div class="eyebrow">BEFORE YOU PUBLISH</div><h2>最后，再听一遍。</h2><ul class="text-list"><li>音乐示例是否完整？</li><li>试听比较的响度是否一致？</li><li>来源和封面是否准确？</li><li>重要人物与动作是否都在画面里？</li></ul><div class="section-divider"></div><h3>当前采用版本</h3><p class="muted">${v?'V'+versionNumber(v):'尚未采用成片'}</p>${artifactLinks(v?.final)}<div class="section-divider"></div><p class="cost-note">工作台不代发视频。请在平台完成发布，再保存链接和真实数据。</p><p class="cost-note">首月样本主要用来发现制作阻力；单个累计数字不能说明正在增长。</p></aside>`;
}
function modal(title, body, footer='') {
  $('#modal-content').innerHTML=`<div class="dialog-head"><h2>${title}</h2>${button('close-modal','×','ghost')}</div>${body}${footer?`<div class="dialog-actions">${footer}</div>`:''}`;
  if(!$('#modal').open)$('#modal').showModal();
}
async function navigate(page) {
  return navigateV2(page);
}
async function runTask(kind, extra={}) {
  const task=await api(`/projects/${S.selected}/tasks`,{kind,...extra});
  await refresh();toast('已开始处理，完成后会保存为新版本。');return task;
}
function previewAsset(ident) {
  const a=S.detail.assets.find(x=>x.id===ident),analysis=a.analysis;
  modal(esc(a.name),`<p>${mediaType(a)} · ${analysis.duration.toFixed(2)} 秒${mediaType(a)==='Music'?' · 完整保护':''}</p>${analysis.has_video?`<video controls style="width:100%;max-height:300px" src="${assetURL(a)}"></video>`:`<audio controls src="${assetURL(a)}"></audio>`}<div class="frames">${(analysis.frames||[]).map((f,i)=>`<img src="/api/assets/${a.id}/frames/${i}" alt="关键帧">`).join('')}</div><div class="field"><label>素材来源与用途</label><input id="asset-provenance" value="${esc(a.provenance)}"></div><div class="field"><label>原素材中的音乐保护区 JSON</label><textarea id="asset-protected" class="json-editor" ${mediaType(a)==='Music'?'readonly':''}>${esc(JSON.stringify(a.protected||[],null,2))}</textarea></div>`,button('save-asset','保存素材信息','primary',`data-id="${a.id}"`));
}
function compare(stage) {
  const versions=S.detail.versions.filter(v=>v.stage===stage), v=selectedVersion(stage);
  if(versions.length<2)throw new Error('保存至少两个版本后可以比较。');
  const other=versions.find(x=>x.id!==v.id);
  const opts=chosen=>versions.map(x=>`<option value="${x.id}" ${x.id===chosen?'selected':''}>V${versionNumber(x)} · ${date(x.created_at)}</option>`).join('');
  modal('比较版本',`<div class="compare-grid"><div><label>版本 A</label><select id="compare-a">${opts(other.id)}</select><pre id="compare-text-a"></pre></div><div><label>版本 B</label><select id="compare-b">${opts(v.id)}</select><pre id="compare-text-b"></pre></div></div><p class="section-note">比较段落、时间线与生效要求；采用或恢复后，后续修改以该版本为基准。</p>`,button('close-modal','关闭','ghost'));
  updateComparison();
}
function updateComparison() {
  for(const key of ['a','b']) {
    const v=S.detail.versions.find(x=>x.id===$(`#compare-${key}`).value);
    const readable=v.result.paragraphs?v.result.paragraphs.map(p=>`[${p.speaker}] ${p.text}\n画面：${p.cue}${p.locked?' · 已锁定':''}`).join('\n\n'):JSON.stringify(v.result,null,2);
    $(`#compare-text-${key}`).textContent=`要求：${v.prompt}\n\n${readable}\n\n生效设置：\n${JSON.stringify(v.effective,null,2)}`;
  }
}
async function handle(action, el) {
  if(action==='save-text-service') {
    await api('/settings/text-service',{engine:$('#text-engine').value,models:Object.fromEntries($$('[data-text-model]').map(el=>[el.dataset.textModel,el.value]))},'PUT');
    await refreshSettings();toast('调研与文案服务已保存，之后的新任务使用所选服务。');return;
  }
  if(action==='connection-add'||action==='connection-edit') {
    const p=(S.connection.connections||[]).find(p=>p.id===el.dataset.id)||{};
    modal(p.id?'编辑 AI 服务':'添加 AI 服务',serviceEditor(p),button('close-modal','取消','ghost')+button('connection-save','保存服务','primary',`data-id="${p.id||''}"`));return;
  }
  if(action==='connection-save') {
    const pricing={};$$('[data-profile-price]').forEach(x=>{(pricing[x.dataset.model]??={})[x.dataset.profilePrice]=Number(x.value);});
    const body={name:$('#profile-name').value,protocol:$('#profile-protocol').value,base_url:$('#profile-url').value,api_key:$('#profile-key').value,
      model_defaults:Object.fromEntries($$('[data-profile-model]').map(x=>[x.dataset.profileModel,x.value])),pricing};
    if(el.dataset.id)body.id=el.dataset.id;
    await api('/connections',body);$('#profile-key').value='';$('#modal').close();await refreshSettings();toast('服务、模型和计费配置已保存。');return;
  }
  if(action==='connection-probe') {const model=S.connection.model_defaults?.script||S.defaults.model_script;$('#connection-test-result').textContent='正在测试当前服务…';try{await api('/connection/test',{model});await refreshSettings();$('#connection-test-result').textContent='连接测试成功';}catch(error){$('#connection-test-result').textContent=error.message;throw error;}return;}
  if(action==='connection-activate') {await api(`/connections/${el.dataset.id}/activate`,{});await refreshSettings();toast('已切换服务，之后的任务使用对应模型与单价。');return;}
  if(action==='connection-delete') {
    const p=S.connection.connections.find(p=>p.id===el.dataset.id);
    modal('删除 AI 连接',`<p>删除「${esc(p.name)}」及其保存的密钥？${p.active?'之后的新 AI 任务需要重新配置连接。已提交的任务保留原配置。':''}</p>`,button('close-modal','取消','ghost')+button('connection-delete-confirm','删除连接','danger',`data-id="${p.id}"`));return;
  }
  if(action==='connection-delete-confirm') {await api(`/connections/${el.dataset.id}`,{},'DELETE');$('#modal').close();await refreshSettings();toast('连接与密钥已删除。');return;}
  if(action.startsWith('v2-'))return handleV2(action,el);
  if(action==='new')return navigate('home');
  if(action==='close-modal')return $('#modal').close();
  if(action==='settings')return navigate('settings');
  if(action==='refresh')return refresh();
  if(action==='save-tool-paths') {
    await api('/connection',{ffmpeg_path:$('#ffmpeg-path').value,ffprobe_path:$('#ffprobe-path').value},'PUT');await refreshSettings();toast('路径已保存，环境检测已更新。');return;
  }
  if(action==='adopt-topic'||action==='adopt') {
    const stage=el.dataset.stage||'research', v=selectedVersion(stage);
    await api(`/projects/${S.selected}/versions/${v.id}/adopt`,action==='adopt-topic'?{topic_id:el.dataset.id}:{});
    capture();if(action==='adopt-topic')S.page='script';await refresh();toast('版本已采用，上游变化会标记下游成果待更新。');return;
  }
  if(action==='compare')return compare(el.dataset.stage);
  if(action==='save-settings') {
    const group=el.dataset.group;
    if(!['preferences','budget'].includes(group))throw new Error('请选择设置分组');
    const settings=Object.fromEntries($$(`#${group}-settings [data-default]`).map(x=>[x.dataset.default,x.type==='number'?Number(x.value):x.value]));
    await api(`/settings/${group}`,settings,'PUT');await refreshSettings(group);toast(group==='budget'?'预算已保存':'创作偏好已保存');return;
  }
  if(action==='add-source') {await api(`/projects/${S.selected}/sources`,{url:$('#source-url').value,title:$('#source-title').value});capture();await refresh();toast('参考来源已保存，标记为用户提供。');return;}
  if(action==='import-csv')return $('#csv-input').click();
  if(action==='analyze')return runTask('analyze');
  if(action==='local-edit')return runTask('local_edit');
  if(action==='inspect-asset')return previewAsset(el.dataset.id);
  if(action==='save-asset') {
    const a=S.detail.assets.find(a=>a.id===el.dataset.id), body={provenance:$('#asset-provenance').value};
    if(a.kind!=='music')body.protected=JSON.parse($('#asset-protected').value);
    await api(`/projects/${S.selected}/assets/${a.id}`,body,'PATCH');$('#modal').close();capture();await refresh();toast('素材信息与保护区已保存。');return;
  }
  if(action==='save-timeline') {
    const v=await api(`/projects/${S.selected}/versions`,{stage:'edit',parent_id:selectedVersion('edit').id,result:JSON.parse($('#timeline-json').value)});
    S.versions.edit=v.id;capture();await refresh();toast('时间线已校验并保存，请渲染预览。');return;
  }
  if(action==='render-preview'||action==='export-final')return runTask(action==='export-final'?'final':'preview',{version_id:selectedVersion('edit').id});
  if(action==='publish-page')return navigate('publish');
  if(action==='edit-page')return navigate('edit');
  if(action==='cancel-task') {await api(`/tasks/${el.dataset.id}/cancel`,{});await refresh();toast('已请求停止当前任务，已保存的版本和文件会保留。');return;}
  if(action==='retry-task') {await api(`/tasks/${el.dataset.id}/retry`,{});await refresh();toast('已重新开始，完成后会自动显示在这里。');return;}
  if(action==='save-publication') {
    const metrics=Object.fromEntries($$('[data-metric]').map(x=>[x.dataset.metric,x.value===''?null:Number(x.value)]));
    await api(`/projects/${S.selected}/publications`,{version_id:$('#publication-version').value,platform:$('#publication-platform').value,
      url:$('#publication-url').value,published_at:$('#publication-date').value?new Date($('#publication-date').value).toISOString():null,metrics});
    await refresh();toast('发布记录已保存。');return;
  }
  if(action==='update-snapshot') {
    modal('添加真实数据快照',`<p class="section-note">记录当前可见指标，无法获取的留空。不同平台分别记录。</p><div class="publishing-grid">${['views','likes','comments','completion_rate'].map((key,i)=>`<div class="field"><label>${['播放','点赞','评论','完播率（%）'][i]}</label><input data-snapshot="${key}" type="number" min="0"></div>`).join('')}</div>`,button('save-snapshot','保存快照','primary',`data-id="${el.dataset.id}"`));return;
  }
  if(action==='save-snapshot') {
    const p=S.detail.publications.find(x=>x.id===el.dataset.id), metrics=Object.fromEntries($$('[data-snapshot]').map(x=>[x.dataset.snapshot,x.value===''?null:Number(x.value)]));
    await api(`/projects/${S.selected}/publications`,{id:p.id,version_id:p.version_id,metrics});$('#modal').close();await refresh();toast('新快照已追加，历史数据保留。');return;
  }
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action],[data-page],#new-project'); if(!el)return;
  try { if(el.dataset.page) return await navigate(el.dataset.page);if(el.id==='new-project')return navigate('home');el.disabled=true;await handle(el.dataset.action,el); }
  catch(error){toast(error.message,true);} finally {if(el.isConnected)el.disabled=false;}
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try {
    if(el.id==='profile-protocol') {$('#profile-models').innerHTML=serviceModelFields(el.value);const url=$('#profile-url');if(['https://api.anthropic.com','https://api.openai.com'].includes(url.value.replace(/\/$/,'')))url.value=el.value==='openai'?'https://api.openai.com':'https://api.anthropic.com';}
    if(el.id==='project-select') {capture();S.selected=el.value;S.versions={};localStorage.setItem('studio-project',S.selected);await refresh();}
    if(el.id==='version-select') {capture();S.versions[el.dataset.stage]=el.value;render();}
    if(el.id==='compare-a'||el.id==='compare-b')updateComparison();
    if(el.id==='csv-input'&&el.files.length) {const form=new FormData();form.append('file',el.files[0]);const result=await api(`/projects/${S.selected}/sources/csv`,form);capture();await refresh();toast(`导入 ${result.imported} 条来源${result.errors.length?'，'+result.errors.length+' 行格式有误':''}`);}
  } catch(error){toast(error.message,true);}
});
document.addEventListener('timeupdate',async event=>{
  if(event.target.id!=='preview-player')return;
  const v=selectedVersion('edit'),t=event.target.currentTime;
  const source=v.result.segments.find(s=>t>=s.output_start_sec&&t<s.output_end_sec);
  if($('#preview-time'))$('#preview-time').textContent=`当前 ${t.toFixed(2)}s / ${v.result.duration.toFixed(2)}s`+(source?` · 源素材 ${(source.source_start_sec+t-source.output_start_sec).toFixed(2)}s`:'');
},true);
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal'))$('#modal').close();});
window.addEventListener('hashchange',()=>routeV2().catch(error=>toast(error.message,true)));
async function poll() {
  try {
    if(S.selected&&!document.hidden) {
      const ident=S.selected, previous=S.detail?.tasks||[], latest=await api(`/projects/${ident}`);
      setOffline(false);
      if(ident!==S.selected)return;
      const changed=latest.tasks.some(t=>!previous.some(old=>old.id===t.id&&old.status===t.status));
      const completed=latest.tasks.filter(t=>t.status==='completed'&&!previous.some(old=>old.id===t.id&&old.status==='completed'));
      S.detail=latest;
      if(typeof pollV2==='function' && await pollV2(changed,completed))return;
      if(changed&&completed.length) {
        const editing=document.activeElement?.matches('input,textarea,select');
        if(!editing&&!$('#modal').open){
          for(const task of completed)if(task.output?.version_id){const v=latest.versions.find(v=>v.id===task.output.version_id);if(v)S.versions[v.stage]=v.id;}
          capture();await refresh();
        }else toast('任务已完成并保存，请通过版本菜单查看新版本。');
      } else if($('#task-slot')) {const fragment=document.createElement('div');fragment.innerHTML=taskPanel();$('#task-slot').replaceWith(fragment.firstElementChild);}
    } else if(!document.hidden) await fetch('/api/environment',{cache:'no-store'}).then(()=>setOffline(false),()=>setOffline(true));
  } catch(error) {
    // The project was deleted elsewhere (another tab, or data restored): leave it instead of polling a 400 forever.
    if(error.message==='记录不存在'){S.selected=null;S.detail=null;localStorage.removeItem('studio-project');if(S.page==='workspace'){S.page='projects';history.replaceState(null,'','#projects');}await refresh().catch(()=>{});toast('这个作品已不存在，已返回作品列表。',true);}
    else setOffline(true); /* Keep user drafts intact while the local service is restarting. */
  }
  finally {setTimeout(poll,2500);}
}
async function setOffline(offline) {
  if(offline){try{await fetch('/api/environment',{cache:'no-store'});return;}catch{}}
  let bar=$('#offline-banner');
  if(offline&&!bar){bar=document.createElement('div');bar.id='offline-banner';bar.setAttribute('role','alert');bar.innerHTML='<strong>与本地服务的连接已断开</strong><span>正在自动重连。你的编辑仍保留在页面上；如果长时间无法恢复，请重新运行「启动工作台」。</span>';document.body.append(bar);}
  else if(!offline&&bar){bar.remove();toast('已重新连接本地服务。');}
}
async function initialize() {
  let failure;
  for(let attempt=0;attempt<6;attempt++) {
    try {await refresh();return;}catch(error){failure=error;await new Promise(resolve=>setTimeout(resolve,700));}
  }
  S.loading=false;$('#content').innerHTML=heading('工作台尚未连接','本地服务需要启动后才能保存项目和处理素材。')+`<div class="warning-box">${esc(failure.message)}</div>`+empty('⚙','启动本地工作台','双击项目目录里的「启动工作台.cmd」。首次运行需要本机安装 Python。');
}
// V2 owns routing and starts initialization after both frontend files are loaded.
