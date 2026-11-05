// Shared icons and tiny renderers for the Bazaar mockups.
const ICONS = {
  overview: '<rect width="7" height="9" x="3" y="3" rx="1.5"/><rect width="7" height="5" x="14" y="3" rx="1.5"/><rect width="7" height="9" x="14" y="12" rx="1.5"/><rect width="7" height="5" x="3" y="16" rx="1.5"/>',
  bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
  chats: '<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
  store: '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/><path d="M22 7v3a2 2 0 0 1-2 2 2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  userplus: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" x2="19" y1="8" y2="14"/><line x1="22" x2="16" y1="11" y2="11"/>',
  wifioff: '<path d="M12 20h.01"/><path d="M8.5 16.43a5 5 0 0 1 7 0"/><path d="M5 12.86a10 10 0 0 1 5.17-2.69"/><path d="M19 12.86a10 10 0 0 0-2-1.52"/><path d="M2 8.82a15 15 0 0 1 4.18-2.64"/><path d="M22 8.82a15 15 0 0 0-11.29-3.76"/><path d="m2 2 20 20"/>',
  send: '<path d="M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.63l-19 6.5a.5.5 0 0 0-.02.93l7.93 3.18a2 2 0 0 1 1.11 1.11z"/><path d="m21.85 2.15-10.94 10.94"/>',
  msg: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  alarm: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6"/><path d="m22 6-3-3"/><path d="M6.38 18.7 4 21"/><path d="M17.64 18.67 20 21"/>',
  sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  coins: '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>',
  activity: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/><path d="m15 5 4 4"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  key: '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  branch: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" x2="6" y1="9" y2="21"/>',
  reply: '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  out: '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  book: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  okc: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
  at: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
};

const FILLED = {
  tg: '<path d="M21.5 3.6 2.9 10.8c-1.3.5-1.3 1.2-.2 1.5l4.8 1.5 1.8 5.6c.2.6.1.9.8.9.5 0 .7-.2 1-.5l2.4-2.3 4.9 3.6c.9.5 1.5.2 1.8-.8l3.3-15.4c.3-1.3-.5-1.9-1.4-1.5zM8.6 13.4l9.6-6c.5-.3.9-.1.5.2l-8.2 7.4-.3 3.4z"/>',
  apple: '<path d="M16.4 12.6c0-2.3 1.9-3.4 2-3.5-1.1-1.6-2.8-1.8-3.4-1.8-1.4-.1-2.8.9-3.5.9-.7 0-1.8-.9-3-.8-1.5 0-3 .9-3.8 2.3-1.6 2.8-.4 7 1.2 9.3.8 1.1 1.7 2.4 2.9 2.3 1.2 0 1.6-.7 3-.7s1.8.7 3 .7c1.2 0 2-1.1 2.8-2.3.9-1.3 1.2-2.5 1.3-2.6-.1 0-2.4-.9-2.5-3.8zM14.1 5.8c.6-.8 1.1-1.8.9-2.8-.9 0-2 .6-2.6 1.4-.6.7-1.1 1.7-.9 2.7 1 .1 2-.5 2.6-1.3z"/>',
  linux: '<path d="M12 2c-2.2 0-3.6 1.9-3.6 4.6 0 1.5.3 2.4-.6 3.9-1 1.6-2.6 3.6-2.6 6.2 0 .9.3 1.6.6 2.1-.6.4-1.6.9-1.6 1.6 0 .9 1.5 1.1 3.2 1.4 1.1.2 1.8.6 2.6.6.9 0 1.5-.5 2-.5s1.1.5 2 .5c.8 0 1.5-.4 2.6-.6 1.7-.3 3.2-.5 3.2-1.4 0-.7-1-1.2-1.6-1.6.3-.5.6-1.2.6-2.1 0-2.6-1.6-4.6-2.6-6.2-.9-1.5-.6-2.4-.6-3.9C15.6 3.9 14.2 2 12 2zm-1.6 4.2c.5 0 .8.5.8 1.1s-.3 1.1-.8 1.1-.8-.5-.8-1.1.3-1.1.8-1.1zm3.2 0c.5 0 .8.5.8 1.1s-.3 1.1-.8 1.1-.8-.5-.8-1.1.3-1.1.8-1.1zM12 9.2c.9 0 2 .6 2 1.1 0 .4-1.1 1.1-2 1.1s-2-.7-2-1.1c0-.5 1.1-1.1 2-1.1z"/>',
  windows: '<path d="M3 5.5 10.5 4.5v7H3zM11.5 4.4 21 3v8.5h-9.5zM3 12.5h7.5v7L3 18.5zM11.5 12.5H21V21l-9.5-1.4z"/>',
};

