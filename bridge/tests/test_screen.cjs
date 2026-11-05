// Run the actual page functions with fixed data, without network or a browser.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../server/app/static');
function script(name) {
  return fs.readFileSync(path.join(root, name), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
}
new vm.Script(script('admin.html')); // Catch syntax regressions in the settings page.
const context = vm.createContext({
  document: { getElementById: () => ({}) },
  addEventListener() {}, setInterval() {},
  fetch: async () => { throw new Error('offline test'); },
});
vm.runInContext(script('screen.html'), context);
function run(expression) { return vm.runInContext(expression, context); }
run(`data = {now:10000, stale_s:300, usage_provider:'codex', sessions:[], perms:[],
  servers:[], media:null, screen:{sound:true}, forecast:{}, usage_hist:{day:[],week:[]},
  today:{prompts:0,tools:0,projects:[]}}; view={name:'claude'};`);
function usage(value) { run(`data.usage=${JSON.stringify(value)}`); }
usage({provider:'codex', t:10000, five:-1, week:31, weekReset:20000,
  windows:[{label:'Неделя',used:31,reset:20000,minutes:10080}]});
let html = run('claudeView()');
assert(html.includes('Codex') && html.includes('31%'));
assert(html.includes('5 часов') && html.includes('нет данных'));
assert(html.includes('codex-full') && !html.includes('class="top"'));
assert(!run('usageView()').includes('-1%'));
assert(!run('usageView()').includes('5 часов'));
usage({provider:'codex',t:10000,five:-1,week:-1,windows:[]});
assert(run('claudeView()').includes('окна не предоставлены'));
assert(run('usageView()').includes('Нет данных'));
usage({provider:'codex',t:1,five:-1,week:-1,
  windows:[{label:'15 мин<script>',used:0,reset:20000,minutes:15}]});
html = run('claudeView()');
assert(html.includes('0%'));
assert(html.includes('устарело'));
assert(html.includes('codex_cyd.py watch'));
assert(html.includes('&lt;script&gt;') && !html.includes('<script>'));
assert(run('usageView()').includes('15 мин'));
usage(null);
assert(run('claudeView()').includes('ожидаю лимиты Codex'));
assert(run('usageView()').includes('Нет данных'));
run(`data.usage_provider='claude'`);
usage({t:10000,model:'Claude Desktop',five:20,week:40});
html = run('claudeView()');
assert(html.includes('Claude Desktop') && html.includes('20%') && html.includes('40%'));
run(`data.space_screens={show_iss:false,show_spaceweather:true}`);
assert.deepEqual(JSON.parse(JSON.stringify(run('spaceTabs().map(x=>x[0])'))), ['spaceweather']);
run(`data.space_screens={show_iss:true,show_spaceweather:true};data.space={iss:{t:10000,lat:40,lon:60,altitude:420,speed:27600,distance:500},weather:{t:10000,kp:5.67,peak:6,points:[[9000,3],[10000,5.67]]}}`);
assert(run('issView()').includes('420 км'));
assert(run('spaceweatherView()').includes('Магнитная буря G1'));
run(`view.menu=true`);
assert(run('navigationMenu()').includes('data-scroll="menu"'));
assert(run('navigationMenu()').includes('scrollMenu(1)'));
console.log('Screen checks passed: limits, space screens, visibility flags, scrollable menu, admin syntax.');
