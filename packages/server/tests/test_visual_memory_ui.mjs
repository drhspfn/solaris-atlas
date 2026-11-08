import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

test('identity creation selects a server-provided type and rejects a forged typo', async () => {
  class Element {
    constructor() { this.events={}; this.dataset={}; this.disabled=false; this.value=''; this.children=new Map(); }
    querySelector(s) { if (!this.children.has(s)) this.children.set(s,new Element()); return this.children.get(s); }
    querySelectorAll() { return []; }
    addEventListener(type, fn) { this.events[type]=fn; }
    setAttribute() {} removeAttribute() {} focus() {}
  }
  const root=new Element();
  root.dataset.identityKinds=JSON.stringify([{value:'npc',label:'NPC'},{value:'other',label:'Other'}]);
  const requests=[];
  const fetch=async (url,opts={})=>{ requests.push({url,opts}); return {ok:true,json:async()=>url==='/auth/csrf'?{csrf_token:'token'}:url.endsWith('/identities')?{id:10,name:'Mysterious Woman',kind:'npc'}:{items:[],total:0}}; };
  const document={querySelector:()=>root,body:{style:{}},activeElement:new Element()};
  class FormData { constructor() { return new URLSearchParams(); } }
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js',import.meta.url),'utf8')+' '+readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),
    {document,fetch,URL,URLSearchParams,FormData,location:{href:'http://test/'},setTimeout});
  root.querySelector('#vm-create-identity').events.click();
  const body=root.querySelector('#vm-dialog-body');
  assert.match(body.innerHTML,/<select[^>]+name="kind"/);
  assert.match(body.innerHTML,/<option value="npc">NPC<\/option>/);
  assert.doesNotMatch(body.innerHTML,/<input[^>]+name="kind"/);
  await new Promise(resolve=>setImmediate(resolve));
  body.querySelector('[name=name]').value='Mysterious Woman';
  body.querySelector('[name=kind]').value='npm';
  const submit=root.querySelector('#vm-dialog-submit');
  await submit.events.click({currentTarget:submit});
  assert.match(root.querySelector('#vm-message').textContent,/Choose a type/);
  assert.equal(requests.filter(r=>r.url.endsWith('/identities')&&r.opts.method==='POST').length,0);
  body.querySelector('[name=kind]').value='npc';
  await submit.events.click({currentTarget:submit});
  const saved=requests.find(r=>r.url.endsWith('/identities')&&r.opts.method==='POST');
  assert.equal(JSON.parse(saved.opts.body).kind,'npc');
});

