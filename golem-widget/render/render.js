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
    fs.mkdirSync('frames/' + name, { recursive: true });
    for (let i = 0; i < c.n; i++) {
      const url = await p.evaluate(([n, i]) => renderClip(n, i), [name, i]);
      fs.writeFileSync(`frames/${name}/${String(i).padStart(2, '0')}.jpg`, Buffer.from(url.split(',')[1], 'base64'));
    }
    console.log(name, c.n);
  }
  await b.close();
})();