// logo D, the cluster of striped tents (tents.js draws the same)
const MARK = `<svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><defs><clipPath id="tent0"><path d="M8.6 13C7.119999999999999 17.95 3.5679999999999987 20.56 1.1999999999999993 22H16C13.632000000000001 20.56 10.08 17.95 8.6 13Z"/></clipPath></defs>
        <rect x="8.1" y="9" width="1" height="4.4" rx=".4" fill="var(--text-2)"/><path d="M9.1 9.1 L12.2 10.2 L9.1 11.3Z" fill="#f2a93b"/>
        <g clip-path="url(#tent0)"><rect x="1.1999999999999993" y="13" width="3.72" height="9.1" fill="#2fb996"/><rect x="4.8999999999999995" y="13" width="3.72" height="9.1" fill="#bfeee0"/><rect x="8.6" y="13" width="3.72" height="9.1" fill="#2fb996"/><rect x="12.3" y="13" width="3.72" height="9.1" fill="#bfeee0"/></g><path d="M16 22 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0Z" fill="#2fb996"/><defs><clipPath id="tent1"><path d="M23.4 13C21.919999999999998 17.95 18.368 20.56 15.999999999999998 22H30.799999999999997C28.432 20.56 24.88 17.95 23.4 13Z"/></clipPath></defs>
        <rect x="22.9" y="9" width="1" height="4.4" rx=".4" fill="var(--text-2)"/><path d="M23.9 9.1 L27 10.2 L23.9 11.3Z" fill="#f2a93b"/>
        <g clip-path="url(#tent1)"><rect x="15.999999999999998" y="13" width="3.72" height="9.1" fill="#7f8ef8"/><rect x="19.7" y="13" width="3.72" height="9.1" fill="#dfe3ff"/><rect x="23.4" y="13" width="3.72" height="9.1" fill="#7f8ef8"/><rect x="27.1" y="13" width="3.72" height="9.1" fill="#dfe3ff"/></g><path d="M30.799999999999997 22 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0 a0.925 0.925 0 0 1 -1.85 0Z" fill="#7f8ef8"/>
      <defs><clipPath id="tent2"><path d="M16 7.5C14.28 15.475000000000001 10.152000000000001 19.68 7.4 22H24.6C21.848 19.68 17.72 15.475000000000001 16 7.5Z"/></clipPath></defs>
        <rect x="15.5" y="3.5" width="1" height="4.4" rx=".4" fill="var(--text-2)"/><path d="M16.5 3.6 L19.6 4.7 L16.5 5.8Z" fill="#ef5a5f"/>
        <g clip-path="url(#tent2)"><rect x="7.4" y="7.5" width="4.319999999999999" height="14.6" fill="#f2a93b"/><rect x="11.7" y="7.5" width="4.319999999999999" height="14.6" fill="#fff1d2"/><rect x="16" y="7.5" width="4.319999999999999" height="14.6" fill="#f2a93b"/><rect x="20.299999999999997" y="7.5" width="4.319999999999999" height="14.6" fill="#fff1d2"/></g><path d="M24.6 22 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0 a1.075 1.075 0 0 1 -2.15 0Z" fill="#f2a93b"/>
      <rect x="1.5" y="25" width="29" height="2.8" rx="1.4" fill="var(--text-2)"/></svg>`;