test('library paginates and ignores stale filter results', async () => {
  class Element {
    constructor() { this.events = {}; this.dataset = {}; this.disabled = false; this.value = ''; }
    addEventListener(type, fn) { this.events[type] = fn; }
    setAttribute() {} removeAttribute() {} focus() {}
  }
  const elements = new Map();
  const root = new Element();
  Element.prototype.querySelector = function() { return new Element(); };
  root.querySelector = s => { if (!elements.has(s)) elements.set(s, new Element()); return elements.get(s); };
  root.querySelectorAll = () => [];
  const requests = [];
  const fetch = (url) => new Promise(resolve => requests.push({url, resolve}));
  const document = {querySelector: () => root, body: {style: {}}, activeElement: new Element()};
  class FormData { constructor() { return new URLSearchParams(); } }
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js', import.meta.url), 'utf8'),
    {document, fetch, URL, URLSearchParams, FormData, location: {href:'http://test/'}, setTimeout});
  assert.match(requests[0].url, /offset=0/);
  root.querySelector('#vm-filters').events.submit({preventDefault(){}});
  assert.equal(requests.length, 2);
  requests[1].resolve({ok:true, json:async()=>({total:21, items:[{id:9,status:'pending',links:[],model_keys:['model-a']}]})});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(root.querySelector('#vm-cards').innerHTML, /Reference #9/);
  assert.match(root.querySelector('#vm-cards').innerHTML, /model-a/);
  requests[0].resolve({ok:true, json:async()=>({total:0,items:[]})});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(root.querySelector('#vm-cards').innerHTML, /Reference #9/);
  root.querySelector('#vm-next').events.click();
  assert.match(requests[2].url, /offset=20/);
  requests[2].resolve({ok:true,json:async()=>({total:21,items:[{id:10,status:'approved',links:[]}]})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(root.querySelector('#vm-page').textContent, '21–21 of 21');
});

test('editing detected bounds clears candidates and selected identities', async () => {
  class Element {
    constructor() { this.events = {}; this.dataset = {}; this.disabled = false; this.value = ''; this.style = {}; }
    addEventListener(type, fn) { this.events[type] = fn; }
    setAttribute() {} removeAttribute() {} focus() {}
    decode() { return Promise.resolve(); }
  }
  const elements = new Map(), root = new Element();
  root.querySelector = s => { if (!elements.has(s)) elements.set(s, new Element()); return elements.get(s); };
  root.querySelectorAll = () => [];
  const img = root.querySelector('#vm-image'); img.naturalWidth = 100; img.naturalHeight = 100; img.getBoundingClientRect = () => ({left:0,top:0,width:100,height:100});
  class FileReader { readAsDataURL() { this.result = 'data:image/png;base64,YQ=='; this.onload(); } }
  class FormData { constructor() { return new URLSearchParams(); } }
  const fetch = async url => ({ok:true, json:async () => url === '/auth/csrf' ? {csrf_token:'token'} : url.endsWith('/identify') ? {
    detections:[{bbox:[0,0,40,40], detection:{status:'detected'}, identification:{status:'identified',candidates:[{identity_id:12,name:'Yangyang',kind:'character',score:0.8,evidence:[]}]}}],
  } : {items:[], total:0}});
  const document = {querySelector:()=>root, body:{style:{}}, activeElement:new Element()};
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js', import.meta.url),'utf8'),
    {document, fetch, URL, URLSearchParams, FormData, FileReader, location:{href:'http://test/'}, setTimeout});
  await root.querySelector('#vm-file').events.change({target:{files:[{type:'image/png',size:1}]}});
  await root.querySelector('#vm-detect').events.click({currentTarget:root.querySelector('#vm-detect')});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(root.querySelector('#vm-boxes').innerHTML, /Yangyang/);
  const candidate = {dataset:{candidate:'0',identity:'12',appearance:'',name:'Yangyang',kind:'character'}, closest(){return this;}};
  root.querySelector('#vm-boxes').events.click({target:candidate});
  assert.match(root.querySelector('#vm-boxes').innerHTML, /Selected links: Yangyang/);
  const coordinate = {dataset:{coordinate:'2',index:'0'},value:'50',closest(){return this;}};
  root.querySelector('#vm-boxes').events.change({target:coordinate});
  assert.doesNotMatch(root.querySelector('#vm-boxes').innerHTML, /Yangyang/);
  assert.match(root.querySelector('#vm-boxes').innerHTML, /None — unresolved/);
  await root.querySelector('#vm-identify').events.click({currentTarget:root.querySelector('#vm-identify')});
  await new Promise(resolve => setImmediate(resolve));
  root.querySelector('#vm-boxes').events.click({target:candidate});
  const overlay = root.querySelector('#vm-overlay'); overlay.setPointerCapture = () => {};
  const handle = {dataset:{resize:'0'}, closest(selector) { return selector === '[data-resize]' ? this : null; }};
  overlay.events.pointerdown({target:handle,currentTarget:overlay,pointerId:1});
  overlay.events.pointerup({clientX:60,clientY:60});
  assert.doesNotMatch(root.querySelector('#vm-boxes').innerHTML, /Yangyang/);
  assert.match(root.querySelector('#vm-boxes').innerHTML, /None — unresolved/);
});

test('reference approval and rejection send explicit moderation decisions', async () => {
  class Element {
    constructor() { this.events={}; this.dataset={}; this.disabled=false; this.hidden=true; this.value=''; this.style={}; }
    addEventListener(type, fn) { this.events[type]=fn; }
    setAttribute() {} removeAttribute() {} focus() {}
    querySelector(s) { return this.children?.[s] || new Element(); }
  }
  const elements=new Map(), root=new Element(), requests=[];
  root.querySelector=s=>{if(!elements.has(s)) elements.set(s,new Element()); return elements.get(s);};
  root.querySelectorAll=()=>[];
  const body=root.querySelector('#vm-dialog-body'); body.children={'[name=reason]':Object.assign(new Element(),{value:'Wrong identity'})};
  const modal=root.querySelector('#vm-dialog'); modal.querySelector=()=>new Element();
  class FormData {constructor(){return new URLSearchParams();}}
  const fetch=async (url,opts={})=>{ requests.push({url,opts}); return {ok:true,json:async()=>url==='/auth/csrf'?{csrf_token:'t'}:url.endsWith('/history')?{items:[]}:url.endsWith('/references/7')?{id:7,status:'pending',enabled_for_search:false,links:[],source_page:'https://example.test/page',source_url:'javascript:evil()'}:{items:[],total:0}}; };
  const document={querySelector:()=>root,body:{style:{}},activeElement:new Element()};
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,location:{href:'http://test/'},setTimeout});
  const review={dataset:{action:'details',id:'7'},disabled:false,setAttribute(){},removeAttribute(){}};
  root.querySelector('#vm-cards').events.click({target:{closest:()=>review}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(body.innerHTML, /Open source page/);
  assert.doesNotMatch(body.innerHTML, /javascript:evil/);
  const approve={dataset:{op:'approve'},disabled:false,setAttribute(){},removeAttribute(){}};
  body.onclick({target:{closest:()=>approve}});
  await new Promise(resolve=>setImmediate(resolve));
  const approval=requests.find(x=>x.url.endsWith('/references/7/review'));
  assert.deepEqual(JSON.parse(approval.opts.body),{status:'approved',enabled_for_search:true,reason:null});
  root.querySelector('#vm-cards').events.click({target:{closest:()=>review}});
  await new Promise(resolve=>setImmediate(resolve));
  const reject={dataset:{op:'reject'},disabled:false,setAttribute(){},removeAttribute(){}};
  body.onclick({target:{closest:()=>reject}});
  root.querySelector('#vm-dialog-submit').events.click({currentTarget:root.querySelector('#vm-dialog-submit')});
  await new Promise(resolve=>setImmediate(resolve));
  const removal=requests.find(x=>x.url.endsWith('/references/7/remove'));
  assert.deepEqual(JSON.parse(removal.opts.body),{mode:'reject',reason:'Wrong identity'});
});

test('late image reads cannot replace a newer upload', async () => {
  class Element {
    constructor(){this.events={};this.dataset={};this.disabled=false;this.style={};}
    addEventListener(type,fn){this.events[type]=fn;} setAttribute(){} removeAttribute(){} focus(){}
    decode(){return Promise.resolve();}
  }
  const elements=new Map(), root=new Element(), readers=[];
  root.querySelector=s=>{if(!elements.has(s)) elements.set(s,new Element()); return elements.get(s);}; root.querySelectorAll=()=>[];
  const image=root.querySelector('#vm-image'); image.naturalWidth=100; image.naturalHeight=100;
  class FileReader { constructor(){readers.push(this);} readAsDataURL(){} }
  class FormData {constructor(){return new URLSearchParams();}}
  const fetch=async()=>({ok:true,json:async()=>({items:[],total:0})});
  const document={querySelector:()=>root,body:{style:{}},activeElement:new Element()};
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,FileReader,location:{href:'http://test/'},setTimeout});
  const change=root.querySelector('#vm-file').events.change;
  const first=change({target:{files:[{type:'image/png',size:1}]}}), second=change({target:{files:[{type:'image/png',size:1}]}});
  readers[1].result='data:image/png;base64,bmV3'; readers[1].onload(); await second;
  readers[0].result='data:image/png;base64,b2xk'; readers[0].onload(); await first;
  assert.equal(image.src,'data:image/png;base64,bmV3');
});

