import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
function harness(props) {
  const states = [], refs = [], effects = []; let si, ri, ei;
  const element = { style: {}, scrollTop: 0, clientHeight: 200, focus() {}, showPopover() {}, hidePopover() {}, getBoundingClientRect: () => ({top:100,left:20,width:240,bottom:144}), querySelector: () => null };
  const exports = {}, react = {
    useId: () => 'select', useState(value) { const i=si++; if (!(i in states)) states[i]=value; return [states[i], next => { states[i]=typeof next==='function'?next(states[i]):next; }]; },
    useRef(value) { return refs[ri++] ??= {current:value}; },
    useEffect(fn,deps=[]) { const i=ei++; if (!effects[i]||deps.some((v,n)=>v!==effects[i].deps[n])) { effects[i]?.cleanup?.(); effects[i]={fn,deps}; } },
  };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/ui/Select.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,
    {exports,require:id=>id==='react'?react:id==='lucide-react'?new Proxy({},{get:(_,key)=>key}):require(id),window:{innerWidth:800,innerHeight:700,addEventListener(){},removeEventListener(){}}});
  return {props,element,flatten:exports.flattenSelectCategories,render(){si=ri=ei=0;const tree=exports.Select(props);for(const n of nodes(tree))if(n.props?.ref)n.props.ref.current=element;for(const e of effects)if(e.fn){const fn=e.fn;e.fn=null;e.cleanup=fn();}return tree;}};
}
const options=[{value:'idle',label:'Stand1',path:['Idle','Standing']},{value:'idle2',label:'Stand2',path:['Idle','Standing']},{value:'walk',label:'Walk',path:['Movement','Walking']},{value:'blocked',label:'Blocked',path:['Movement','Walking'],disabled:true}];
const rows=t=>nodes(t).filter(n=>n.props?.role==='option');
const row=(t,label)=>rows(t).find(n=>nodes(n).some(child=>child.props?.children===label));
const trigger=t=>nodes(t).find(n=>n.props?.className==='atlas-select-trigger');
const popup=t=>nodes(t).find(n=>n.props?.popover==='auto');
const event=(key,tagName='DIV')=>({key,target:{tagName},nativeEvent:{},preventDefault(){}});

test('catalog drilldown remembers branch and scroll and follows externally controlled selection',()=>{
 const h=harness({value:'idle',onChange(){},options,label:'Animation',searchable:true,categorized:true,rememberPosition:true});
 let t=h.render();trigger(t).props.onClick();t=h.render();row(t,'Idle').props.onClick();t=h.render();row(t,'Standing').props.onClick();t=h.render();
 assert.equal(rows(t).length,2);assert(row(t,'Stand1').props['aria-selected']);
 h.element.scrollTop=140;trigger(t).props.onClick();t=h.render();trigger(t).props.onClick();t=h.render();assert(row(t,'Stand2'));assert.equal(h.element.scrollTop,140);
 h.props.value='walk';h.render();t=h.render();assert(row(t,'Walk'));assert(!row(t,'Stand1'));assert(row(t,'Walk').props['aria-selected']);
});
test('search lives inside popup, searches every category, and keyboard selection ignores disabled rows',()=>{
 let selected;const h=harness({value:'',onChange:v=>selected=v,options,label:'Animation',searchable:true,categorized:true});
 let t=h.render();trigger(t).props.onClick();t=h.render();const input=nodes(popup(t)).find(n=>n.type==='input');assert(input);input.props.onChange({target:{value:'Walk'}});t=h.render();assert.equal(rows(t).length,2);assert(row(t,'Walk'));popup(t).props.onKeyDown(event('Enter','INPUT'));assert.equal(selected,'walk');
 const flat=harness({value:'walk',onChange:v=>selected=v,options:options.slice(2),label:'Flat'});t=flat.render();trigger(t).props.onClick();t=flat.render();popup(t).props.onKeyDown(event('ArrowDown'));t=flat.render();popup(t).props.onKeyDown(event('Enter'));assert.equal(selected,'walk');
});
test('nested categories flatten without replacing option identity',()=>{
 const h=harness({value:'',onChange(){},options:[],label:'Nested'});const result=h.flatten([{label:'Body',children:[{label:'Standing',options:[{value:7,label:'Stand'}]}]}]);assert.equal(result[0].value,7);assert.equal(result[0].path.join('/'),'Body/Standing');
});

