(() => {
  const root = document.querySelector('#visual-memory');
  if (!root) return;
  const $ = (s, parent = root) => parent.querySelector(s);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeURL = value => { if (!value) return ''; try { const u = new URL(value, location.href); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };
  const identityKinds = JSON.parse(root.dataset.identityKinds || '[]');
  const kindSelect = () => `<label>Type<select class="form-select" name="kind" required><option value="">Choose a type</option>${identityKinds.map(kind => `<option value="${esc(kind.value)}">${esc(kind.label)}</option>`).join('')}</select></label>`;
  const state = { page: 0, size: 20, total: 0, image: null, width: 0, height: 0, boxes: [], sequence: 0 };
  const message = (text, error = false) => { const el = $('#vm-message'); el.textContent = text; el.className = error ? 'vm-error' : ''; el.setAttribute('role', error ? 'alert' : 'status'); const dialogMessage = $('#vm-dialog-message'); if (dialogMessage) { dialogMessage.textContent = !$('#vm-dialog').hidden && error ? text : ''; dialogMessage.className = error ? 'vm-error' : ''; } };
  async function api(path, body, method = 'POST') {
    const options = { credentials: 'same-origin' };
    if (body !== undefined) {
      const tokenResponse = await fetch('/auth/csrf', {credentials: 'same-origin'});
      if (!tokenResponse.ok) throw new Error('Could not obtain request verification. Sign in and retry.');
      const token = (await tokenResponse.json()).csrf_token;
      Object.assign(options, {method, headers: {'Content-Type':'application/json', 'X-CSRF-Token': token}, body: JSON.stringify(body)});
    }
    const response = await fetch('/admin/visual-memory' + path, options);
    if (!response.ok) { let detail = ''; try { detail = (await response.json()).detail; } catch {} throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`); }
    return response.json();
  }
  let working = false;
  async function busy(button, action) {
    if (working || button.disabled) return;
    working = true;
    const controls = [...root.querySelectorAll('button, input, select')];
    if (!controls.includes(button)) controls.push(button);
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    button.setAttribute('aria-busy', 'true');
    try { await action(); } catch (e) { message(e.message || 'Request failed. Please retry.', true); }
    finally {
      controls.forEach((control, i) => { control.disabled = disabled[i]; });
      button.removeAttribute('aria-busy'); working = false;
      $('#vm-prev').disabled = state.page === 0;
      $('#vm-next').disabled = (state.page + 1) * state.size >= state.total;
      $('#vm-detect').disabled = !state.image; $('#vm-add-box').disabled = !state.image;
      $('#vm-identify').disabled = !state.boxes.length;
      for (const control of root.querySelectorAll('[data-page-disabled]')) control.disabled = control.dataset.pageDisabled === 'true';
    }
  }
  const field = (name, label, value = '', type = 'text') => `<label>${esc(label)}<input class="form-control" name="${name}" type="${type}" value="${esc(value)}"></label>`;
  let previousFocus, submitAction, dialogGeneration = 0;
  function closeDialog() { ++dialogGeneration; $('#vm-dialog').hidden = true; document.body.style.overflow = ''; previousFocus?.focus(); }
  function dialog(title, html, action, label = 'Save') {
    ++dialogGeneration;
    if ($('#vm-dialog').hidden) previousFocus = document.activeElement; submitAction = action; $('#vm-dialog-message').textContent = '';
    $('#vm-dialog-title').textContent = title; $('#vm-dialog-body').innerHTML = html;
    $('#vm-dialog-body').onclick = null; $('#vm-dialog-submit').textContent = label; $('#vm-dialog').hidden = false; document.body.style.overflow = 'hidden';
    $('button[data-close]', $('#vm-dialog')).focus();
  }
  $('#vm-dialog').addEventListener('click', e => { if (!working && e.target.hasAttribute('data-close')) closeDialog(); });
  $('#vm-dialog').addEventListener('keydown', e => {
    if (e.key === 'Escape' && !working) { e.preventDefault(); closeDialog(); }
    if (e.key === 'Tab') { const els = [...$('#vm-dialog-panel, .vm-dialog-panel').querySelectorAll('input, select, textarea, button')].filter(x => !x.disabled); const first = els[0], last = els.at(-1); if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
  });
  $('#vm-dialog-submit').addEventListener('click', e => busy(e.currentTarget, async () => { await submitAction($('#vm-dialog-body')); closeDialog(); }));
  async function modelStatus() {
    const el = $('#vm-model-status'); el.textContent = 'Checking model status…';
    try {
      const models = await api('/models');
      const describe = (label, model) => `${label}: ${model?.available === true ? 'available' : model?.available === false ? 'unavailable' : 'not verified'}${model?.reason ? ' — ' + model.reason : ''}`;
      el.textContent = `${describe('Embedder', models.embedding)} · ${describe('Detector', models.detection)}. Manual boxes remain available without the detector.`;
    } catch (e) { el.textContent = `Model status unavailable: ${e.message}. You can still add boxes manually.`; }
  }
  root.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    for (const b of root.querySelectorAll('[data-tab]')) { const active = b === button; b.setAttribute('aria-pressed', active); b.className = active ? 'btn btn-primary' : 'btn btn-secondary'; }
    $('#vm-library').hidden = button.dataset.tab !== 'library'; $('#vm-lab').hidden = button.dataset.tab !== 'lab'; $('#vm-cutscenes').hidden = button.dataset.tab !== 'cutscenes'; message(''); if (button.dataset.tab === 'lab') modelStatus(); if (button.dataset.tab === 'cutscenes') videoReview?.open();
  }));
  function linksText(links) { return (links || []).map(l => `${l.identity_id}${l.appearance_id ? ':' + l.appearance_id : ''}`).join(', '); }
  function parseLinks(text) {
    if (!text.trim()) return [];
    return text.split(',').map(part => { const m = part.trim().match(/^(\d+)(?::(\d+))?$/); if (!m || +m[1] < 1 || (m[2] && +m[2] < 1)) throw new Error('Use identity IDs separated by commas; add :appearance ID when needed.'); return {identity_id: +m[1], appearance_id: m[2] ? +m[2] : null}; });
  }
  const linksHelp = 'Identity IDs, separated by commas. For an appearance use identity ID:appearance ID. Leave empty for unresolved.';
  async function load() {
    const seq = ++state.sequence, params = new URLSearchParams(new FormData($('#vm-filters')));
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    params.set('limit', state.size); params.set('offset', state.page * state.size);
    $('#vm-cards').innerHTML = '<div class="vm-skeleton" role="status" aria-label="Loading references"></div>'.repeat(4);
    try {
      const data = await api('/references?' + params); if (seq !== state.sequence) return;
      state.total = data.total; const items = data.items || [];
      if (!items.length && state.page > 0) { state.page--; return load(); }
      $('#vm-cards').innerHTML = items.length ? items.map(card).join('') : '<div class="vm-empty">No references match these filters.</div>';
      $('#vm-page').textContent = `${state.total ? state.page * state.size + 1 : 0}–${Math.min((state.page + 1) * state.size, state.total)} of ${state.total}`;
      $('#vm-prev').disabled = state.page === 0; $('#vm-next').disabled = (state.page + 1) * state.size >= state.total;
    } catch (e) { if (seq === state.sequence) { $('#vm-cards').innerHTML = '<div class="vm-empty">Could not load references. Apply filters to retry.</div>'; message(e.message, true); } }
  }
  function card(item) {
    const url = safeURL(item.image_url || item.thumbnail_url || '');
    const source = safeURL(item.source_page || item.source_url || '');
    const links = (item.links || []).map(l => esc((l.name || l.identity_name || `Identity ${l.identity_id}`) + (l.kind ? ` · ${l.kind}` : '')) + (l.appearance_name ? ` · ${esc(l.appearance_name)}` : l.appearance_id ? ` · appearance ${esc(l.appearance_id)}` : '')).join(', ') || 'Unresolved';
    return `<article class="vm-card">${url ? `<img src="${esc(url)}" alt="Reference ${esc(item.id)}" loading="lazy">` : '<div class="vm-empty">No stored preview</div>'}<h3>${links}</h3><div class="vm-meta"><div>Reference #${esc(item.id)} · ${esc(item.status)}</div><div>${esc(item.source_type || 'Unknown source')} · ${esc((item.model_keys || []).join(', ') || item.model_key || 'Model unavailable')}</div><div>Quality: ${esc(item.quality ?? 'Not recorded')} · Search: ${item.enabled_for_search ? 'enabled' : 'disabled'}</div><div>Retention: ${esc(item.retention_mode || 'unknown')}</div>${source ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">Open source</a>` : ''}</div><div class="vm-actions"><button class="btn btn-secondary" data-action="details" data-id="${esc(item.id)}">Review</button></div></article>`;
  }
  $('#vm-filters').addEventListener('submit', e => { e.preventDefault(); const value = $('[name=quality_min]', $('#vm-filters')).value; if (value !== '' && (!Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 1)) { message('Minimum quality must be between 0 and 1.', true); return; } message(''); state.page = 0; load(); });
  $('#vm-filters').addEventListener('reset', () => { state.page = 0; setTimeout(load, 0); });
  $('#vm-prev').addEventListener('click', () => { state.page--; load(); }); $('#vm-next').addEventListener('click', () => { state.page++; load(); });
  $('#vm-cards').addEventListener('click', e => { const b = e.target.closest('[data-action]'); if (b) busy(b, async () => { const [ref, history] = await Promise.all([api('/references/' + b.dataset.id), api('/references/' + b.dataset.id + '/history')]); ref.history = history.items; showReference(ref); }); });
  function showReference(ref) {
    const id = ref.id;
    const sourcePage = safeURL(ref.source_page), sourceImage = safeURL(ref.source_url);
    const sourceLinks = `${sourcePage ? `<a class="btn btn-secondary" href="${esc(sourcePage)}" target="_blank" rel="noopener noreferrer">Open source page</a>` : ''}${sourceImage ? `<a class="btn btn-secondary" href="${esc(sourceImage)}" target="_blank" rel="noopener noreferrer">Open source image</a>` : ''}`;
    const history = (ref.history || ref.reviews || []).map(h => `<li>${esc(h.created_at || '')} · ${esc(h.action || h.status)} · actor ${esc(h.actor_id ?? 'unknown')} · ${esc(h.reason || '')}</li>`).join('');
    dialog(`Reference #${id}`, `<p>Status: ${esc(ref.status)} · Search: ${ref.enabled_for_search ? 'enabled' : 'disabled'}</p><p>${esc(linksHelp)} Saving links replaces all existing links and returns this reference to pending review.</p><div class="vm-actions">${sourceLinks || '<span class="vm-meta">No source link recorded.</span>'}</div><p>Author: ${esc(ref.author || 'Not recorded')} · Quality: ${esc(ref.quality ?? 'Not recorded')} · Rights: ${esc(ref.rights || 'Not recorded')} · Last embedded: ${esc(ref.embedded_at || 'Not recorded')} · SHA-256: ${esc(ref.sha256 || 'Not recorded')}</p>${field('links', 'Links', linksText(ref.links))}<div class="vm-actions"><button class="btn btn-secondary" data-op="choose">Find identities</button><button class="btn btn-secondary" data-op="links">Save links</button><button class="btn btn-secondary" data-op="approve">Approve</button><button class="btn btn-secondary" data-op="reject">Reject</button><button class="btn btn-secondary" data-op="search">${ref.enabled_for_search ? 'Disable' : 'Enable'} search</button><button class="btn btn-secondary" data-op="vector">Keep vector only</button><button class="btn btn-secondary" data-op="reindex">Reindex</button></div><details><summary>History</summary>${history ? `<ul>${history}</ul>` : '<p>No history available.</p>'}</details>`, async () => {}, 'Close');
    $('#vm-dialog-body').onclick = e => {
      const b = e.target.closest('[data-op]'); if (!b) return;
      const op = b.dataset.op;
      if (op === 'choose') { const links = parseLinks($('[name=links]', $('#vm-dialog-body')).value); chooseLinks(null, {links, names:{}}, async selected => { await api(`/references/${id}/links`, {links:selected}); message('Links saved; reference returned to pending review.'); load(); }); return; }
      if (op === 'reject' || op === 'vector') {
        const text = op === 'reject' ? 'Reject this reference and exclude it from search. Record why it is unsuitable.' : 'Unlink stored images from this reference and retain the vector and metadata. Shared files may remain in storage until garbage collection; this cannot be undone here.';
        dialog(op === 'reject' ? 'Reject reference' : 'Keep vector only', `<p>${text}</p>${field('reason', 'Reason')}`, async body => {
          const reason = $('[name=reason]', body).value.trim(); if (op === 'reject' && !reason) throw new Error('Enter a rejection reason.');
          await api(`/references/${id}/remove`, {mode: op === 'reject' ? 'reject' : 'vector_only', reason: reason || null}); message('Reference updated.'); load();
        }, op === 'reject' ? 'Reject reference' : 'Remove image'); return;
      }
      busy(b, async () => {
        if (op === 'links') await api(`/references/${id}/links`, {links: parseLinks($('[name=links]', $('#vm-dialog-body')).value)});
        if (op === 'approve') await api(`/references/${id}/review`, {status:'approved', enabled_for_search:true, reason:null});
        if (op === 'search' && ref.status !== 'approved') throw new Error('Approve this reference before enabling search.');
        if (op === 'search') await api(`/references/${id}/review`, {status:ref.status, enabled_for_search:!ref.enabled_for_search, reason:null});
        if (op === 'reindex') { const result = await api('/references/reindex', {reference_ids:[id]}); const outcome = result.items?.[0]; if (outcome?.status !== 'indexed') throw new Error('Reindex did not complete: ' + (outcome?.status || 'unknown result')); }
        message('Reference updated.'); closeDialog(); load();
      });
    };
  }
  function createIdentity(onCreated) {
    dialog('Create visual identity', `<p>Link an existing game entry or leave game IDs empty for a standalone identity. Use Other for subjects outside the listed types.</p>${field('name','Name')}${kindSelect()}${field('nodes','Existing game node IDs (comma-separated)')}<label>Existing lore group<select class="form-select" name="lore"><option value="">None</option></select></label>${field('lore_name','New lore group name (optional)')}<div class="vm-actions"><button class="btn btn-secondary" data-create-lore>Create lore group</button></div><p id="vm-created-lore"></p>`, async body => {
      const name = $('[name=name]',body).value.trim(), kind = $('[name=kind]',body).value.trim(), nodes = $('[name=nodes]',body).value.trim(), lore = $('[name=lore]',body).value;
      if (!name || !kind || (nodes && !/^\d+(\s*,\s*\d+)*$/.test(nodes))) throw new Error('Enter a name, kind and valid comma-separated node IDs.');
      if (!identityKinds.some(option => option.value === kind)) throw new Error('Choose a type from the list.');
      const result = await api('/identities', {name, kind, node_ids:nodes ? nodes.split(',').map(Number) : [], lore_id:lore ? Number(lore) : null, description:''});
      message(`Created identity ${result.name || result.id}.`); if (onCreated) onCreated(result);
    }, 'Create identity');
    api('/lore-identities').then(result => { const select = $('[name=lore]', $('#vm-dialog-body')); if (select) select.insertAdjacentHTML('beforeend', (result.items || []).map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')); }).catch(e => message(e.message, true));
    $('[data-create-lore]').addEventListener('click', e => busy(e.currentTarget, async () => {
      const name = $('[name=lore_name]', $('#vm-dialog-body')).value.trim(); if (!name) throw new Error('Enter a name for the new lore group.');
      const result = await api('/lore-identities', {name}); const select = $('[name=lore]', $('#vm-dialog-body')); select.insertAdjacentHTML('beforeend', `<option value="${esc(result.id)}">${esc(result.name)}</option>`); select.value = result.id; $('#vm-created-lore').textContent = `Created lore group ${result.name}.`;
    }));
  }
  $('#vm-create-identity').addEventListener('click', () => createIdentity());
  function validBox(box) { return visualMemoryBbox.valid(box, state.width, state.height); }
  function addBox(bbox, data = {}) { if (state.boxes.length >= 20) throw new Error('A maximum of 20 boxes can be identified at once.'); state.boxes.push({bbox, data, links: [], names: {}}); }
  function renderBoxes() {
    $('#vm-identify').disabled = !state.boxes.length;
    $('#vm-overlay').innerHTML = visualMemoryBbox.bounds(state.boxes, state.width, state.height);
    $('#vm-boxes').innerHTML = state.boxes.map((b, i) => {
      const d = b.data.detection || {}, identity = b.data.identification || {}, candidates = identity.candidates || [];
      return `<article class="vm-box" id="vm-box-${i}"><div class="vm-box-head"><h3>Object ${i+1}</h3><button class="btn btn-secondary" data-delete="${i}">Delete box</button></div><div class="vm-meta">Detection: ${esc(d.status || 'manual')} ${d.score == null ? '' : '· score ' + esc(d.score)} · Identification: ${esc(identity.status || 'unresolved')}</div><div class="vm-coordinates">${['Left x','Top y','Right x','Bottom y'].map((label, n) => `<label>${label}<input class="form-control" type="number" min="0" step="1" data-coordinate="${n}" data-index="${i}" value="${esc(b.bbox[n])}"></label>`).join('')}</div><canvas data-crop="${i}" aria-label="Crop preview for object ${i+1}"></canvas><div class="vm-candidates">${candidates.slice(0,10).map(c => `<div class="vm-candidate"><strong>${esc(c.name || 'Unnamed')} · ${esc(c.kind || '')}</strong> <span>Similarity score ${esc(c.score)}</span> <button class="btn btn-secondary" data-candidate="${i}" data-identity="${esc(c.identity_id)}" data-appearance="${esc(c.appearance_id || '')}" data-name="${esc(c.name || '')}" data-kind="${esc(c.kind || '')}">Add link</button><div class="vm-evidence">${(c.evidence || []).map(ev => { const url = safeURL(ev.image_url); return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"><img src="${esc(url)}" alt="Evidence ${esc(ev.reference_id)}, similarity ${esc(ev.similarity)}" loading="lazy"></a>` : `<span class="vm-meta">Reference #${esc(ev.reference_id)} · ${esc(ev.similarity)} (no preview)</span>`; }).join('')}</div></div>`).join('') || '<p class="vm-meta">No candidate evidence. The object may remain unresolved.</p>'}</div><p>Selected links: ${esc(b.links.map(l => (b.names[l.identity_id] || `Identity ${l.identity_id}`) + (l.appearance_id ? ` · appearance ${l.appearance_id}` : '')).join(', ') || 'None — unresolved')}</p><div class="vm-actions"><button class="btn btn-secondary" data-choose="${i}">Choose identities / appearances</button><button class="btn btn-secondary" data-clear="${i}">Clear links</button><button class="btn btn-primary" data-save="${i}">Save crop</button></div></article>`;
    }).join('');
    for (const canvas of root.querySelectorAll('[data-crop]')) { const box = state.boxes[+canvas.dataset.crop].bbox; if (!validBox(box)) continue; const scale = Math.min(1, 180 / (box[2]-box[0]), 120 / (box[3]-box[1])); canvas.width = Math.max(1, Math.round((box[2]-box[0])*scale)); canvas.height = Math.max(1, Math.round((box[3]-box[1])*scale)); canvas.getContext('2d').drawImage($('#vm-image'), box[0],box[1],box[2]-box[0],box[3]-box[1],0,0,canvas.width,canvas.height); }
  }
  let uploadGeneration = 0;
  $('#vm-file').addEventListener('change', async e => {
    const generation = ++uploadGeneration;
    const file = e.target.files[0]; state.image = null; state.boxes = []; $('#vm-stage').hidden = true; $('#vm-detect').disabled = true; $('#vm-add-box').disabled = true; $('#vm-identify').disabled = true; $('#vm-boxes').innerHTML = '';
    if (!file) return;
    if (!file.type.startsWith('image/') || file.size > 5*1024*1024) { message('Choose an image no larger than 5 MiB.', true); return; }
    try {
      const dataURL = await new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Could not read image.')); reader.readAsDataURL(file); });
      if (generation !== uploadGeneration) return;
      const img = $('#vm-image'); img.src = dataURL; await img.decode(); if (generation !== uploadGeneration) return; state.width = img.naturalWidth; state.height = img.naturalHeight; state.image = dataURL.split(',')[1]; $('#vm-stage').hidden = false; $('#vm-detect').disabled = false; $('#vm-add-box').disabled = false; message('Image ready. Detect objects or add a box manually.');
    } catch { if (generation === uploadGeneration) message('Could not read this image.', true); }
  });
  async function identify(detect) {
    if (!state.image) return;
    if (!detect && state.boxes.some(b => !validBox(b.bbox))) throw new Error('Each box must fit within the image and have positive width and height.');
    const data = await api('/identify', {image_base64:state.image, boxes:detect ? null : state.boxes.map(b => b.bbox), detect, limit:10, kind:null});
    const detections = data.detections || [];
    if (detect) { state.boxes = []; for (const d of detections) if (validBox(d.bbox)) addBox(d.bbox, d); }
    else { for (let i = 0; i < state.boxes.length; i++) state.boxes[i].data = detections[i] || {}; }
    renderBoxes(); message(detections.length ? `Found ${detections.length} object${detections.length === 1 ? '' : 's'}. Similarity scores are not probabilities.` : 'No objects detected. Add a box manually.');
  }
  $('#vm-detect').addEventListener('click', e => busy(e.currentTarget, () => identify(true)));
  $('#vm-identify').addEventListener('click', e => busy(e.currentTarget, () => identify(false)));
  $('#vm-add-box').addEventListener('click', () => { try { addBox([0,0,state.width,state.height]); renderBoxes(); } catch(e) { message(e.message, true); } });
  visualMemoryBbox.bind({overlay:$('#vm-overlay'), image:$('#vm-image'), dimensions:()=>({width:state.width,height:state.height}), boxes:()=>state.boxes, disabled:()=>working, render:renderBoxes,
    onAdd:box=>{ try { addBox(box); } catch(err) { message(err.message, true); } },
    onResize:(index,next)=>{ const b=state.boxes[index]; b.bbox=next; b.data={}; b.links=[]; b.names={}; message('Box updated. Identify again to refresh candidates.'); }});
  $('#vm-overlay').addEventListener('click', e => { const b = e.target.closest('[data-index]'); if (b) $(`#vm-box-${b.dataset.index}`)?.scrollIntoView({block:'nearest'}); });
  $('#vm-boxes').addEventListener('change', e => {
    const input = e.target.closest('[data-coordinate]'); if (!input) return; const box = state.boxes[+input.dataset.index]; const next = box.bbox.slice(); next[+input.dataset.coordinate] = Number(input.value);
    if (!input.value || !validBox(next)) { message('Box coordinates must fit the image and have positive width and height.', true); input.value = box.bbox[+input.dataset.coordinate]; return; }
    box.bbox = next; box.data = {}; box.links = []; box.names = {}; renderBoxes(); message('Box updated. Identify again to refresh candidates.');
  });
  $('#vm-boxes').addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.delete !== undefined) { state.boxes.splice(+button.dataset.delete,1); renderBoxes(); }
    if (button.dataset.clear !== undefined) { state.boxes[+button.dataset.clear].links = []; state.boxes[+button.dataset.clear].names = {}; renderBoxes(); }
    if (button.dataset.candidate !== undefined) { const b = state.boxes[+button.dataset.candidate], id = +button.dataset.identity, appearance = button.dataset.appearance ? +button.dataset.appearance : null; if (!b.links.some(l => l.identity_id === id)) b.links.push({identity_id:id, appearance_id:appearance}); b.names[id] = [button.dataset.name, button.dataset.kind].filter(Boolean).join(' · '); renderBoxes(); }
    if (button.dataset.choose !== undefined) chooseLinks(+button.dataset.choose);
    if (button.dataset.save !== undefined) saveCrop(+button.dataset.save);
  });
  async function chooseLinks(index, suppliedBox = null, onSave = null) {
    const box = suppliedBox || state.boxes[index];
    dialog('Choose identities and appearances', `<p>Search your visual identities and the game catalog. Adding a game character creates or reuses its visual identity.</p><p id="vm-selected-identities"></p>${field('query','Search identities and game characters')}<div id="vm-identity-results" aria-live="polite"></div><div class="vm-actions"><button type="button" class="btn btn-secondary" data-identity-prev disabled>Previous</button><span id="vm-identity-page"></span><button type="button" class="btn btn-secondary" data-identity-next disabled>Next</button></div><details><summary>Technical details</summary><p>${esc(linksHelp)}</p>${field('links','Selected identity IDs',linksText(box.links))}<button type="button" class="btn btn-secondary" data-new>Create custom identity</button><p>To create an appearance, enter its identity ID and name.</p>${field('appearance_identity','Identity ID', '', 'number')}${field('appearance_name','Appearance name')}<button type="button" class="btn btn-secondary" data-appearance-create>Create appearance</button><p id="vm-appearance-result"></p></details>`, async body => { box.links = parseLinks($('[name=links]',body).value); if (onSave) await onSave(box.links); else renderBoxes(); });
    const body = $('#vm-dialog-body'), generation = dialogGeneration;
    let offset = 0, searchSequence = 0, timer;
    const queryInput = $('[name=query]', body), linksInput = $('[name=links]', body);
    function selectedNames() { const links = parseLinks(linksInput.value); $('#vm-selected-identities').innerHTML = 'Selected: ' + (links.map(l => `<button type="button" class="btn btn-secondary" data-remove-identity="${esc(l.identity_id)}" aria-label="Remove ${esc(box.names[l.identity_id] || `Identity ${l.identity_id}`)}">${esc(box.names[l.identity_id] || `Identity ${l.identity_id}`)} ×</button>`).join(' ') || 'None'); }
    function addLink(id, appearance, name) {
      const links = parseLinks(linksInput.value), selected = {identity_id:id, appearance_id:appearance || null};
      const existing = links.findIndex(l => l.identity_id === id);
      if (existing < 0) links.push(selected); else links[existing] = selected;
      box.names[id] = name || `Identity ${id}`; linksInput.value = linksText(links); selectedNames();
    }
    selectedNames();
    async function findIdentities() {
      const sequence = ++searchSequence, query = queryInput.value.trim();
      const results = $('#vm-identity-results');
      results.innerHTML = '<div class="vm-skeleton" role="status" aria-label="Searching identities"></div>';
      const params = new URLSearchParams({q:query,limit:'20',offset:String(offset)});
      const [visual, game] = await Promise.allSettled([api('/identities?' + params), api('/game-entities?' + params)]);
      if (generation !== dialogGeneration || sequence !== searchSequence) return;
      const visualItems = visual.status === 'fulfilled' ? visual.value.items || [] : [];
      const gameItems = game.status === 'fulfilled' ? game.value.items || [] : [];
      const hasMore = [visual, game].some(result => result.status === 'fulfilled' && (result.value.has_more === true || (Number.isFinite(result.value.total) && offset + 20 < result.value.total)));
      const visualRows = visualItems.map(x => `<div class="vm-picker-row"><div><strong>${esc(x.name)}</strong><span class="vm-meta"> ${esc(x.kind || '')}</span></div><div class="vm-actions"><button type="button" class="btn btn-secondary" data-add-identity="${esc(x.id)}" data-name="${esc(x.name)}" data-kind="${esc(x.kind || '')}">Add ${esc(x.name)}</button>${(x.appearances || []).map(a => `<button type="button" class="btn btn-secondary" data-add-identity="${esc(x.id)}" data-add-appearance="${esc(a.id)}" data-appearance-name="${esc(a.name)}" data-name="${esc(x.name)}" data-kind="${esc(x.kind || '')}">${esc(a.name)}</button>`).join('')}</div></div>`).join('');
      const gameRows = gameItems.map(x => { const name=x.name || x.display_name || 'Unnamed', image=safeURL(x.thumbnail_url || x.image_url || x.thumbnail), choices=(x.appearance_choices || []).filter(a => a.key === 'male' || a.key === 'female'); return `<div class="vm-picker-row">${image ? `<img src="${esc(image)}" alt="" loading="lazy">` : ''}<div><strong>${esc(name)}</strong><span class="vm-meta"> ${esc(x.kind || '')}</span>${choices.length ? '<p class="vm-meta">Rover is one identity with two appearances; elements are not appearances.</p>' : ''}</div><div class="vm-actions"><button type="button" class="btn btn-secondary" data-add-game="${esc(x.node_id)}" data-name="${esc(name)}">Add ${esc(name)}</button>${choices.map(a => `<button type="button" class="btn btn-secondary" data-add-game="${esc(x.node_id)}" data-name="${esc(name)}" data-appearance-name="${esc(a.name)}">${esc(a.name)}</button>`).join('')}</div></div>`; }).join('');
      results.innerHTML = '<h3>Visual identities</h3>' + (visual.status === 'rejected' ? '<p class="vm-error">Visual identities could not be loaded. Search again to retry.</p>' : visualRows || '<p class="vm-meta">No visual identities found. You can add a character from the game catalog below.</p>') + '<h3>Game catalog</h3>' + (game.status === 'rejected' ? '<p class="vm-error">Game catalog could not be loaded. Search again to retry.</p>' : gameRows || '<p class="vm-meta">No game characters match this search.</p>');
      $('#vm-identity-page').textContent = `Page ${Math.floor(offset / 20) + 1}${hasMore ? ' · More results available' : ''}`;
      for (const [selector, disabled] of [['[data-identity-prev]',offset === 0],['[data-identity-next]',!hasMore]]) { const button=$(selector,body); button.dataset.pageDisabled=String(disabled); button.disabled=disabled; }
    }
    queryInput.addEventListener('input', () => { clearTimeout(timer); ++searchSequence; offset = 0; timer = setTimeout(() => findIdentities(), 250); });
    queryInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); offset = 0; findIdentities(); } });
    $('[data-identity-prev]',body).addEventListener('click', () => { offset = Math.max(0,offset-20); findIdentities(); });
    $('[data-identity-next]',body).addEventListener('click', () => { offset += 20; findIdentities(); });
    body.onclick = e => {
      const b=e.target.closest('[data-add-identity], [data-add-game], [data-remove-identity]'); if (!b) return;
      if (b.dataset.removeIdentity !== undefined) { linksInput.value = linksText(parseLinks(linksInput.value).filter(l => l.identity_id !== +b.dataset.removeIdentity)); selectedNames(); return; }
      if (b.dataset.addGame !== undefined) { busy(b, async () => { const result=await api('/identities/from-game',{node_id:+b.dataset.addGame}); if (generation !== dialogGeneration) return; const appearance=b.dataset.appearanceName ? (result.appearances || []).find(a => a.name === b.dataset.appearanceName && +a.identity_id === +result.id) : null; if (b.dataset.appearanceName && !appearance) throw new Error('The selected appearance is unavailable. Retry or choose another appearance.'); addLink(+result.id, appearance ? +appearance.id : null, [result.name || b.dataset.name, appearance?.name].filter(Boolean).join(' · ')); }); return; }
      addLink(+b.dataset.addIdentity, b.dataset.addAppearance ? +b.dataset.addAppearance : null, [b.dataset.name,b.dataset.kind,b.dataset.appearanceName].filter(Boolean).join(' · '));
    };
    $('[data-new]',body).addEventListener('click', () => { box.links = parseLinks(linksInput.value); createIdentity(result => { box.links.push({identity_id:result.id,appearance_id:null}); box.names[result.id] = [result.name,result.kind].filter(Boolean).join(' · '); setTimeout(() => chooseLinks(index, suppliedBox, onSave), 0); }); });
    $('[data-appearance-create]',body).addEventListener('click', e => busy(e.currentTarget, async () => { const id=+$('[name=appearance_identity]',body).value, name=$('[name=appearance_name]',body).value.trim(); if (!id || !name) throw new Error('Enter an identity ID and appearance name.'); const result=await api(`/identities/${id}/appearances`,{name}); $('#vm-appearance-result').textContent=`Created appearance #${result.id}. Add ${id}:${result.id} to selected links.`; }));
    findIdentities();
  }
  function saveCrop(index) {
    const box = state.boxes[index];
    dialog('Save confirmed crop', `<p>${esc(linksHelp)} A new identity must be created explicitly before an unknown object can be saved.</p>${field('links','Identity links',linksText(box.links))}${field('source_type','Source type','manual')}${field('source_url','Original image URL (optional)')}${field('source_page','Source page (optional)')}${field('author','Author (optional)')}${field('quality','Quality, 0–1 (optional)','', 'number')}${field('rights','Rights / attribution (optional)')}<label>Moderation<select class="form-select" name="status"><option value="pending">Pending review</option><option value="approved">Explicitly approve</option></select></label><label>Retention<select class="form-select" name="retention"><option value="image">Keep image</option><option value="vector_only">Vector only</option></select></label>`, async body => {
      const get = name => $(`[name=${name}]`, body).value.trim(), links = parseLinks(get('links'));
      if (!links.length) throw new Error('Choose or create an identity before saving. An unknown object can remain unresolved in the laboratory.');
      const quality = get('quality'); if (quality && (!Number.isFinite(Number(quality)) || Number(quality) < 0 || Number(quality) > 1)) throw new Error('Quality must be between 0 and 1.');
      const result = await api('/references', {image_base64:state.image,bbox:box.bbox,links,source_type:get('source_type') || 'manual',source_url:get('source_url') || null,source_page:get('source_page') || null,author:get('author') || null,quality:quality ? Number(quality) : 1,rights:get('rights') || null,status:get('status'),retention_mode:get('retention')});
      message(result.duplicate ? 'This crop already exists; its existing links and moderation were preserved.' : 'Crop saved.'); load();
    }, 'Save crop');
  }
  const videoReview = globalThis.createVisualMemoryVideoReview?.({root: $('#vm-cutscenes'), api, escape: esc, message, dialog, closeDialog, isBusy:()=>working, chooseLinks: (box, onSave) => chooseLinks(null, box, onSave)});
  load();
  if (new URL(location.href).searchParams.get('tab') === 'cutscenes') root.querySelector('[data-tab="cutscenes"]')?.click();
})();