test('pointer gestures cannot change boxes while inference is pending', async () => {
  class Element {
    constructor(){this.events={};this.dataset={};this.disabled=false;this.style={};}
    addEventListener(type,fn){this.events[type]=fn;} setAttribute(){} removeAttribute(){} focus(){} decode(){return Promise.resolve();}
  }
  const elements=new Map(), root=new Element();
  root.querySelector=s=>{if(!elements.has(s)) elements.set(s,new Element()); return elements.get(s);}; root.querySelectorAll=()=>[];
  const img=root.querySelector('#vm-image'); img.naturalWidth=100; img.naturalHeight=100; img.getBoundingClientRect=()=>({left:0,top:0,width:100,height:100});
  class FileReader {readAsDataURL(){this.result='data:image/png;base64,YQ=='; this.onload();}}
  class FormData {constructor(){return new URLSearchParams();}}
  let finish;
  const fetch=async url=>url.endsWith('/identify') ? new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({detections:[]})});}) : {ok:true,json:async()=>url==='/auth/csrf'?{csrf_token:'token'}:{items:[],total:0}};
  const document={querySelector:()=>root,body:{style:{}},activeElement:new Element()};
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,FileReader,location:{href:'http://test/'},setTimeout});
  await root.querySelector('#vm-file').events.change({target:{files:[{type:'image/png',size:1}]}});
  root.querySelector('#vm-detect').events.click({currentTarget:root.querySelector('#vm-detect')});
  await new Promise(resolve=>setImmediate(resolve));
  const overlay=root.querySelector('#vm-overlay'); overlay.setPointerCapture=()=>{};
  overlay.events.pointerdown({target:{closest:()=>null},currentTarget:overlay,pointerId:1,clientX:10,clientY:10});
  overlay.events.pointerup({clientX:50,clientY:50});
  assert.equal(root.querySelector('#vm-boxes').innerHTML,'');
  finish(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(root.querySelector('#vm-boxes').innerHTML,'');
});