function icon(name, cls = '', style = '') {
  if (FILLED[name]) return `<svg class="i ${cls}" viewBox="0 0 24 24" style="fill: currentColor; stroke: none; ${style}">${FILLED[name]}</svg>`;
  return `<svg class="i ${cls}" viewBox="0 0 24 24" style="${style}">${ICONS[name]}</svg>`;
}

function logo(name, cls = '', style = '') {
  const l = LOGOS[name];
  return `<svg class="logo-${name} ${cls}" viewBox="${l.view}" style="fill: currentColor; ${style}">${l.body}</svg>`;
}

function spark(values, w = 84, h = 26) {
  const max = Math.max(...values, 1);
  const step = w / (values.length - 1);
  const pts = values.map((v, i) => [i * step, h - 2 - (v / max) * (h - 6)]);
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const fill = `${line} L${w} ${h} L0 ${h} Z`;
  return `<path class="fill" d="${fill}"/><path class="line" d="${line}"/>`;
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-i]').forEach(el => {
    el.outerHTML = icon(el.dataset.i, el.className || '', el.getAttribute('style') || '');
  });
  document.querySelectorAll('[data-l]').forEach(el => {
    el.outerHTML = logo(el.dataset.l, el.className || '', el.getAttribute('style') || '');
  });
  document.querySelectorAll('[data-mark]').forEach(el => { el.outerHTML = MARK; });
  document.querySelectorAll('svg.spark[data-v]').forEach(el => {
    const w = Number(el.getAttribute('width') || 84), h = Number(el.getAttribute('height') || 26);
    el.setAttribute('viewBox', `0 0 ${w} ${h}`);
    el.innerHTML = spark(el.dataset.v.split(',').map(Number), w, h);
  });
});

function sidebar(active, theme) {
  const nav = (key, name, ic, extra = '') =>
    `<div class="nav${key === active ? ' on' : ''}">${icon(ic)}<span>${name}</span>${extra}</div>`;
  return `
    <div class="brand">${MARK}<span class="word">Bazaar</span></div>
    <div class="jump">${icon('search', 'sm')}<span>搜索或跳转…</span><kbd>⌘K</kbd></div>
    <div class="nav-group">工作台</div>
    ${nav('overview', '总览', 'overview')}
    ${nav('agents', '智能体', 'bot', '<span class="count">9</span>')}
    ${nav('chats', '会话', 'chats', '<span class="badge">2</span>')}
    ${nav('computers', '电脑', 'monitor', '<span class="count">4</span>')}
    <div class="nav-group">扩展</div>
    ${nav('market', '市场', 'store', '<span class="soon">即将上线</span>')}
    <div class="nav-group">正在工作</div>
    <div class="pin"><span class="lantern work"></span><span>有马佳奈</span><span class="t">3:12</span></div>
    <div class="pin"><span class="lantern work"></span><span>Vincent</span><span class="t">6:04</span></div>
    <div class="pin"><span class="lantern work"></span><span>啊夸</span><span class="t">0:48</span></div>
    <div class="grow"></div>
    ${nav('settings', '设置', 'settings')}
    <div class="me">
      <div class="av sm h1">Y</div>
      <div class="who"><b>yuchanns</b><span>管理员</span></div>
      <div class="theme"><span class="${theme === 'night' ? 'on' : ''}">${icon('moon', 'xs')}</span><span class="${theme === 'day' ? 'on' : ''}">${icon('sun', 'xs')}</span></div>
    </div>`;
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-side]').forEach(el => {
    el.innerHTML = sidebar(el.dataset.side, document.documentElement.dataset.theme);
  });
}, { once: true });

// ?theme=day renders the same screen in the day theme
{
  const theme = new URLSearchParams(location.search).get('theme');
  if (theme) document.documentElement.dataset.theme = theme;
}
