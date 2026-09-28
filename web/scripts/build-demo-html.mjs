// Build bản demo thành MỘT file HTML tự chứa: mở bằng cách bấm đúp, không cần Node hay backend.
//
//   npm run build:demo-html   →  web/dist/vsf-demo.html
//
// Cách làm: xuất tĩnh Next.js ở chế độ demo (NEXT_PUBLIC_DEMO=1, webpack), rồi nhúng mọi chunk JS,
// CSS và font (data URI) vào index.html. Nguồn vẫn là app React/Next.js trong web/; không sửa file này bằng tay.

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = join(WEB, '.next-demo');
const DIST = join(WEB, 'dist');

rmSync(OUT, { recursive: true, force: true });
execFileSync('npx', ['next', 'build', '--webpack'], {
  cwd: WEB,
  stdio: 'inherit',
  env: { ...process.env, NEXT_OUTPUT: 'export', NEXT_PUBLIC_DEMO: '1', NEXT_TELEMETRY_DISABLED: '1' },
});

const read = (p) => readFileSync(join(OUT, p.replace(/^\//, '')), 'utf8');
const b64 = (p) => readFileSync(join(OUT, p.replace(/^\//, ''))).toString('base64');
let html = readFileSync(join(OUT, 'index.html'), 'utf8');

// CSS: nhúng font woff2 thành data URI, bỏ bản woff dự phòng cho nhẹ.
const cssHrefs = [];
html = html.replace(/<link rel="stylesheet" href="([^"]+)"[^>]*\/?>/g, (_, href) => {
  cssHrefs.push(href);
  const css = read(href)
    .replace(/,\s*url\([^)]*\.woff\)\s*format\("woff"\)/g, '')
    .replace(/url\((\/_next\/static\/media\/[^)]+\.woff2)\)/g, (_m, p) => `url(data:font/woff2;base64,${b64(p)})`);
  return `<style>${css}</style>`;
});

// JS: gom mọi chunk app router (kể cả chunk tải động như Monaco) vào trang, runtime webpack đặt cuối
// để nó nhận các chunk đã đăng ký sẵn thay vì tải qua mạng.
const chunkDir = join(OUT, '_next/static/chunks');
const skip = /^(framework|main-(?!app)|polyfills|pages\/)/;
const files = [];
(function walk(dir, rel) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const r = rel ? `${rel}/${name}` : name;
    if (statSync(full).isDirectory()) walk(full, r);
    else if (name.endsWith('.js') && !skip.test(r)) files.push(r);
  }
})(chunkDir, '');
const runtime = files.filter((f) => f.startsWith('webpack-'));
const ordered = [...files.filter((f) => !f.startsWith('webpack-')), ...runtime];
const safe = (js) => js.replace(/<\/script/gi, '<\\/script');
// Hai chỗ Next.js giả định trang được phục vụ qua HTTP với file /_next/ riêng lẻ:
// 1. bootstrap đọc document.currentScript.src để tìm tiền tố /_next/ — script nhúng không có src,
//    nên trả về một thẻ <script> giả trỏ tới /_next/;
// 2. mở bằng file:// thì location.origin là "null", `new URL(path, origin)` lỗi — thay base "null" bằng origin giả.
const fileShim = `<script>(function(){
var fake=document.createElement('script');fake.src='/_next/static/chunks/inline.js';
Object.defineProperty(document,'currentScript',{configurable:true,get:function(){return fake;}});
if(location.protocol!=='file:')return;
var U=window.URL;function P(u,b){if(b==='null')b='http://localhost';return arguments.length>1?new U(u,b):new U(u);}
P.prototype=U.prototype;Object.setPrototypeOf(P,U);window.URL=P;})();</script>`;
const bundle = fileShim + ordered.map((f) => `<script>/* ${f} */${safe(readFileSync(join(chunkDir, f), 'utf8'))}</script>`).join('');

// Dữ liệu RSC vẫn khai báo file CSS để React tự chèn <link>; CSS đã nằm trong <style> nên trỏ tới stylesheet rỗng.
for (const href of cssHrefs) html = html.split(href).join('data:text/css,');

html = html
  .replace(/<script src="\/_next\/[^"]+"[^>]*><\/script>/g, '')
  .replace(/<link rel="(preload|modulepreload)"[^>]*\/?>/g, '')
  // Hàm thay thế: nội dung JS có `$&`, `$'`… nếu truyền chuỗi thì String.replace sẽ hiểu thành mẫu thay thế.
  .replace('</body>', () => `${bundle}</body>`);

if (/(src|href)="\/_next\//.test(html)) {
  throw new Error('Còn tham chiếu tới /_next/ chưa được nhúng vào HTML');
}

if (!existsSync(DIST)) mkdirSync(DIST);
const target = join(DIST, 'vsf-demo.html');
writeFileSync(target, html);
console.log(`\n✓ ${target} (${Math.round(statSync(target).size / 1024)} KB)`);