test('identity picker searches game catalog, ignores stale results and explicitly adds a game identity', async () => {
  class Element {
    constructor() { this.events={}; this.dataset={}; this.value=''; this.hidden=true; this.disabled=false; this.style={}; }
    addEventListener(type, fn) { this.events[type]=fn; } setAttribute() {} removeAttribute() {} focus() {}
    querySelector(s) { if (!this.children) this.children=new Map(); if (!this.children.has(s)) this.children.set(s,new Element()); return this.children.get(s); }
  }
  const root=new Element(), document={querySelector:()=>root,body:{style:{}},activeElement:new Element()}, requests=[];
  root.querySelectorAll=()=>[];
  class FormData {constructor(){return new URLSearchParams();}}
  const fetch=(url,opts={})=>new Promise(resolve=>requests.push({url,opts,resolve}));
  let videoOptions;
  const context={document,fetch,URL,URLSearchParams,FormData,location:{href:'http://test/'},setTimeout,clearTimeout,createVisualMemoryVideoReview:opts=>{videoOptions=opts;return {open(){}};}};
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),context);
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  const response=data=>({ok:true,json:async()=>data});
  const box={links:[],names:{}};
  videoOptions.chooseLinks(box,()=>{});
  const body=root.querySelector('#vm-dialog-body'), query=body.querySelector('[name=query]');
  query.value='Hsin'; query.events.keydown({key:'Enter',preventDefault(){}});
  const oldVisual=requests.find(r=>r.url.includes('/identities?') && !r.url.includes('q=Hsin'));
  const oldGame=requests.find(r=>r.url.includes('/game-entities?') && !r.url.includes('q=Hsin'));
  const newVisual=requests.find(r=>r.url.includes('/identities?') && r.url.includes('q=Hsin'));
  const newGame=requests.find(r=>r.url.includes('/game-entities?') && r.url.includes('q=Hsin'));
  newVisual.resolve(response({items:[],total:0})); newGame.resolve(response({items:[{node_id:77,name:'Hsin',kind:'character'}],total:null,has_more:true})); await tick();
  oldVisual.resolve(response({items:[{id:9,name:'Old'}],total:1})); oldGame.resolve(response({items:[],total:0})); await tick();
  assert.match(root.querySelector('#vm-identity-results').innerHTML,/Add Hsin/);
  assert.doesNotMatch(root.querySelector('#vm-identity-results').innerHTML,/Old/);
  assert.doesNotMatch(root.querySelector('#vm-identity-page').textContent,/NaN|undefined/);
  assert.equal(body.querySelector('[data-identity-next]').disabled,false);
  const gameButton={dataset:{addGame:'77',name:'Hsin'},disabled:false,setAttribute(){},removeAttribute(){}};
  body.onclick({target:{closest:()=>gameButton}}); await tick();
  const csrf=requests.find(r=>r.url==='/auth/csrf'); csrf.resolve(response({csrf_token:'token'})); await tick();
  const create=requests.find(r=>r.url.endsWith('/identities/from-game'));
  assert.deepEqual(JSON.parse(create.opts.body),{node_id:77});
  create.resolve(response({id:15,name:'Hsin',kind:'character'})); await tick();
  assert.equal(body.querySelector('[name=links]').value,'15');
  assert.match(root.querySelector('#vm-selected-identities').innerHTML,/Hsin/);
});

