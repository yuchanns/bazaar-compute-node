// Logo D: the bazaar's skyline, many small striped tents rather than one spire.
const awning = (x, y, w, h, n, fill) => {
  const r = w / (2 * n);
  return `<path d="M${x} ${y}H${x + w}V${y + h}${` a${r} ${r} 0 0 1 ${-2 * r} 0`.repeat(n)}Z" fill="${fill}"/>`;
};
const C = { saffron: '#f2a93b', jade: '#2fb996', indigo: '#7f8ef8', crimson: '#ef5a5f', plum: '#b784f5', cream: '#fff1d2' };
const MARKS = {
  // D: the bazaar's skyline, many small striped tents rather than one spire
  tents: () => {
    let n = 0;
    const tent = (ax, ay, hw, by, a, b, flag) => {
      const id = `tent${n++}`, h = by - ay, k = 4, w = 2 * hw / k, r = w / 4;
      const roof = `M${ax} ${ay}C${ax - hw * .2} ${ay + h * .55} ${ax - hw * .68} ${by - h * .16} ${ax - hw} ${by}H${ax + hw}C${ax + hw * .68} ${by - h * .16} ${ax + hw * .2} ${ay + h * .55} ${ax} ${ay}Z`;
      const stripes = Array.from({ length: k }, (_, i) => `<rect x="${ax - hw + i * w}" y="${ay}" width="${w + .02}" height="${h + .1}" fill="${i % 2 ? b : a}"/>`).join('');
      const hem = `M${ax + hw} ${by}${` a${r} ${r} 0 0 1 ${-2 * r} 0`.repeat(2 * k)}Z`;
      return `<defs><clipPath id="${id}"><path d="${roof}"/></clipPath></defs>
        <rect x="${ax - .5}" y="${ay - 4}" width="1" height="4.4" rx=".4" fill="var(--text-2)"/><path d="M${ax + .5} ${ay - 3.9} L${ax + 3.6} ${ay - 2.8} L${ax + .5} ${ay - 1.7}Z" fill="${flag}"/>
        <g clip-path="url(#${id})">${stripes}</g><path d="${hem}" fill="${a}"/>`;
    };
    return `${tent(8.6, 13, 7.4, 22, C.jade, '#bfeee0', C.saffron)}${tent(23.4, 13, 7.4, 22, C.indigo, '#dfe3ff', C.saffron)}
      ${tent(16, 7.5, 8.6, 22, C.saffron, C.cream, C.crimson)}
      <rect x="1.5" y="25" width="29" height="2.8" rx="1.4" fill="var(--text-2)"/>`;
  }
};
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-tents]').forEach(el => {
    el.outerHTML = `<svg class="mark" viewBox="0 0 32 32" aria-hidden="true">${MARKS.tents()}</svg>`;
  });
});
