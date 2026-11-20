// Page-side: poses the golem by hand and renders widget frames (WebGL + 2D ink overlay).
(() => {
  const G = window.__g;
  const { THREE, scene, camera, renderer, golem, GOLEM0, applyPose, setLids, REST, KEYS, sun } = G;
  G.manual = true;
  const keep = new Set([golem, G.ground, sun, sun.target]);
  for (const o of scene.children) if (!keep.has(o) && !o.isLight) o.visible = false;
  golem.visible = true;

  const W = 420, H = 196;
  renderer.setPixelRatio(1);
  renderer.setSize(W, H, false);
  camera.aspect = W / H; camera.updateProjectionMatrix();

  const out = document.createElement('canvas'); out.width = W; out.height = H;
  const ctx = out.getContext('2d');
  const INK = '#1c1b18';
  const TAU = Math.PI * 2;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const ss = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const bump = (a, b, x) => ss(a, (a + b) / 2, x) * (1 - ss((a + b) / 2, b, x));

  // transparent shadow catcher for the golem layer
  const shadowPlane = new THREE.Mesh(new THREE.PlaneGeometry(400, 400).rotateX(-Math.PI / 2), new THREE.ShadowMaterial({ color: 0x2a2620, opacity: 0.3 }));
  shadowPlane.receiveShadow = true; shadowPlane.position.y = 0.02; shadowPlane.visible = false; scene.add(shadowPlane);
  let LAYER = 'fg';
  const fg = (fn) => (LAYER === 'fg' ? fn() : undefined);
  const bg = (fn) => (LAYER === 'bg' ? fn() : undefined);

  // a pebble the golem can hold (eat clip)
  const pebble = new THREE.Mesh(new THREE.DodecahedronGeometry(0.75, 0), new THREE.MeshToonMaterial({ color: 0x9a948a }));
  const pebInk = new THREE.Mesh(pebble.geometry, new THREE.MeshBasicMaterial({ color: 0x1c1b18, side: THREE.BackSide }));
  pebInk.scale.setScalar(1.12); pebble.add(pebInk);
  pebble.visible = false;
  pebble.castShadow = true;
  scene.add(pebble);

  // the walk layer, copied from the game's pose() so the gait matches
  function walkPose(ph, w, time) {
    const b = { ...REST };
    const stride = 1.65, lift = 1.05;
    for (let i = 0; i < 2; i++) {
      const u = ((ph + i * 0.5) % 1 + 1) % 1, k0 = i ? 'r' : 'l';
      let fz, fy = 0, fp = 0;
      if (u < 0.62) fz = stride * (1 - 2 * u / 0.62);
      else { const k = (u - 0.62) / 0.38, e = k * k * (3 - 2 * k); fz = stride * (2 * e - 1); fy = lift * Math.sin(Math.PI * k); fp = 0.4 * Math.sin(Math.PI * k) * (k < 0.55 ? 1 : -0.5); }
      b[k0 + 'fz'] = fz * w; b[k0 + 'fy'] = fy * w; b[k0 + 'fp'] = fp * w;
    }
    const c2 = Math.cos(ph * TAU * 2), s1 = Math.sin(ph * TAU), c1 = Math.cos(ph * TAU);
    b.hy += w * (-0.14 - 0.16 * c2);
    b.hroll += w * 0.05 * s1; b.hyaw += w * 0.12 * s1; b.ty -= w * 0.2 * s1;
    b.hx += w * 0.07; b.tx += w * 0.06;
    b.lsx += w * 0.4 * c1; b.rsx -= w * 0.4 * c1; b.lex -= w * 0.22; b.rex -= w * 0.22;
    b.nx -= w * 0.05;
    const br = Math.sin(time * TAU);
    b.cx += 0.025 * br; b.lsz += 0.015 * br; b.rsz -= 0.015 * br; b.hy += 0.05 * br * (1 - w);
    return b;
  }
  const over = (b, o, w = 1) => { for (const k in o) b[k] = lerp(b[k], o[k], w); return b; };
  const add = (b, o, w = 1) => { for (const k in o) b[k] += o[k] * w; return b; };

  function place(x, z, rot, p, lid = 1) {
    applyPose(GOLEM0, p, 0, 0);
    golem.position.set(x, 0, z); golem.rotation.y = rot;
    setLids(GOLEM0, clamp(lid, 0.06, 1));
  }
  function cam(px, py, pz, tx, ty, tz, fov = 30) {
    camera.fov = fov; camera.position.set(px, py, pz); camera.lookAt(tx, ty, tz); camera.updateProjectionMatrix();
    sun.position.set(tx + 22, 60, tz + 34); sun.target.position.set(tx, 0, tz); sun.target.updateMatrixWorld();
  }
  function toScreen(v) { const p = v.clone().project(camera); return [(p.x + 1) / 2 * W, (1 - p.y) / 2 * H]; }
  const handPos = (side) => { golem.updateMatrixWorld(true); return new THREE.Box3().setFromObject(G.rig.arm[side].hand).getCenter(new THREE.Vector3()); };
  const headPos = () => { golem.updateMatrixWorld(true); return G.rig.head.localToWorld(new THREE.Vector3(0, 11.6 - 11.25 + 0.4, 1.2)); };

  // ---------- 2D ink helpers ----------
  function txt(s, x, y, size, rot = 0, alpha = 1, fill = INK) {
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y); ctx.rotate(rot);
    ctx.font = `900 ${size}px "Arial Black", Impact, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.lineWidth = size * 0.22; ctx.strokeStyle = '#f5f3ee'; ctx.lineJoin = 'round'; ctx.strokeText(s, 0, 0);
    ctx.fillStyle = fill; ctx.fillText(s, 0, 0); ctx.restore();
  }
  function burst(x, y, r0, r1, n, rot = 0, lw = 3) {
    ctx.save(); ctx.strokeStyle = INK; ctx.lineWidth = lw; ctx.lineCap = 'round';
    for (let i = 0; i < n; i++) { const a = rot + i / n * TAU; ctx.beginPath(); ctx.moveTo(x + Math.cos(a) * r0, y + Math.sin(a) * r0); ctx.lineTo(x + Math.cos(a) * r1, y + Math.sin(a) * r1); ctx.stroke(); }
    ctx.restore();
  }
  function smudge(x, y, s, alpha, seed) {
    // a dusty fist print on the glass: a blotch of granite dust with four knuckle dots
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y);
    let r = seed * 9301 + 49297;
    const R = () => ((r = (r * 9301 + 49297) % 233280) / 233280);
    ctx.fillStyle = 'rgba(110,104,95,0.35)';
    for (let i = 0; i < 26; i++) { const a = R() * TAU, d = R() * s * 0.9; ctx.beginPath(); ctx.arc(Math.cos(a) * d, Math.sin(a) * d * 0.7, s * (0.12 + R() * 0.2), 0, TAU); ctx.fill(); }
    ctx.fillStyle = 'rgba(70,66,60,0.55)';
    for (let i = 0; i < 4; i++) { ctx.beginPath(); ctx.ellipse((i - 1.5) * s * 0.42, -s * 0.15 + Math.abs(i - 1.5) * s * 0.08, s * 0.2, s * 0.16, 0, 0, TAU); ctx.fill(); }
    ctx.restore();
  }
  function heart(x, y, s, alpha) {
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y); ctx.scale(s / 20, s / 20);
    ctx.beginPath(); ctx.moveTo(0, 6); ctx.bezierCurveTo(-14, -4, -8, -16, 0, -8); ctx.bezierCurveTo(8, -16, 14, -4, 0, 6); ctx.closePath();
    ctx.fillStyle = '#e8873a'; ctx.fill(); ctx.lineWidth = 2.6; ctx.strokeStyle = INK; ctx.stroke(); ctx.restore();
  }
  function sparkle(x, y, s, alpha) {
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x, y); ctx.fillStyle = '#ffffff'; ctx.strokeStyle = INK; ctx.lineWidth = 2;
    ctx.beginPath(); for (let i = 0; i < 8; i++) { const a = i / 8 * TAU - Math.PI / 2, rr = i % 2 ? s * 0.28 : s; ctx.lineTo(Math.cos(a) * rr, Math.sin(a) * rr); } ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
  }
  function glassSheen(alpha) {
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0.0, `rgba(255,255,255,0)`); g.addColorStop(0.42, `rgba(255,255,255,0)`);
    g.addColorStop(0.47, `rgba(255,255,255,${alpha})`); g.addColorStop(0.53, `rgba(255,255,255,0)`); g.addColorStop(1, `rgba(255,255,255,0)`);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }

  // ---------- clips: t runs 0..1 over the loop ----------
  const WIDE = () => cam(0, 7.6, 34, 0, 6.6, 0, 30);
  const CLOSE = () => cam(0, 11.0, 20.5, 0, 9.9, 0, 38);
  const PRINTS = [[0.33, 0.36, 0], [0.47, 0.52, 1], [0.6, 0.33, 2]];   // where the fist lands on the glass (screen fractions)

  const clips = {
    idle: { n: 240, dur: 4, draw(t) {
      const b = walkPose(0, 0, t * 2);
      const look = Math.sin(t * TAU);
      add(b, { ny: 0.55 * look, hyaw: 0.08 * look, cy: 0.12 * look, nx: 0.06 * Math.sin(t * TAU * 2) });
      add(b, { lsx: 0.06 * Math.sin(t * TAU), rsx: -0.06 * Math.sin(t * TAU) });
      const lid = 1 - bump(0.5, 0.56, t) - bump(0.58, 0.64, t) * 0.9;
      WIDE(); place(0, 0, 0, b, lid); return () => {};
    } },
    walk: { n: 360, dur: 6, draw(t) {
      // right for 42%, turn, left for 42%, turn
      const X = 13;
      let x, rot, w;
      if (t < 0.42) { x = lerp(-X, X, t / 0.42); rot = Math.PI / 2; w = 1; }
      else if (t < 0.5) { const k = ss(0.42, 0.5, t); x = X; rot = lerp(Math.PI / 2, -Math.PI / 2, k); w = 0.35; }
      else if (t < 0.92) { x = lerp(X, -X, (t - 0.5) / 0.42); rot = -Math.PI / 2; w = 1; }
      else { const k = ss(0.92, 1, t); x = -X; rot = lerp(-Math.PI / 2, -3 * Math.PI / 2, k); w = 0.35; }
      const b = walkPose(t * 10, w, t * 3);
      add(b, { ny: rot > 0 ? -0.35 : 0.35 }, w);   // glances at the viewer while walking
      const lid = 1 - bump(0.2, 0.25, t) - bump(0.7, 0.75, t);
      WIDE(); place(x, 0, rot, b, lid); return () => {};
    } },
    knock: { n: 240, dur: 4, draw(t, i) {
      const b = walkPose(0, 0, t);
      const up = ss(0.0, 0.16, t) * (1 - ss(0.84, 0.98, t));
      // knock k: back at (k), hit at (k+.06)
      const hits = [7 / 24, 11 / 24, 15 / 24];
      let hit = 0, aim = 0; hits.forEach((h, k) => { const v = bump(h - 0.07, h + 0.07, t); if (v > hit) { hit = v; } aim += [0.35, -0.15, 0.05][k] * bump(h - 0.12, h + 0.1, t); });
      over(b, { rsx: lerp(-1.25, -1.62, hit), rsz: 0.15 + aim, rex: lerp(-1.5, -0.25, hit), rwx: lerp(-0.4, 0.3, hit), rfc: 1, cy: 0.18, ty: 0.12, nx: -0.05, ny: 0.1 }, up);
      add(b, { hz: 0.4 * hit, hx: 0.06 * hit, cx: 0.06 * hit, rsx: -0.35 * Math.max(0, -aim) + 0.0 });
      const peek = bump(0.66, 0.86, t);
      add(b, { ny: -0.35 * peek, nx: 0.12 * peek, hroll: 0.06 * peek });
      CLOSE(); place(0, 0, 0, b, 1 - bump(0.7, 0.76, t));
      const near = hits.map((h) => Math.abs(t - h) < 0.005);
      near.forEach((on, k) => { if (on) { const [fx, fy] = toScreen(handPos('R')); PRINTS[k] = [fx / W, fy / H, k]; } });
      return () => {
        const shake = hits.some((h) => Math.abs(t - h) < 0.016) ? 4 : 0;
        if (shake) { const img = ctx.getImageData(0, 0, W, H); ctx.putImageData(img, shake, -shake * 0.5); }
        hits.forEach((h, k) => {
          const [px, py] = PRINTS[k];
          if (t >= h - 0.01) smudge(px * W, py * H, 26, 0.9, k + 1);
          if (Math.abs(t - h) < 0.03) { burst(px * W, py * H, 30, 46, 9, k, 3.2); txt('ТУК!', px * W + 34, py * H - 34, 26, -0.18 + k * 0.12); }
        });
      };
    } },
    wipe: { n: 240, dur: 4, draw(t) {
      const b = walkPose(0, 0, t);
      const up = ss(0.0, 0.14, t) * (1 - ss(0.78, 0.94, t));
      const sweep = Math.sin(clamp((t - 0.14) / 0.64, 0, 1) * TAU * 2);   // two passes, left-right
      over(b, { rsx: -1.2 + 0.12 * Math.cos(clamp((t - 0.14) / 0.64, 0, 1) * TAU * 4), rsz: -0.05 + 0.55 * sweep, rex: -1.6, rwx: 0, rwz: -0.25 * sweep, rfc: 0.02, cy: 0.06 + 0.14 * sweep, ty: 0.06 * sweep, nx: 0, ny: 0.3 * sweep }, up);
      const happy = bump(0.82, 1.0, t);
      add(b, { nx: 0.15 * happy, hy: 0.15 * happy });
      CLOSE(); place(0, 0, 0, b, 1 - bump(0.86, 0.92, t) * 0.85);
      const clean = ss(0.16, 0.74, t);
      return () => {
        PRINTS.forEach(([px, py], k) => smudge(px * W, py * H, 26, 0.9 * (1 - ss(0.16 + k * 0.17, 0.4 + k * 0.17, t)), k + 1));
        if (t > 0.78) { sparkle(W * 0.72, H * 0.26, 14 * bump(0.78, 1, t) + 2, bump(0.78, 1, t)); sparkle(W * 0.26, H * 0.62, 10 * bump(0.82, 1, t) + 2, bump(0.82, 1, t)); }
      };
    } },
    eat: { n: 210, dur: 3.5, draw(t) {
      // a pebble is tossed in from the front (from you); he catches it in front of him, eats it, and is happy
      const b = walkPose(0, 0, t);
      const reach = ss(0.0, 0.14, t) * (1 - ss(0.26, 0.36, t));          // open hand out front to catch
      const grip = ss(0.2, 0.25, t);
      const mouth = ss(0.27, 0.4, t) * (1 - ss(0.66, 0.8, t));          // hand to the mouth
      over(b, { rsx: -1.2, rsz: -0.05, rex: -1.6, rwx: 0, rfc: lerp(0.05, 0.75, grip), cy: 0.08, nx: 0.05, ny: 0.1 }, reach);
      over(b, { rsx: -1.45, rsz: 0.55, rex: -1.95, rwx: -0.3, rfc: 0.75, cy: 0.12, nx: -0.1, ny: 0 }, mouth);
      const chew = t > 0.4 && t < 0.78 ? Math.abs(Math.sin((t - 0.4) * TAU * 3.5)) : 0;
      add(b, { nx: -0.12 * chew, cx: 0.04 * chew });
      const joy = bump(0.78, 1.0, t);
      add(b, { hy: 0.35 * joy, lsz: 0.35 * joy, rsz: -0.35 * joy, nx: 0.2 * joy });
      cam(0, 9.6, 24, 0, 8.6, 0, 34); place(0, 0, 0.25, b, joy > 0.3 ? 0.3 : 1);
      // pebble: always on the camera side of the hand, so it is in front of him
      const toCam = camera.position.clone().sub(handPos('R')).normalize();
      const held = handPos('R').add(toCam.clone().multiplyScalar(1.5)).add(new THREE.Vector3(0, 0.4, 0));
      if (t < 0.22) {
        const u = ss(0.02, 0.22, t), from = new THREE.Vector3(6, 8.5, 17);
        pebble.position.copy(from).lerp(held, u); pebble.position.y += Math.sin(u * Math.PI) * 1.6;
        pebble.rotation.set(t * 30, t * 22, 0);
      } else pebble.position.copy(held);
      pebble.scale.setScalar(t < 0.44 ? 1 : lerp(1, 0.2, ss(0.44, 0.56, t)));
      pebble.visible = LAYER === 'fg' && t > 0.02 && t < 0.56;
      const caught = Math.abs(t - 0.23) < 0.035;
      return () => {
        pebble.visible = false;
        if (caught) { const [px, py] = toScreen(held); txt('ХОП!', px + 46, py - 30, 22, -0.12); }
        if (chew > 0.6) { const [hx, hy] = toScreen(headPos()); for (let k = 0; k < 3; k++) { ctx.fillStyle = '#8f897f'; ctx.beginPath(); ctx.arc(hx + (k - 1) * 14, hy + 40 + k * 7 + chew * 6, 3.5, 0, TAU); ctx.fill(); } txt('ХРУМ', hx + 70, hy - 4, 20, 0.15); }
        if (joy > 0.05) { const [hx, hy] = toScreen(headPos()); heart(hx + 52, hy - 30 - joy * 16, 20 + joy * 6, Math.min(1, joy * 2)); }
      };
    } },
    sleep: { n: 120, dur: 4, draw(t) {
      const b = walkPose(0, 0, 0);
      const br = Math.sin(t * TAU);
      over(b, { hy: -2.0, hz: -0.3, hx: 0.1, tx: 0.12, cx: 0.0, nx: -0.55, lsx: 0.25, rsx: 0.25, lsz: 0.25, rsz: -0.25, lex: -0.9, rex: -0.9, lfz: 1.2, rfz: 1.2 });
      add(b, { cx: 0.04 * br, hy: 0.08 * br, nx: 0.04 * br });
      cam(0, 4.6, 30, 0, 5.6, 0, 30); place(0, 0, 0.35, b, 0.06);
      return () => {
        if (LAYER === 'bg') {
          ctx.fillStyle = 'rgba(40,52,92,0.30)'; ctx.fillRect(0, 0, W, H);
          ctx.fillStyle = '#fff6c8'; ctx.beginPath(); ctx.arc(W * 0.84, H * 0.2, 12, 0, TAU); ctx.fill();
          return;
        }
        ctx.save(); ctx.globalCompositeOperation = 'source-atop'; ctx.fillStyle = 'rgba(40,52,92,0.30)'; ctx.fillRect(0, 0, W, H); ctx.restore();
        const [hx, hy] = toScreen(headPos());
        for (let k = 0; k < 3; k++) { const u = (t + k / 3) % 1; txt('z', hx + 30 + u * 46, hy + 14 - u * 46, 14 + u * 16, -0.2, Math.sin(u * Math.PI)); }
      };
    } },
    sad: { n: 120, dur: 4, draw(t) {
      const b = walkPose(0, 0, t);
      const sw = Math.sin(t * TAU);
      over(b, { hy: -0.6, tx: 0.3, cx: 0.3, nx: -0.15, lsx: 0.25, rsx: -0.6, rsz: 0.5, rex: -1.4, rfc: 0.4, lsz: 0.08 });
      add(b, { hroll: 0.04 * sw, ny: 0.2 * sw, rsz: 0.1 * Math.sin(t * TAU * 2) });
      cam(0, 7.4, 30, 0, 6.4, 0, 30); place(0, 0, 0, b, 0.55 - bump(0.5, 0.6, t) * 0.45);
      return () => {
        const [hx, hy] = toScreen(headPos());
        ctx.save(); ctx.fillStyle = '#ffffff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.ellipse(hx + 62, hy - 34, 30, 19, 0, 0, TAU); ctx.fill(); ctx.stroke(); ctx.restore();
        const dots = 1 + Math.floor(t * 3);
        txt('.'.repeat(dots), hx + 62, hy - 38, 22, 0);
      };
    } },
    happy: { n: 180, dur: 3, draw(t) {
      const b = walkPose(0, 0, t);
      const hop = Math.abs(Math.sin(t * TAU * 2));
      over(b, { lsx: -2.7, rsx: -2.7, lsz: 0.45, rsz: -0.45, lex: -0.5, rex: -0.5, nx: 0.2, lfc: 0.1, rfc: 0.1 }, 0.85);
      add(b, { lsz: 0.2 * Math.sin(t * TAU * 4), rsz: -0.2 * Math.sin(t * TAU * 4) });
      add(b, { hy: 1.2 * hop - 0.4, lfy: 1.1 * hop, rfy: 1.1 * hop });
      cam(0, 8.6, 46, 0, 8.4, 0, 30); place(0, 2.4 * hop, 0, b, 0.25);
      golem.position.y = 2.4 * hop;
      return () => {
        const [hx, hy] = toScreen(headPos());
        for (let k = 0; k < 3; k++) { const u = (t * 2 + k / 3) % 1; heart(hx + (k - 1) * 58, hy - 20 - u * 54, 16 + 6 * k, Math.sin(u * Math.PI)); }
      };
    } },
  };

  window.renderClip = (name, i, layer = 'fg') => {
    const c = clips[name], t = i / c.n;
    LAYER = layer;
    golem.position.set(0, 0, 0);
    const post = c.draw(t, i);
    const isFg = layer === 'fg';
    golem.visible = isFg; shadowPlane.visible = isFg; G.ground.visible = !isFg;
    renderer.setClearColor(0xf5f3ee, isFg ? 0 : 1);
    renderer.render(scene, camera);
    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(renderer.domElement, 0, 0, W, H);
    if (isFg || name === 'sleep') post();
    return out.toDataURL(isFg ? 'image/png' : 'image/jpeg', 0.88);
  };
  window.clipInfo = () => Object.fromEntries(Object.entries(clips).map(([k, c]) => [k, { n: c.n, dur: c.dur }]));
})();
window.renderPose = (o) => {
  const G = window.__g; const b = { ...G.REST, ...o };
  G.applyPose(G.GOLEM0, b, 0, 0); G.golem.position.set(0,0,0); G.golem.rotation.y = 0;
  G.camera.fov = 38; G.camera.position.set(0, 10.8, 19); G.camera.lookAt(0, 9.4, 0); G.camera.updateProjectionMatrix();
  G.renderer.render(G.scene, G.camera); return G.renderer.domElement.toDataURL('image/jpeg', 0.8);
};