test('Rover game choices link appearances to the same identity', async () => {
  class Element {
    constructor() { this.events={}; this.dataset={}; this.value=''; this.hidden=true; this.disabled=false; this.style={}; }
    addEventListener(type, fn) { this.events[type]=fn; } setAttribute() {} removeAttribute() {} focus() {}
    querySelector(s) { if (!this.children) this.children=new Map(); if (!this.children.has(s)) this.children.set(s,new Element()); return this.children.get(s); }
  }
  const root=new Element(), requests=[];
  root.querySelectorAll=()=>[];
  const document={querySelector:()=>root,body:{style:{}},activeElement:new Element()};
  class FormData {constructor(){return new URLSearchParams();}}
  const identity={id:15,name:'Rover',appearances:[{id:101,identity_id:15,name:'Male Rover'},{id:102,identity_id:15,name:'Female Rover'}]};
  const fetch=async (url,opts={})=>{ requests.push({url,opts}); return {ok:true,json:async()=>url==='/auth/csrf'?{csrf_token:'token'}:url.endsWith('/identities/from-game')?identity:url.includes('/game-entities?')?{items:[{node_id:77,name:'Rover',kind:'playable',appearance_choices:[{key:'male',name:'Male Rover'},{key:'female',name:'Female Rover'},{key:'spectro',name:'Spectro Rover'}]}],total:1}:{items:[],total:0}}; };
  let videoOptions;
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/bbox-annotation.js', import.meta.url), 'utf8') + ' ' + readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,location:{href:'http://test/'},setTimeout,clearTimeout,createVisualMemoryVideoReview:opts=>{videoOptions=opts;return {open(){}};}});
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  let saved;
  videoOptions.chooseLinks({links:[],names:{}},links=>{saved=links;});
  await tick();
  const body=root.querySelector('#vm-dialog-body'), html=root.querySelector('#vm-identity-results').innerHTML;
  assert.match(html,/one identity with two appearances/);
  assert.doesNotMatch(html,/Spectro Rover/);
  for (const [name,id] of [['Male Rover',101],['Female Rover',102]]) {
    const match=html.match(new RegExp(`<button[^>]*data-add-game="(\\d+)"[^>]*data-name="([^"]+)"[^>]*data-appearance-name="${name}"[^>]*>${name}</button>`));
    assert.ok(match, `${name} button is rendered`);
    const button={dataset:{addGame:match[1],name:match[2],appearanceName:name},disabled:false,setAttribute(){},removeAttribute(){}};
    body.onclick({target:{closest:()=>button}}); await tick();
    assert.equal(body.querySelector('[name=links]').value,`15:${id}`);
    assert.match(root.querySelector('#vm-selected-identities').innerHTML,new RegExp(`Rover · ${name}`));
  }
  assert.deepEqual(requests.filter(r=>r.url.endsWith('/identities/from-game')).map(r=>JSON.parse(r.opts.body)),[{node_id:77},{node_id:77}]);
  identity.appearances=[];
  const missing={dataset:{addGame:'77',name:'Rover',appearanceName:'Male Rover'},disabled:false,setAttribute(){},removeAttribute(){}};
  body.onclick({target:{closest:()=>missing}}); await tick();
  assert.match(root.querySelector('#vm-dialog-message').textContent,/appearance is unavailable/);
  assert.equal(body.querySelector('[name=links]').value,'15:102');
  root.querySelector('#vm-dialog-submit').events.click({currentTarget:root.querySelector('#vm-dialog-submit')}); await tick();
  assert.equal(JSON.stringify(saved),JSON.stringify([{identity_id:15,appearance_id:102}]));
});

