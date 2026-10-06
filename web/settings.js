'use strict';

function serviceModels(protocol) {
  return (S.modelCatalog||S.models).filter(m=>!m.engine&&m.provider===(protocol==='openai'?'openai':'claude'));
}
function textServiceCard() {
  const service=S.textService||{engine:'api',models:{research:'sonnet',script:'sonnet'}};
  return `<section class="card"><div class="card-head"><h2>调研与文案服务</h2><span class="chip">${service.engine==='claude_cli'?'Claude 订阅':'API'}</span></div><div class="field"><label for="text-engine">制作方式</label><select id="text-engine"><option value="claude_cli" ${service.engine==='claude_cli'?'selected':''}>Claude 订阅 · 本机 Claude Code</option><option value="api" ${service.engine==='api'?'selected':''}>API · 使用下方服务连接</option></select></div><div class="settings-grid">${['research','script'].map(stage=>`<div class="field"><label>${stageNames[stage]}订阅模型</label><select data-text-model="${stage}">${['sonnet','opus','haiku'].map(model=>`<option value="${model}" ${service.models[stage]===model?'selected':''}>Claude ${model[0].toUpperCase()+model.slice(1)}</option>`).join('')}</select></div>`).join('')}</div><p class="section-note">订阅模式共用上方 Claude Code 登录，不需要 API Key。调研可以搜索和读取网页；写稿、角度与局部重写使用同一订阅。模型别名由 CLI 解析，额度以 Claude 账号为准。保存只影响之后的新任务。</p><div class="button-row end">${button('save-text-service','保存调研与文案服务','primary small')}</div></section>`;
}
function serviceOptions(protocol,chosen) {
  return serviceModels(protocol).map(m=>`<option value="${m.id}" ${m.id===chosen?'selected':''}>${esc(m.name)}</option>`).join('');
}
function serviceEditor(profile={}) {
  const protocol=profile.protocol||'openai', defaults=profile.model_defaults||{}, prices=profile.pricing||{};
  return `<div class="field"><label for="profile-name">连接名称</label><input id="profile-name" maxlength="80" value="${esc(profile.name||'')}" placeholder="例如：我的 OpenAI"></div>
    <div class="field"><label for="profile-protocol">Provider / 接口</label><select id="profile-protocol"><option value="openai" ${protocol==='openai'?'selected':''}>OpenAI · Responses</option><option value="anthropic" ${protocol==='anthropic'?'selected':''}>Claude · Anthropic Messages</option></select></div>
    <div class="field"><label for="profile-url">API 基础地址</label><input id="profile-url" type="url" value="${esc(profile.base_url||(protocol==='openai'?'https://api.openai.com':'https://api.anthropic.com'))}"></div>
    <div class="field"><label for="profile-key">API Key</label><input id="profile-key" type="password" autocomplete="new-password" placeholder="${profile.id?'留空保留现有密钥':'填写密钥'}"></div>
    <div id="profile-models">${serviceModelFields(protocol,defaults,prices)}</div><p class="section-note">此连接独立保存模型和计费单价。切换连接只影响之后创建的任务。</p>`;
}
function serviceModelFields(protocol,defaults={},prices={}) {
  const models=serviceModels(protocol), fallback=models.find(m=>m.id==='claude-sonnet-5-5')?.id||models[0]?.id;
  return `<div class="settings-grid">${['research','script','edit'].map(stage=>`<div class="field"><label>${stageNames[stage]}默认模型</label><select data-profile-model="${stage}">${serviceOptions(protocol,defaults[stage]||fallback)}</select></div>`).join('')}</div>
    <details class="details"><summary>此连接的计费单价（美元 / 百万 token；搜索按次）</summary>${models.map(m=>`<div class="pricing-model"><h3>${esc(m.name)}</h3><div class="settings-grid">${[['input_price','输入'],['output_price','输出'],['cache_read_price','缓存读取'],['cache_write_price','缓存写入'],['search_price','搜索 / 次']].map(([key,label])=>`<div><label>${label}</label><input data-profile-price="${key}" data-model="${m.id}" type="number" min="0" step="0.001" value="${prices[m.id]?.[key]??m[key]}"></div>`).join('')}</div></div>`).join('')}</details>`;
}
function readiness() {
  const cli=S.env.claude_cli||{}, check=cli.last_check, sub=S.textService?.engine==='claude_cli';
  const rows=[
    [cli.installed&&check?.logged_in&&check?.supported,'Claude Code 已连接',cli.installed?(check?.logged_in?'需要更新 Claude Code 到新版本':'还没有检测登录：在下方点「保存并检测」'):'还没有找到 Claude Code：安装后在下方填写位置并检测'],
    [sub?cli.installed:S.env.api_configured,`调研与写稿：${sub?'使用 Claude 订阅':'使用 API'}`,sub?'需要先连接 Claude Code':'还没有添加 API 服务'],
    [S.env.ffmpeg&&S.env.ffprobe,'视频处理工具（FFmpeg）已就绪','生成视频需要 FFmpeg：在右侧「本地工具」填写路径'],
  ];
  const ready=rows.every(r=>r[0]);
  return `<section class="card"><div class="card-head"><h2>${ready?'一切就绪，可以开始创作':'还差几步就能开始创作'}</h2><span class="chip ${ready?'green':'warn'}">${ready?'已就绪':'需要设置'}</span></div><div class="readiness">${rows.map(([ok,yes,no])=>`<div class="readiness-row ${ok?'':'missing'}"><b>${ok?'✓':'!'}</b><span>${ok?esc(yes):esc(no)}</span></div>`).join('')}</div>${ready&&sessionStorage.getItem('studio-return')?vbutton('return','回到刚才的作品 →','primary small'):''}</section>`;
}
function renderSettings() {
  const d=S.defaults,c=S.connection, sub=S.textService?.engine==='claude_cli';
  const groupInput=(key,label)=>`<div class="field"><label>${label}</label><input data-default="${key}" type="${['duration','days','search_limit','monthly_budget','max_tokens'].includes(key)?'number':'text'}" value="${esc(d[key])}"></div>`;
  return (sessionStorage.getItem('studio-return')?vbutton('return','← 回到创作','text-button'):'')+heading('设置','连接 Claude、配置视频工具，并设置新作品的默认偏好。',false,'SETTINGS')+
    `<div class="settings-layout"><div class="result-stack">${readiness()}${cliSettingsCard()}${textServiceCard()}
    <section class="card" id="preferences-settings"><div class="card-head"><h2>创作偏好</h2><span class="chip">新项目使用</span></div><div class="settings-grid">${[['audience','目标受众'],['style','表达风格'],['platform','发布平台'],['exclude','避免内容'],['roles','角色设定'],['duration','目标时长（秒）'],['days','研究范围（天）'],['search_limit','搜索上限']].map(([k,l])=>groupInput(k,l)).join('')}<div class="field"><label>画幅</label><select data-default="aspect">${['9:16','16:9','1:1'].map(a=>`<option ${d.aspect===a?'selected':''}>${a}</option>`).join('')}</select></div></div><div class="button-row end">${button('save-settings','保存创作偏好','primary small','data-group="preferences"')}</div></section>
    ${sub?'<details class="advanced-settings"><summary>API 服务与预算（使用 Claude 订阅时不需要设置）</summary><div class="result-stack">':''}<section class="card key-manager connection-card" id="service-settings"><div class="card-head"><h2>API 服务与模型</h2>${button('connection-add','＋ 添加连接','primary small')}</div>
      <p class="section-note">Provider、API 密钥、阶段模型和计费单价随连接一起管理。</p>
      ${(c.connections||[]).map(p=>`<article class="key-profile"><div><h3>${esc(p.name)} ${p.active?'<span class="chip green">当前使用</span>':''}</h3><p>${p.provider==='openai'?'OpenAI':'Claude'} · ${esc(p.base_url)}</p><small>${['research','script','edit'].map(stage=>`${stageNames[stage]}：${esc(modelName(p.model_defaults?.[stage]))}`).join(' · ')}</small>${p.last_test?`<p class="section-note">最近测试成功：${date(p.last_test.at)}</p>`:''}</div><div class="button-row">${!p.active?button('connection-activate','启用','small',`data-id="${p.id}"`):button('connection-probe','测试连接','small',`data-id="${p.id}"`)}${button('connection-edit','编辑','small ghost',`data-id="${p.id}"`)}${button('connection-delete','删除','small ghost danger',`data-id="${p.id}"`)}</div></article>`).join('')||`<p class="muted">${c.api_configured?'当前使用旧配置或环境变量连接。添加连接后可统一管理模型和计费。':S.textService?.engine==='claude_cli'?'当前使用 Claude 订阅，此处用于配置可选的 API 服务。':'尚未连接 API 服务。添加连接后可开始研究和写稿。'}</p>`}
      <div id="connection-test-result" class="connection-result" role="status"></div></section>
    <section class="card" id="budget-settings"><div class="card-head"><h2>API 预算与用量</h2><span class="chip">${new Date().getMonth()+1} 月</span></div><div class="settings-grid">${groupInput('monthly_budget','月预算（美元）')}${groupInput('max_tokens','单次输出 token 上限')}</div><p>$${S.usage.estimated_usd.toFixed(4)} / $${esc(d.monthly_budget)} · 输入 ${S.usage.input_tokens} / 输出 ${S.usage.output_tokens} token</p><p class="section-note">按对应连接保存的单价估算。任务创建时冻结预算及输出上限。CLI 使用其账号额度，单独记录。</p><div class="button-row end">${button('save-settings','保存预算','primary small','data-group="budget"')}</div></section>${sub?'</div></details>':''}
    </div><aside class="card"><div class="card-head"><h2>本地工具</h2>${button('refresh','刷新','small')}</div>${[['ffmpeg','FFmpeg'],['ffprobe','ffprobe'],['font','中文字体']].map(([k,l])=>`<div class="env-row"><span>${l}</span><span>${S.env[k]?'✓ 已就绪':'○ 未配置'}</span></div>`).join('')}<div class="field"><label>ffmpeg.exe 路径</label><input id="ffmpeg-path" value="${esc(c.ffmpeg_path||'')}"></div><div class="field"><label>ffprobe.exe 路径</label><input id="ffprobe-path" value="${esc(c.ffprobe_path||'')}"></div>${button('save-tool-paths','保存并检测','small')}<p class="section-note">当前版本不使用 ASR，也不自动生成字幕。</p></aside></div>`;
}

async function refreshSettings(savedGroup='') {
  const pending=S.page==='settings'?$$('[data-default]').filter(el=>!savedGroup||!el.closest(`#${savedGroup}-settings`)).map(el=>[el.dataset.default,el.value]):[];
  await refresh();
  if(S.page==='settings')for(const [key,value] of pending){const el=$(`[data-default="${key}"]`);if(el)el.value=value;}
}
