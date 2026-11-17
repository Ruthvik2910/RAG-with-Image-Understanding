// Exercise the real dashboard component and event handlers without a browser or
// extra dependencies. Only hooks/network/Next Link are deterministic test doubles.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const key = '__evaluation_dashboard_test__';
const source = await readFile(new URL('../src/app/evaluation/page.tsx', import.meta.url), 'utf8');
const hooksUrl = new URL(`./.evaluation-hooks-${process.pid}.mjs`, import.meta.url);
const pageUrl = new URL(`./.evaluation-page-${process.pid}.mjs`, import.meta.url);
await writeFile(hooksUrl, `
import { createElement } from 'react';
export const API_URL = 'http://localhost:8000';
export function useState(initial) {
 const h = globalThis[${JSON.stringify(key)}];
 const i = h.cursor++;
 if (!(i in h.states)) h.states[i] = initial;
 return [h.states[i], value => { h.states[i] = typeof value === 'function' ? value(h.states[i]) : value; }];
}
export function useEffect(callback) { globalThis[${JSON.stringify(key)}].effects.push(callback); }
export function Link(props) { return createElement('a', {href: props.href, className: props.className}, props.children); }
export async function apiFetch(url, init={}) {
 const h = globalThis[${JSON.stringify(key)}];
 h.requests.push([url, init.method || 'GET']);
 if (h.fail) return {ok:false,status:503};
 if (init.method === 'DELETE') h.payload = h.empty;
 return {ok:true,json:async()=>h.payload};
}
`);
const compiled = ts.transpileModule(source
  .replace('from "@/lib/api"', `from ${JSON.stringify(hooksUrl.href)}`)
  .replace('from "react"', `from ${JSON.stringify(hooksUrl.href)}`)
  .replace('import Link from "next/link";', `import { Link } from ${JSON.stringify(hooksUrl.href)};`),
  {compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
await writeFile(pageUrl, compiled);
const Page = (await import(pageUrl.href)).default;
await unlink(pageUrl); await unlink(hooksUrl);

const empty = { results:[], summary:{total_questions:0,successful_questions:0,failed_questions:0,
 average_latency_seconds:0,grounded_rate:0,answer_quality_rate:0,average_retries:0,average_retrieval_relevance:0,
 grounded_evaluated_questions:0,quality_evaluated_questions:0,retrieval_evaluated_questions:0} };
const populated = {results:[{question:'Recorded question',answer:'Recorded answer',route:'local',retrieved_documents:4,relevant_documents:2,
 grounded:true,answers_question:false,retry_count:1,latency_seconds:2,sources:[],timestamp:'2026-11-17T00:00:00Z',error:'',retrieval_relevance_rate:0.5,success:true}],
 summary:{...empty.summary,total_questions:1,successful_questions:1,average_latency_seconds:2,grounded_rate:0.5,answer_quality_rate:0,average_retries:1,average_retrieval_relevance:0.5,grounded_evaluated_questions:1,quality_evaluated_questions:1,retrieval_evaluated_questions:1}};
function setup(payload=populated) {
 globalThis[key]={states:[],cursor:0,effects:[],requests:[],payload,empty,fail:false};
 globalThis.window={confirm:()=>true,setTimeout,clearTimeout};
 return globalThis[key];
}
function render() {globalThis[key].cursor=0; return Page();}
function walk(node, predicate) {
 if (!node || typeof node !== 'object') return null;
 if (predicate(node)) return node;
 for(const child of [node.props?.children].flat(Infinity)) {const found=walk(child,predicate); if(found)return found;}
 return null;
}
function button(tree,label) {return walk(tree,n=>n.type==='button'&&[n.props.children].flat().join('')===label);}
function metric(tree,label) {return walk(tree,n=>n.type?.name==='MetricCard'&&n.props.label===label)?.props.value;}
async function refresh() {const tree=render(); const control=walk(tree,node=>node.type==='button' && node.props.onClick?.name==='loadEvaluation'); await control.props.onClick(); return render();}

test('evaluation requests share the same API origin as chat and observability', async()=>{
 const h=setup(); await refresh();
 assert.equal(h.requests[0][0],'http://localhost:8000/evaluation');
 assert.ok(source.includes('import { apiFetch, API_URL } from "@/lib/api"'));
 assert.ok(!source.includes('127.0.0.1:8000'));
});

test('real dashboard renders returned records, percentages, latency, retries, and execution status',async()=>{
 setup(); const tree=await refresh();
 assert.equal(metric(tree,'Total questions'),'1');
 assert.equal(metric(tree,'Successful'),'1');
 assert.equal(metric(tree,'Grounded rate'),'50.0%');
 assert.equal(metric(tree,'Average latency'),'2.00s');
 assert.equal(metric(tree,'Average retries'),'1.00');
 assert.ok(renderToStaticMarkup(tree).includes('Recorded question'));
 assert.ok(renderToStaticMarkup(tree).includes('Success'));
});

test('refresh reads updated actual response data instead of retaining prior counts',async()=>{
 const h=setup(); await refresh();
 h.payload={results:[],summary:{...empty.summary,total_questions:3,successful_questions:2,failed_questions:1}};
 const tree=await refresh();
 assert.equal(metric(tree,'Total questions'),'3');
 assert.equal(metric(tree,'Failed'),'1');
 assert.equal(h.requests.length,2);
});

test('initial load uses the same request handler and produces a genuine empty state',async()=>{
 const h=setup(empty); render();
 h.effects[0]();
 await new Promise(resolve=>setTimeout(resolve,5));
 const tree=render();
 assert.equal(metric(tree,'Total questions'),'0');
 assert.ok(renderToStaticMarkup(tree).includes('No evaluations yet'));
 assert.equal(h.requests.length,1);
});

test('clear history deletes evaluation only and then reloads its empty history',async()=>{
 const h=setup(); await refresh();
 await button(render(),'Clear history').props.onClick();
 const tree=render();
 assert.deepEqual(h.requests.slice(1),[['http://localhost:8000/evaluation','DELETE'],['http://localhost:8000/evaluation','GET']]);
 assert.equal(metric(tree,'Total questions'),'0');
 assert.ok(h.requests.every(([url])=>!url.includes('observability')));
});

test('request errors are displayed rather than fabricated zero metrics or a false empty state',async()=>{
 const h=setup(); h.fail=true;
 const tree=await refresh(); const html=renderToStaticMarkup(tree);
 assert.ok(html.includes('Evaluation request failed (503)'));
 assert.ok(!html.includes('No evaluations yet'));
 assert.equal(metric(tree,'Total questions'),undefined);
});

test('unassessed results show dashes while execution success remains separate',async()=>{
 setup({...populated,summary:{...populated.summary,grounded_evaluated_questions:0,quality_evaluated_questions:0,retrieval_evaluated_questions:0}});
 const tree=await refresh();
 assert.equal(metric(tree,'Successful'),'1');
 for(const label of ['Grounded rate','Answer quality','Retrieval relevance']) assert.equal(metric(tree,label),'—');
});

test('observability percentages convert the backend fraction exactly once',async()=>{
 const text=await readFile(new URL('../src/app/observability/page.tsx',import.meta.url),'utf8');
 const ast=ts.createSourceFile('page.tsx',text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 const declaration=ast.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='formatPercent');
 const js=ts.transpileModule(declaration.getText(ast),{compilerOptions:{target:ts.ScriptTarget.ES2020}}).outputText;
 const format=new Function(js+'; return formatPercent;')();
 assert.equal(format(0.5),'50.0%'); assert.equal(format(1),'100.0%'); assert.equal(format(0),'0.0%');
});
