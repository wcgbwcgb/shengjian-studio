'use strict';

const SCOPE_NAMES = {home:'首页',project:'对话',research:'调研页',script:'文案页',inspiration:'灵感'};

// Prompts ---------------------------------------------------------------
function renderPrompts() {
  const promptCard=p=>`<article class="library-item"><div class="library-item-head"><h3>${esc(p.label)}</h3><span>${p.scope.map(s=>`<span class="chip">${SCOPE_NAMES[s]}</span>`).join(' ')}</span></div>${p.description?`<p>${esc(p.description)}</p>`:''}<pre class="prompt-preview">${esc(p.body.slice(0,180))}${p.body.length>180?'…':''}</pre>${button('edit-prompt','编辑','small ghost',`data-id="${esc(p.id)}"`)}</article>`;
  return `<div class="collection-shell">${heading('提示词','工作台的每个功能都是一段发给 Claude 的话。改这里，就是改功能。','PROMPTS')}
    <div class="section-heading"><p class="small-note">按钮背后的文字。点按钮只会把它填进输入框，你能看到并修改要发送的全部内容。</p>${button('new-prompt','＋ 新建提示词','small')}</div><div class="prompt-grid">${S.prompts.map(promptCard).join('')||'<p class="muted">还没有提示词。</p>'}</div></div>`;
}
function promptEditor(p) {
  const scope=p?.scope||['project'];
  modal(p?'编辑提示词':'新建提示词',`<div class="field"><label for="prompt-label">按钮名称</label><input id="prompt-label" maxlength="40" value="${esc(p?.label||'')}"></div>
    <div class="field"><span class="field-label">出现在</span><div class="button-row">${Object.entries(SCOPE_NAMES).map(([k,v])=>`<label class="brief-toggle"><input type="checkbox" data-prompt-scope="${k}" ${scope.includes(k)?'checked':''}> ${v}</label>`).join('')}</div></div>
    <div class="field"><label for="prompt-description">说明（鼠标悬停时显示）</label><input id="prompt-description" maxlength="200" value="${esc(p?.description||'')}"></div>
    <div class="field"><label for="prompt-body">提示词内容</label><textarea id="prompt-body" class="file-editor" rows="14">${esc(p?.body||'')}</textarea></div>
    <p class="small-note">「灵感」位置的两个提示词（换一批灵感、联网找近期热点）由灵感页的按钮使用，需要让 Claude 把选题写进 ideas.json。作品的「调研」页显示 research.json 和 directions.json，「文案」页显示 脚本.md。</p>`,
    (p?button('delete-prompt','删除','ghost danger',`data-id="${esc(p.id)}"`):'')+(p?.default?button('reset-prompt','恢复默认','ghost',`data-id="${esc(p.id)}"`):'')+button('close-modal','取消','ghost')+button('save-prompt','保存','primary',`data-id="${esc(p?.id||'')}"`));
}
async function reloadLibrary(message) {
  S.prompts=await api('/prompts');
  if($('#modal').open)$('#modal').close();
  render();toast(message);
}

