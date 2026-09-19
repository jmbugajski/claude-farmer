// Runs a built dashboard's inline script in jsdom (Chart.js stubbed) and prints
// the text of the generated sentences, so a template branch can be read and
// diffed instead of eyeballed. Used by wf_sentences; needs the local,
// gitignored `npm i jsdom`.
//   node .claude/render-text.cjs <built.html> [element-id ...]
const path = require('path');
const { JSDOM } = require(path.join(__dirname, '..', 'node_modules', 'jsdom'));
const fs = require('fs');

const IDS = ['gauges', 'cv-cards', 'cv-caveat', 'regime-caveat', 'budget-caveat', 'event-cards',
             'event-caveat', 'health-flags',
             'plan-note', 'tva-caveat', 'f-tom', 'f-pep', 'f-wx', 'f-act'];
const html = fs.readFileSync(process.argv[2], 'utf8').replace(/<script src=[^>]*><\/script>/g, '');
const errs = [];
const dom = new JSDOM(html, { runScripts: 'dangerously', beforeParse(w) {
  w.Chart = function () {}; w.Chart.register = () => {};
  w.Chart.defaults = { font: {}, plugins: { legend: { labels: {} } } };
  w.HTMLCanvasElement.prototype.getContext = () => null;
  w.addEventListener('error', e => errs.push(e.message));
} });
const ids = process.argv.length > 3 ? process.argv.slice(3) : IDS;
for (const id of ids) {
  const el = dom.window.document.getElementById(id);
  let t = el ? el.textContent.replace(/\s+/g, ' ').trim() : '(missing)';
  if (id === 'plan-note') t = t.split('Schedule change-log')[0];   // the log is config, not a sentence
  console.log('## ' + id + '\n' + t + '\n');
}
console.log('SCRIPT_ERRORS=' + errs.length); errs.forEach(e => console.log('  ' + e));
process.exit(errs.length ? 1 : 0);