function modalHarness() {
  let document;
  class Element {
    constructor() {
      this.events = {};
      this.dataset = {};
      this.value = "";
      this.hidden = true;
      this.disabled = false;
      this.style = {};
      this.children = new Map();
    }
    addEventListener(type, fn) {
      this.events[type] = fn;
    }
    setAttribute() {}
    removeAttribute() {}
    focus() {
      document.activeElement = this;
    }
    querySelector(s) {
      if (!this.children.has(s)) this.children.set(s, new Element());
      return this.children.get(s);
    }
    querySelectorAll() {
      return [];
    }
    set innerHTML(value) {
      this.html = value;
      for (const match of value.matchAll(
        /<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"/g,
      ))
        this.querySelector(`[name=${match[1]}]`).value = match[2];
    }
    get innerHTML() {
      return this.html || "";
    }
    set textContent(value) {
      this.text = value;
      this.html = "";
    }
    get textContent() {
      return this.text || "";
    }
    insertAdjacentHTML() {}
  }
  const root = new Element();
  root.dataset.identityKinds = JSON.stringify([{ value: "npc", label: "NPC" }]);
  document = {
    querySelector: () => root,
    body: { style: {} },
    activeElement: new Element(),
  };
  class FormData {
    constructor() {
      return new URLSearchParams();
    }
  }
  const fetch = async (url) => ({
    ok: true,
    json: async () =>
      url === "/auth/csrf"
        ? { csrf_token: "token" }
        : url.endsWith("/identities")
          ? { id: 44, name: "New", kind: "npc" }
          : { items: [], total: 0 },
  });
  let videoOptions;
  const windowEvents = {};
  vm.runInNewContext(
    readFileSync(
      new URL(
        "../src/wuwa_story/admin/statics/bbox-annotation.js",
        import.meta.url,
      ),
      "utf8",
    ) +
      " " +
      readFileSync(
        new URL(
          "../src/wuwa_story/admin/statics/visual-memory.js",
          import.meta.url,
        ),
        "utf8",
      ),
    {
      document,
      fetch,
      URL,
      URLSearchParams,
      FormData,
      location: { href: "http://test/" },
      setTimeout,
      clearTimeout,
      addEventListener: (type, fn) => {
        windowEvents[type] = fn;
      },
      createVisualMemoryVideoReview: (opts) => {
        videoOptions = opts;
        return { open() {} };
      },
    },
  );
  const modal = root.querySelector("#vm-dialog"),
    body = root.querySelector("#vm-dialog-body"),
    submit = root.querySelector("#vm-dialog-submit");
  const clickAttr = (attr) =>
    modal.events.click({ target: { hasAttribute: (name) => name === attr } });
  const escape = () =>
    modal.events.keydown({ key: "Escape", preventDefault() {} });
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  return {
    root,
    body,
    modal,
    submit,
    videoOptions,
    clickAttr,
    escape,
    tick,
    windowEvents,
  };
}

test("close callbacks run once on save or discard, and unload warns only for the current dirty dialog", async () => {
  const h = modalHarness();
  let dirty = false,
    closed = 0;
  const unload = () => {
    const event = {
      prevented: false,
      preventDefault() {
        this.prevented = true;
      },
    };
    h.windowEvents.beforeunload(event);
    return event;
  };
  h.videoOptions.dialog("Edit", "", async () => {}, "Save", {
    isDirty: () => dirty,
    onClose: () => closed++,
  });
  assert.equal(unload().prevented, false);
  dirty = true;
  const warned = unload();
  assert.equal(warned.prevented, true);
  assert.equal(warned.returnValue, "");
  h.clickAttr("data-close");
  h.clickAttr("data-keep-editing");
  assert.equal(closed, 0);
  h.clickAttr("data-close");
  h.clickAttr("data-discard-changes");
  assert.equal(closed, 1);
  assert.equal(unload().prevented, false);
  h.videoOptions.closeDialog();
  assert.equal(closed, 1);
  h.videoOptions.dialog("Save", "", async () => {}, "Save", {
    isDirty: () => true,
    onClose: () => closed++,
  });
  await h.submit.events.click({ currentTarget: h.submit });
  assert.equal(closed, 2);
  assert.equal(unload().prevented, false);
  h.videoOptions.dialog("Dirty", "", async () => {}, "Save", {
    isDirty: () => true,
  });
  h.videoOptions.dialog("Clean", "", async () => {});
  assert.equal(unload().prevented, false);
});

