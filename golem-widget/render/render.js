const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const only = process.argv[2] ? process.argv[2].split(',') : null;
  const b = await chromium.launch({ args: ['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage({ viewport: { width: 800, height: 500 } });
  p.on('pageerror', e => console.log('err:', e.message));
  await p.goto('http://localhost:8765/golem.html');
  await p.waitForFunction(() => window.__g && window.__g.ready, null, { timeout: 60000 });
  await p.evaluate(() => new Promise(r => setTimeout(r, 500)));
  await p.addScriptTag({ path: 'clips.js' });
  const info = await p.evaluate(() => clipInfo());
  fs.writeFileSync('frames-info.json', JSON.stringify(info));
  for (const [name, c] of Object.entries(info)) {
    if (only && !only.includes(name)) continue;
    fs.rmSync('raw/' + name, { recursive: true, force: true });
    fs.mkdirSync('raw/' + name, { recursive: true });
    const bgu = await p.evaluate(([n]) => renderClip(n, 0, 'bg'), [name]);
    fs.writeFileSync(`raw/${name}/bg.jpg`, Buffer.from(bgu.split(',')[1], 'base64'));
    for (let i = 0; i < c.n; i++) {
      const url = await p.evaluate(([n, i]) => renderClip(n, i, 'fg'), [name, i]);
      fs.writeFileSync(`raw/${name}/${String(i).padStart(3, '0')}.png`, Buffer.from(url.split(',')[1], 'base64'));
    }
    console.log(name, c.n);
  }
  await b.close();
})();
