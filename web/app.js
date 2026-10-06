'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pageNames = {home:'开始创作',inspiration:'灵感发现',projects:'我的作品',workspace:'创作空间',library:'素材库',films:'成片',publish:'发布记录',prompts:'提示词',settings:'设置'};
const S = {page:'home', projects:[], selected:localStorage.getItem('studio-project'), detail:null, env:{}, prompts:[], tools:{}, inspirations:{}, library:{assets:[],films:[]}, loading:true};
const W = {};
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
  const names={prompt:'要求',name:'名称',label:'名称',body:'内容',description:'说明',text:'文件内容'};
  if(!first)return '请求没有被接受，请检查输入后重试。';
  if(/at most|too_long/.test(first.type||first.msg))return `${names[field]||'输入'}太长了，请精简后再试。`;
  if(/at least|too_short|missing/.test(first.type||first.msg))return `请先填写${names[field]||'必填内容'}。`;
  return `${names[field]||'输入'}格式不正确，请检查后重试。`;
}
// Turns a backend/CLI error into what happened, why, and what the user can do next.
function explainError(text='') {
  const t=String(text);
  const rules=[
    [/登录|auth login|loggedIn/i,'Claude 还没有登录','这台电脑上的 Claude Code 没有检测到登录。','打开设置，按提示登录后点「检测」，再回来重试。','settings'],
    [/未找到 Claude Code|claude\.exe|CLI 路径|Claude Code 路径/i,'没有找到 Claude Code','需要先在这台电脑上安装 Claude Code。','打开设置，填写 Claude Code 位置或安装后重新检测。','settings'],
    [/FFmpeg|ffprobe/i,'缺少视频处理工具','保存视频版本需要本机的 FFmpeg。','打开设置，在「本地工具」中填写 FFmpeg 路径。','settings'],
    [/超时|timeout|timed out/i,'这一步花的时间太久，已停止','可能是网络较慢，或要求的范围太大。','重试一次；或者在设置里放宽超时时间。','retry'],
    [/overloaded|529|rate.?limit|429|额度|usage limit/i,'Claude 暂时繁忙或额度已用完','服务端临时限流，或订阅额度达到上限。','稍等几分钟后重试。','retry'],
    [/无法连接|network|ECONN/i,'网络连接失败','这台电脑暂时连不上 Claude。','检查网络后重试。','retry'],
    [/turn limit|max_turns|执行轮数/i,'步骤太多，没能完成','任务超过了允许的执行轮数。','把要求拆小一点，或在设置中提高「最多执行轮数」。','retry'],
  ];
  const hit=rules.find(([re])=>re.test(t));
  if(hit)return {title:hit[1],why:hit[2],next:hit[3],action:hit[4],detail:t};
  return {title:'这一步没有完成',why:'',next:'工作文件夹里的文件都还在，可以直接重试。',action:'retry',detail:t};
}
const elapsedText = since => {const s=Math.max(0,Math.round((Date.now()-new Date(since))/1000));return s<60?`${s} 秒`:`${Math.floor(s/60)} 分 ${String(s%60).padStart(2,'0')} 秒`;};
setInterval(()=>$$('[data-since]').forEach(el=>{el.textContent=elapsedText(el.dataset.since);}),1000);
const date = value => value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '未提供';
const fileURL = (path, project=S.selected) => `/api/projects/${project}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
const workURL = (path, project=S.selected) => `/api/projects/${project}/work/${path.split('/').map(encodeURIComponent).join('/')}`;
const assetURL = a => `/api/assets/${a.id}/file`;
const link = (url,title) => /^https?:\/\//i.test(url||'') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title||url)} ↗</a>` : esc(title||url);
const button = (action, label, cls='', attrs='') => `<button data-action="${action}" class="${cls}" ${attrs}>${label}</button>`;
const empty = (icon, title, description, action='') => `<div class="empty"><div class="empty-icon">${icon}</div><h3>${title}</h3><p>${description}</p>${action}</div>`;
const activeTask = () => S.detail?.tasks.find(t=>['queued','running','cancelling'].includes(t.status));
function heading(title, description, eyebrow='YOUR IDEA, YOUR STORY') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="muted">${description}</p></div></div>`;
}
function modal(title, body, footer='') {
  $('#modal-content').innerHTML=`<div class="dialog-head"><h2>${title}</h2>${button('close-modal','×','ghost')}</div>${body}${footer?`<div class="dialog-actions">${footer}</div>`:''}`;
  if(!$('#modal').open)$('#modal').showModal();
}
function promptsFor(scope) {return S.prompts.filter(p=>p.scope.includes(scope));}

async function refresh(renderPage=true) {
  const ident=S.selected;
  const [projects,env,prompts,library,inspirations] = await Promise.all([api('/projects'),api('/environment'),api('/prompts'),api('/library'),api('/inspirations')]);
  Object.assign(S,{projects,env,prompts,library,inspirations,loading:false});
  if(S.page==='settings')S.tools=(await api('/settings')).tools;
  if(ident!==S.selected)return;
  if(S.selected&&!S.projects.some(p=>p.id===S.selected))S.selected=null;
  if(S.selected){const selected=S.selected,detail=await api(`/projects/${selected}`);if(selected!==S.selected)return;S.detail=detail;}else S.detail=null;
  if(renderPage)render();
}
function render() {
  const screens={home:renderHome,inspiration:renderInspiration,projects:renderWorks,workspace:renderWorkspace,library:renderAssetLibrary,films:renderFilms,publish:renderPublish,prompts:renderPrompts,settings:renderSettings};
  document.title=`${pageNames[S.page]} · 声间`;
  $('#crumb').textContent=pageNames[S.page];
  $$('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===S.page||(S.page==='workspace'&&b.dataset.page==='projects')));
  document.body.classList.toggle('in-workspace',S.page==='workspace');
  const check=S.env.claude_cli?.last_check;
  $('#env-dot').style.background=S.env.ffmpeg&&check?.logged_in&&check?.supported?'#a4ce89':'#d7ad6f';
  if(S.loading){$('#content').innerHTML=heading('准备创作空间','正在读取本地项目…');return;}
  $('#content').innerHTML=(screens[S.page]||renderHome)();
  if(S.page==='workspace')afterWorkspaceRender();
}
async function navigate(page) {
  S.page=page;location.hash=page;
  if(page==='prompts'||page==='settings')await refresh(false);
  render();
  if(page==='home')$('#creation-idea')?.focus();
}
async function openProject(id, tab='conversation') {
  S.selected=id;S.page='workspace';W.tab=tab;W.scriptEditing=false;
  localStorage.setItem('studio-project',id);
  history.pushState(null,'',`#workspace/${id}/${tab}`);
  await refresh(false);
  W.scrollToEnd=true;
  render();
}
function showTab(tab) {
  W.tab=tab;W.scriptEditing=false;
  history.replaceState(null,'',`#workspace/${S.selected}/${tab}`);
  render();
}
async function route() {
  const [page,id,tab]=location.hash.slice(1).split('/');
  if(page==='workspace'&&id){
    S.page='workspace';W.tab=['research','script'].includes(tab)?tab:'conversation';
    if(S.selected!==id||!S.detail){S.selected=id;localStorage.setItem('studio-project',id);await refresh(false);}
    W.scrollToEnd=true;
  } else S.page=pageNames[page]&&page!=='workspace'?page:'home';
  if(S.page==='prompts'||S.page==='settings')await refresh(false);
  render();
}

