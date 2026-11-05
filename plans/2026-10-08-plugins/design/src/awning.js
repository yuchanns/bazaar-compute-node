// The stall cards' awning, drawn like the street's: stripes of the stall's hue
// and cream, a dark band under the front edge, a scalloped hem with the
// underside of each fold in shade, and the ridge pole along the top.
let awnings = 0;
function awningSVG() {
  const id = `awning-${awnings++}`;
  return `<svg class="awning-svg" width="100%" height="36" aria-hidden="true"><defs>
    <pattern id="${id}" width="36" height="33" patternUnits="userSpaceOnUse">
      <rect x="0" y="0" width="18" height="22" style="fill: var(--stripe)"/>
      <rect x="18" y="0" width="18" height="22" style="fill: var(--awning-alt)"/>
      <rect x="0" y="17" width="36" height="5" style="fill: rgba(0, 0, 0, .16)"/>
      <path d="M0 22 A9 8.2 0 0 0 18 22 Z" style="fill: var(--stripe)"/>
      <path d="M18 22 A9 8.2 0 0 0 36 22 Z" style="fill: var(--awning-alt)"/>
      <path d="M3.2 26.9 A6.3 3.2 0 0 0 14.8 26.9 A9 8.2 0 0 1 3.2 26.9 Z" style="fill: rgba(0, 0, 0, .24)"/>
      <path d="M21.2 26.9 A6.3 3.2 0 0 0 32.8 26.9 A9 8.2 0 0 1 21.2 26.9 Z" style="fill: rgba(0, 0, 0, .16)"/>
    </pattern></defs>
    <rect y="3" width="100%" height="33" fill="url(#${id})"/>
    <rect width="100%" height="5" rx="2.5" style="fill: var(--awning-pole)"/>
  </svg>`;
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.awning').forEach(el => { el.outerHTML = awningSVG(); });
});
