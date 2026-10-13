import { readFile, writeFile } from 'node:fs/promises';

const modules = ['state.mjs', 'views.mjs', 'admin.mjs', 'mobile.mjs', 'app.mjs'];
const code = (await Promise.all(modules.map(file => readFile(new URL(file, import.meta.url), 'utf8'))))
  .map(source => source.replace(/^import .* from .*;\n/gm, '').replace(/^export /gm, ''))
  .join('\n\n')
  .replace(/<\/script/gi, '<\\/script');
const css = await readFile(new URL('styles.css', import.meta.url), 'utf8');
const source = await readFile(new URL('index.html', import.meta.url), 'utf8');
const result = source.replace('<link rel="stylesheet" href="styles.css">', `<style>${css}</style>`)
  .replace('<script type="module" src="app.mjs"></script>', `<script type="module">${code}</script>`);
await writeFile(new URL('订单履约治理交互稿.html', import.meta.url), result);
process.stdout.write('已生成独立 HTML：订单履约治理交互稿.html\n');