test("shared modal guards dirty cancel and Escape, and successful save bypasses the guard", async () => {
  const h = modalHarness(),
    { dialog } = h.videoOptions;
  let dirty = false,
    saves = 0;
  dialog(
    "Edit",
    "",
    async () => {
      saves++;
    },
    "Save",
    { isDirty: () => dirty },
  );
  h.clickAttr("data-close");
  assert.equal(h.modal.hidden, true);
  dialog(
    "Edit",
    "",
    async () => {
      saves++;
    },
    "Save",
    { isDirty: () => dirty },
  );
  dirty = true;
  h.escape();
  assert.equal(h.modal.hidden, false);
  assert.match(
    h.root.querySelector("#vm-dialog-message").innerHTML,
    /Discard unsaved changes/,
  );
  h.escape();
  assert.equal(h.modal.hidden, false);
  assert.equal(h.root.querySelector("#vm-dialog-message").innerHTML, "");
  h.clickAttr("data-close");
  h.clickAttr("data-keep-editing");
  assert.equal(h.modal.hidden, false);
  await h.submit.events.click({ currentTarget: h.submit });
  assert.equal(saves, 1);
  assert.equal(h.modal.hidden, true);
  dialog("Edit", "", async () => {}, "Save", { isDirty: () => true });
  h.clickAttr("data-close");
  h.clickAttr("data-discard-changes");
  assert.equal(h.modal.hidden, true);
});

test("a late submit does not close the newer dialog", async () => {
  const h = modalHarness();
  let finish;
  h.videoOptions.dialog(
    "First",
    "",
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const pending = h.submit.events.click({ currentTarget: h.submit });
  h.videoOptions.dialog("Second", "", async () => {});
  finish();
  await pending;
  assert.equal(h.modal.hidden, false);
  assert.equal(h.root.querySelector("#vm-dialog-title").textContent, "Second");
});

test("picker compares parsed links, protects invalid edits, additions and removals", async () => {
  const h = modalHarness(),
    box = {
      links: [{ identity_id: 3, appearance_id: null }],
      names: { 3: "Three" },
    };
  h.videoOptions.chooseLinks(box, () => {});
  await h.tick();
  const links = h.body.querySelector("[name=links]");
  links.value = " 3  ";
  h.clickAttr("data-close");
  assert.equal(h.modal.hidden, true);
  h.videoOptions.chooseLinks(box, () => {});
  await h.tick();
  links.value = "invalid";
  h.escape();
  assert.match(h.root.querySelector("#vm-dialog-message").innerHTML, /Discard/);
  h.clickAttr("data-keep-editing");
  links.value = "3";
  const add = {
    dataset: { addIdentity: "4", name: "Four" },
    closest() {
      return this;
    },
  };
  h.body.onclick({ target: add });
  h.clickAttr("data-close");
  assert.match(h.root.querySelector("#vm-dialog-message").innerHTML, /Discard/);
  h.clickAttr("data-discard-changes");
  assert.equal(
    JSON.stringify(box.links),
    JSON.stringify([{ identity_id: 3, appearance_id: null }]),
  );
  h.videoOptions.chooseLinks(box, () => {});
  await h.tick();
  const remove = {
    dataset: { removeIdentity: "3" },
    closest() {
      return this;
    },
  };
  h.body.onclick({ target: remove });
  h.escape();
  assert.match(h.root.querySelector("#vm-dialog-message").innerHTML, /Discard/);
});

test("picker exposes selected names to its save callback before updating links", async () => {
  const h = modalHarness(), box = { links: [], names: {} };
  let observedName, linksAtCallback;
  h.videoOptions.chooseLinks(box, () => {
    observedName = box.names[4];
    linksAtCallback = box.links.length;
  });
  await h.tick();
  const add = { dataset: { addIdentity: "4", name: "Four" }, closest() { return this; } };
  h.body.onclick({ target: add });
  await h.submit.events.click({ currentTarget: h.submit });
  assert.equal(observedName, "Four");
  assert.equal(linksAtCallback, 0);
  assert.equal(box.links[0].identity_id, 4);
});

test("creating an identity returns to the picker with its original dirty baseline", async () => {
  const h = modalHarness(),
    box = { links: [], names: {} };
  h.videoOptions.chooseLinks(box, () => {});
  await h.tick();
  h.body.querySelector("[name=links]").value = "3";
  h.body.querySelector("[data-new]").events.click();
  h.body.querySelector("[name=name]").value = "New";
  h.body.querySelector("[name=kind]").value = "npc";
  await h.submit.events.click({ currentTarget: h.submit });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(
    h.root.querySelector("#vm-dialog-title").textContent,
    "Choose identities and appearances",
  );
  assert.equal(h.body.querySelector("[name=links]").value, "3, 44");
  h.escape();
  assert.match(h.root.querySelector("#vm-dialog-message").innerHTML, /Discard/);
  assert.equal(box.links.length, 0);
});
