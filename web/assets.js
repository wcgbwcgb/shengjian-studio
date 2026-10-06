'use strict';

function mediaType(a) {return a.type|| (a.kind==='music'?'Music':a.kind==='image'?'Image':/\.(wav|mp3|m4a|flac|aac|ogg)$/i.test(a.path)?'Audio':'Video');}
function assetUploadControl(scope='project') {
  return `<div class="asset-upload-controls"><label>上传类型 <select id="${scope}-upload-type"><option value="">按文件格式识别</option>${['Video','Image','Audio','Music'].map(t=>`<option>${t}</option>`).join('')}</select></label>${vbutton(scope==='public'?'public-upload':'upload',scope==='public'?'＋ 上传公共素材':'＋ 上传项目素材','small')}</div>`;
}
function managedAsset(a) {
  const frame=a.analysis?.frames?.[0];
  return `<article class="library-card"><div class="library-preview">${mediaType(a)==='Image'?`<img src="${assetURL(a)}" alt="${esc(a.name)}">`:frame?`<img src="/api/assets/${a.id}/frames/0" alt="${esc(a.name)}">`:`<span>${mediaType(a)==='Music'||mediaType(a)==='Audio'?'♫':'▷'}</span>`}</div><h3>${esc(a.name)}</h3><p>${esc(a.project_name||'当前项目')} · ${mediaType(a)}${mediaType(a)==='Music'?' · 完整保护':''}</p><div class="button-row"><a href="${assetURL(a)}" target="_blank" rel="noopener">查看素材 ↗</a>${vbutton('manage-asset','类型与归属','text-button small',`data-id="${a.id}"`)}</div></article>`;
}
function projectMaterials() {
  const own=S.detail.assets.filter(a=>!a.generated);
  return `<section class="card project-materials"><div class="card-head"><h2>项目素材 <span class="muted">${own.length}</span></h2>${vbutton('public-picker','从公共库添加','small')}</div>${assetUploadControl()}<div class="project-asset-list">${own.map(a=>`<div class="asset-row"><div class="asset-info"><strong>${esc(a.name)}</strong><small>${mediaType(a)}${mediaType(a)==='Music'?' · 完整保护':''}</small></div>${a.analysis?button('inspect-asset','查看信息','small',`data-id="${a.id}"`):''}${vbutton('manage-asset','管理','small',`data-id="${a.id}"`)}</div>`).join('')||'<p class="muted">上传素材，或从公共素材库添加。</p>'}</div></section>`;
}
function renderAssetLibrary() {
  const scope=W.libraryScope||'public',type=W.libraryType||'',q=(W.libraryQuery||'').toLowerCase();
  const list=S.library.assets.filter(a=>(a.scope||'project')===scope&&(!type||mediaType(a)===type)&&(a.name+' '+(a.project_name||'')).toLowerCase().includes(q));
  return `<div class="collection-shell">${heading('素材库','归属与类型分别管理。Music 类型始终完整保护。',false,'YOUR CREATIVE LIBRARY')}<div class="library-toolbar"><div class="view-switch">${vbutton('library-scope','公共素材库',scope==='public'?'selected':'','data-scope="public"')}${vbutton('library-scope','项目素材',scope==='project'?'selected':'','data-scope="project"')}</div><input id="library-search" type="search" aria-label="搜索素材" placeholder="搜索素材或项目" value="${esc(W.libraryQuery||'')}"><select id="library-type" aria-label="筛选素材类型"><option value="">全部类型</option>${['Video','Image','Audio','Music'].map(t=>`<option ${t===type?'selected':''}>${t}</option>`).join('')}</select></div>${scope==='public'?assetUploadControl('public'):''}<div class="library-grid">${list.map(managedAsset).join('')}</div>${!list.length?empty('▧','这里还没有素材',scope==='public'?'在此上传，素材将默认归属公共库。':'项目内上传的素材会归属对应项目。'):''}<input id="public-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></div>`;
}
function manageAssetModal(id) {
  const a=S.library.assets.find(a=>a.id===id)||S.detail?.assets.find(a=>a.id===id);
  if(!a)throw new Error('素材不存在，请刷新');
  modal('素材类型与归属',`<div class="field"><label>名称</label><input id="asset-name" value="${esc(a.name)}" maxlength="200"></div><div class="field"><label>素材类型</label><select id="asset-type">${['Video','Image','Audio','Music'].map(t=>`<option ${mediaType(a)===t?'selected':''}>${t}</option>`).join('')}</select></div><div class="field"><label>归属</label><select id="asset-owner"><option value="" ${a.scope==='public'?'selected':''}>公共素材库</option>${S.projects.map(p=>`<option value="${p.id}" ${p.id===a.project_id?'selected':''}>项目：${esc(p.name)}</option>`).join('')}</select></div><p class="section-note">移动保留素材 ID 和文件，旧视频与版本记录继续可用。Music 类型自动完整保护。</p>`,vbutton('save-asset','保存','primary',`data-id="${id}"`));
}
