import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

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
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js', import.meta.url), 'utf8'),
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
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js', import.meta.url),'utf8'),
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
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,location:{href:'http://test/'},setTimeout});
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
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,FileReader,location:{href:'http://test/'},setTimeout});
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
  vm.runInNewContext(readFileSync(new URL('../src/wuwa_story/admin/statics/visual-memory.js',import.meta.url),'utf8'),{document,fetch,URL,URLSearchParams,FormData,FileReader,location:{href:'http://test/'},setTimeout});
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