function versionNumber(v) {const list=S.detail.versions;return list.length-list.findIndex(x=>x.id===v.id);}
function renderPublish() {
  if(!S.detail)return heading('发布记录','先打开一个作品，再记录它的发布。','PUBLISH')+empty('↗','还没有选择作品','在「我的作品」里打开一个作品，再回到这里。',button('go-projects','查看我的作品','primary'));
  const finals=S.detail.versions.filter(v=>v.final);
  return `${button('open-project','← 回到作品','text-button',`data-id="${S.selected}"`)}${heading('发布记录',esc(S.detail.project.name),'PUBLISH')}<div class="result-stack"><section class="card"><div class="card-head"><h2>记录一次发布</h2><span class="chip">手动发布</span></div>${finals.length?`<div class="field"><label>视频版本</label><select id="publication-version">${finals.map(v=>`<option value="${v.id}">V${versionNumber(v)} · ${date(v.created_at)}</option>`).join('')}</select></div><div class="publishing-grid"><div class="field"><label>发布平台</label><select id="publication-platform"><option>抖音</option><option>B站</option><option>小红书</option><option>视频号</option><option>YouTube</option></select></div><div class="field"><label>发布时间</label><input id="publication-date" type="datetime-local"></div></div><div class="field"><label>作品链接</label><input id="publication-url" placeholder="https://…"></div><div class="publishing-grid">${['views','likes','comments','completion_rate'].map((k,i)=>`<div class="field"><label>${['播放量','点赞','评论','完播率（%）'][i]}</label><input data-metric="${k}" type="number" min="0" placeholder="未获取时留空"></div>`).join('')}</div><div class="button-row end">${button('save-publication','保存发布记录','primary small')}</div>`:empty('↗','还没有视频版本','Claude 做出视频后，就可以在这里记录发布。')}</section><section class="card"><div class="card-head"><h2>发布与数据快照</h2><a class="muted" href="/api/projects/${S.selected}/export/records">↓ 导出记录</a></div>${S.detail.publications.length?S.detail.publications.map(p=>`<article class="publication"><div class="card-head"><h3>${esc(p.platform)}</h3>${button('update-snapshot','添加快照','small',`data-id="${p.id}"`)}</div>${link(p.url,'查看发布作品')}<p class="section-note">发布于 ${date(p.published_at)}</p><div class="snapshots">${p.snapshots.map(s=>`${date(s.at)} · ${Object.entries(s.metrics).map(([k,x])=>`${esc({views:'播放',likes:'点赞',comments:'评论',completion_rate:'完播率'}[k]||k)} ${x??'未获取'}`).join(' / ')}`).join('<br>')}</div></article>`).join(''):empty('▤','发布记录会保存在这里','发布后 24 小时和 7 天各添加一份快照。不同平台分别记录，缺失数据保持为空。')}</section></div>`;
}

