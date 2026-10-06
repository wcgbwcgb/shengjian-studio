'use strict';

function mediaType(a) {return a.type|| (a.kind==='music'?'Music':a.kind==='image'?'Image':/\.(wav|mp3|m4a|flac|aac|ogg)$/i.test(a.path)?'Audio':'Video');}
function managedAsset(a) {
  const type=mediaType(a);
  return `<article class="library-card"><div class="library-preview">${type==='Image'?`<img src="${assetURL(a)}" alt="${esc(a.name)}">`:type==='Video'?`<video preload="metadata" src="${assetURL(a)}#t=0.5" muted></video>`:'<span>♫</span>'}</div><h3>${esc(a.name)}</h3><p>${esc(a.project_name||'当前作品')} · ${type}</p><div class="button-row"><a href="${assetURL(a)}" target="_blank" rel="noopener">查看素材 ↗</a>${button('manage-asset','类型与归属','text-button small',`data-id="${a.id}"`)}</div></article>`;
}
function renderAssetLibrary() {
  const scope=W.libraryScope||'public',type=W.libraryType||'',q=(W.libraryQuery||'').toLowerCase();
  const list=S.library.assets.filter(a=>(a.scope||'project')===scope&&(!type||mediaType(a)===type)&&(a.name+' '+(a.project_name||'')).toLowerCase().includes(q));
  return `<div class="collection-shell">${heading('素材库','公共素材可以添加到任何作品；作品素材会放进对应工作文件夹的「素材」目录。','YOUR CREATIVE LIBRARY')}<div class="library-toolbar"><div class="view-switch">${button('library-scope','公共素材库',scope==='public'?'selected':'','data-scope="public"')}${button('library-scope','作品素材',scope==='project'?'selected':'','data-scope="project"')}</div><input id="library-search" type="search" aria-label="搜索素材" placeholder="搜索素材或作品" value="${esc(W.libraryQuery||'')}"><select id="library-type" aria-label="筛选素材类型"><option value="">全部类型</option>${['Video','Image','Audio','Music'].map(t=>`<option ${t===type?'selected':''}>${t}</option>`).join('')}</select></div>${scope==='public'?`<div class="asset-upload-controls">${button('public-upload','＋ 上传公共素材','small')}</div>`:''}<div class="library-grid">${list.map(managedAsset).join('')}</div>${!list.length?empty('▧','这里还没有素材',scope==='public'?'在此上传，素材将归属公共库。':'作品里上传的素材会出现在这里。'):''}<input id="public-assets" type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden></div>`;
}
function manageAssetModal(id) {
  const a=S.library.assets.find(a=>a.id===id);
  if(!a)throw new Error('素材不存在，请刷新');
  modal('素材类型与归属',`<div class="field"><label>名称</label><input id="asset-name" value="${esc(a.name)}" maxlength="200"></div><div class="field"><label>素材类型</label><select id="asset-type">${['Video','Image','Audio','Music'].map(t=>`<option ${mediaType(a)===t?'selected':''}>${t}</option>`).join('')}</select></div><div class="field"><label>归属</label><select id="asset-owner"><option value="" ${a.scope==='public'?'selected':''}>公共素材库</option>${S.projects.map(p=>`<option value="${p.id}" ${p.id===a.project_id?'selected':''}>作品：${esc(p.name)}</option>`).join('')}</select></div><p class="section-note">移动后素材文件不变，已经做好的视频不受影响。</p>`,button('save-asset','保存','primary',`data-id="${id}"`));
}
Object.assign(HANDLERS, {
  'library-scope':el=>{W.libraryScope=el.dataset.scope;render();},
  'public-upload':()=>$('#public-assets').click(),
  'manage-asset':el=>manageAssetModal(el.dataset.id),
  'save-asset':async el=>{const owner=$('#asset-owner').value;await api(`/assets/${el.dataset.id}`,{name:$('#asset-name').value,type:$('#asset-type').value,scope:owner?'project':'public',project_id:owner||null},'PATCH');$('#modal').close();await refresh();toast('素材类型与归属已保存');},
});
document.addEventListener('input',event=>{
  const el=event.target;
  if(el.id==='library-search'){W.libraryQuery=el.value;const position=el.selectionStart;render();const input=$('#library-search');input.focus();input.setSelectionRange(position,position);}
});
document.addEventListener('change',async event=>{
  const el=event.target;
  try{
    if(el.id==='library-type'){W.libraryType=el.value;render();}
    if(el.id==='public-assets'&&el.files.length){for(const file of el.files){const form=new FormData();form.append('file',file);await api('/library/assets',form);}await refresh();toast('公共素材已保存');}
  }catch(error){toast(error.message,true);}
});