// Settings ----------------------------------------------------------------
function cliSettingsCard() {
  const cli=S.env.claude_cli||{}, o=cli.options||{}, check=cli.last_check;
  return `<section class="card cli-settings"><div class="card-head"><h2>本机 Claude Code</h2><span class="chip ${check?.logged_in&&check?.supported?'green':'warn'}">${check?.logged_in&&check?.supported?'已就绪':cli.installed?'待检测登录':'等待连接'}</span></div><p class="section-note">工作台的所有功能都通过这台电脑上已登录的 Claude Code 完成。</p><div class="field"><label for="cli-path">Claude Code 路径</label><input id="cli-path" value="${esc(o.path||'')}" placeholder="自动查找；也可填写 claude.exe 完整路径"></div><div class="publishing-grid"><div class="field"><label for="cli-model">模型</label><select id="cli-model">${[...new Set(['opus','sonnet','haiku',o.model||'opus'])].map(m=>`<option value="${esc(m)}" ${m===(o.model||'opus')?'selected':''}>${esc({opus:'Opus',sonnet:'Sonnet',haiku:'Haiku'}[m]||m)}</option>`).join('')}</select></div><div class="field"><label for="cli-timeout">单次任务超时（分钟）</label><input id="cli-timeout" type="number" min="1" max="360" value="${Math.round((o.timeout_sec||7200)/60)}"></div></div><details class="details"><summary>执行上限</summary><div class="field"><label for="cli-turns">最多执行轮数</label><input id="cli-turns" type="number" min="1" max="2000" value="${o.max_turns||300}"></div></details><div class="button-row">${button('cli-check','保存并检测安装与登录','primary small')}${button('cli-save','仅保存','small')}</div><div class="connection-result" role="status">${esc(check?.message||cli.error||'先安装 Claude Code，在终端运行 claude auth login 登录，再点击检测。')}${check?.version?` · v${esc(check.version)}`:''}</div><p class="section-note">需要 Claude Code 2.1.248 或以上。Claude 可以联网、安装 Python / Node 依赖、使用全部工具；只拦截删除或移动工作文件夹以外的文件、修改系统设置、读取凭据和对外发布等操作。依赖与模板保存在 .runtime/agent-toolbox，可重复使用。</p></section>`;
}
function readiness() {
  const cli=S.env.claude_cli||{}, check=cli.last_check;
  const rows=[
    [cli.installed&&check?.logged_in&&check?.supported,'Claude Code 已连接',cli.installed?(check?.logged_in?'需要更新 Claude Code 到新版本':'还没有检测登录：在下方点「保存并检测」'):'还没有找到 Claude Code：安装后在下方填写位置并检测'],
    [S.env.ffmpeg&&S.env.ffprobe,'视频处理工具（FFmpeg）已就绪','Claude 剪辑视频、工作台保存视频版本都需要 FFmpeg：在右侧填写路径'],
  ];
  const ready=rows.every(r=>r[0]);
  return `<section class="card"><div class="card-head"><h2>${ready?'一切就绪，可以开始创作':'还差几步就能开始创作'}</h2><span class="chip ${ready?'green':'warn'}">${ready?'已就绪':'需要设置'}</span></div><div class="readiness">${rows.map(([ok,yes,no])=>`<div class="readiness-row ${ok?'':'missing'}"><b>${ok?'✓':'!'}</b><span>${ok?esc(yes):esc(no)}</span></div>`).join('')}</div></section>`;
}
function renderSettings() {
  const t=S.tools||{};
  return heading('设置','连接 Claude Code，配置本地视频工具。','SETTINGS')+
    `<div class="settings-layout"><div class="result-stack">${readiness()}${cliSettingsCard()}</div><aside class="card"><div class="card-head"><h2>本地工具</h2>${button('refresh-settings','刷新','small')}</div>${[['ffmpeg','FFmpeg'],['ffprobe','ffprobe']].map(([k,l])=>`<div class="env-row"><span>${l}</span><span>${S.env[k]?'✓ 已就绪':'○ 未配置'}</span></div>`).join('')}<div class="field"><label for="ffmpeg-path">ffmpeg.exe 路径</label><input id="ffmpeg-path" value="${esc(t.ffmpeg_path||'')}"></div><div class="field"><label for="ffprobe-path">ffprobe.exe 路径</label><input id="ffprobe-path" value="${esc(t.ffprobe_path||'')}"></div>${button('save-tool-paths','保存并检测','small')}<p class="section-note">留空时自动从系统 PATH 查找。</p></aside></div>`;
}

Object.assign(HANDLERS, {
  'new-prompt':()=>promptEditor(null),
  'edit-prompt':el=>promptEditor(S.prompts.find(p=>p.id===el.dataset.id)),
  'save-prompt':async el=>{
    const body={label:$('#prompt-label').value,description:$('#prompt-description').value,body:$('#prompt-body').value,scope:$$('[data-prompt-scope]').filter(x=>x.checked).map(x=>x.dataset.promptScope)};
    await (el.dataset.id?api(`/prompts/${el.dataset.id}`,body,'PUT'):api('/prompts',body));
    await reloadLibrary('提示词已保存，之后点按钮就会填入新内容。');
  },
  'delete-prompt':async el=>{await api(`/prompts/${el.dataset.id}`,{},'DELETE');await reloadLibrary('提示词已删除。');},
  'reset-prompt':async el=>{await api(`/prompts/${el.dataset.id}/reset`,{});await reloadLibrary('已恢复默认内容。');},
  'refresh-settings':()=>refresh(),
  'save-tool-paths':async()=>{await api('/tools',{ffmpeg_path:$('#ffmpeg-path').value,ffprobe_path:$('#ffprobe-path').value},'PUT');await refresh();toast('路径已保存，环境检测已更新。');},
  'cli-save':()=>saveCli(false),
  'cli-check':()=>saveCli(true),
});
async function saveCli(check) {
  await api('/claude-code',{path:$('#cli-path').value,model:$('#cli-model').value,timeout_sec:Number($('#cli-timeout').value)*60,max_turns:Number($('#cli-turns').value)},'PUT');
  if(check)await api('/claude-code/check',{});
  await refresh();toast(check?(S.env.claude_cli?.last_check?.message||'环境检测完成'):'Claude Code 设置已保存。');
}