const HANDLERS = {};
async function handle(action, el) {
  if(action==='close-modal')return $('#modal').close();
  if(action==='go-projects')return navigate('projects');
  if(action==='open-project')return openProject(el.dataset.id);
  if(action==='cancel-task'){await api(`/tasks/${el.dataset.id}/cancel`,{});await refresh();toast('已请求停止，工作文件夹里已有的文件会保留。');return;}
  if(action==='retry-task'){await api(`/tasks/${el.dataset.id}/retry`,{});W.scrollToEnd=true;await refresh();toast('已重新开始。');return;}
  if(action==='save-publication'){
    const metrics=Object.fromEntries($$('[data-metric]').map(x=>[x.dataset.metric,x.value===''?null:Number(x.value)]));
    await api(`/projects/${S.selected}/publications`,{version_id:$('#publication-version').value,platform:$('#publication-platform').value,
      url:$('#publication-url').value,published_at:$('#publication-date').value?new Date($('#publication-date').value).toISOString():null,metrics});
    await refresh();toast('发布记录已保存。');return;
  }
  if(action==='update-snapshot'){modal('添加真实数据快照',`<p class="section-note">记录当前可见指标，无法获取的留空。</p><div class="publishing-grid">${['views','likes','comments','completion_rate'].map((key,i)=>`<div class="field"><label>${['播放','点赞','评论','完播率（%）'][i]}</label><input data-snapshot="${key}" type="number" min="0"></div>`).join('')}</div>`,button('save-snapshot','保存快照','primary',`data-id="${el.dataset.id}"`));return;}
  if(action==='save-snapshot'){
    const p=S.detail.publications.find(x=>x.id===el.dataset.id), metrics=Object.fromEntries($$('[data-snapshot]').map(x=>[x.dataset.snapshot,x.value===''?null:Number(x.value)]));
    await api(`/projects/${S.selected}/publications`,{id:p.id,version_id:p.version_id,metrics});$('#modal').close();await refresh();toast('新快照已追加，历史数据保留。');return;
  }
  const handler=HANDLERS[action];
  if(handler)return handler(el);
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action],[data-page],#new-project'); if(!el)return;
  try { if(el.dataset.page)return await navigate(el.dataset.page);if(el.id==='new-project')return navigate('home');el.disabled=true;await handle(el.dataset.action,el); }
  catch(error){toast(error.message,true);} finally {if(el.isConnected)el.disabled=false;}
});
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal'))$('#modal').close();});
window.addEventListener('hashchange',()=>route().catch(error=>toast(error.message,true)));

async function poll() {
  try {
    if(S.page==='workspace'&&S.selected&&!document.hidden) {
      const ident=S.selected, latest=await api(`/projects/${ident}`);
      setOffline(false);
      if(ident===S.selected&&S.page==='workspace')updateWorkspace(latest);
    } else if(!document.hidden) await fetch('/api/environment',{cache:'no-store'}).then(()=>setOffline(false),()=>setOffline(true));
  } catch(error) {
    // The project was deleted elsewhere: leave it instead of polling a 400 forever.
    if(error.message==='记录不存在'){S.selected=null;S.detail=null;localStorage.removeItem('studio-project');S.page='projects';history.replaceState(null,'','#projects');await refresh().catch(()=>{});toast('这个作品已不存在，已返回作品列表。',true);}
    else setOffline(true);
  }
  finally {setTimeout(poll,2500);}
}
async function setOffline(offline) {
  if(offline){try{await fetch('/api/environment',{cache:'no-store'});return;}catch{}}
  let bar=$('#offline-banner');
  if(offline&&!bar){bar=document.createElement('div');bar.id='offline-banner';bar.setAttribute('role','alert');bar.innerHTML='<strong>与本地服务的连接已断开</strong><span>正在自动重连。你写的内容仍保留在页面上；如果长时间无法恢复，请重新运行「启动工作台」。</span>';document.body.append(bar);}
  else if(!offline&&bar){bar.remove();toast('已重新连接本地服务。');}
}
async function initialize() {
  let failure;
  for(let attempt=0;attempt<6;attempt++) {
    try {await refresh(false);await route();setTimeout(poll,1500);return;}catch(error){failure=error;await new Promise(resolve=>setTimeout(resolve,700));}
  }
  S.loading=false;$('#content').innerHTML=heading('工作台尚未连接','本地服务需要启动后才能保存作品。')+`<div class="warning-box">${esc(failure.message)}</div>`+empty('⚙','启动本地工作台','双击项目目录里的「启动工作台.cmd」。首次运行需要本机安装 Python。');
}
document.addEventListener('DOMContentLoaded',()=>{render();initialize();});
