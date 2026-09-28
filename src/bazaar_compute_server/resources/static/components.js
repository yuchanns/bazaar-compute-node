document.addEventListener('alpine:init', () => {
  const stateComponent = (attribute, kind) => ({
    status: 'running', labels: {}, online: false, queued: null, last_event_at_ms: null,
    subscription: null, element: null, descriptor: null, syncHandler: null,
    init() {
      this.element = this.$el;
      this.syncHandler = () => this.sync();
      document.addEventListener('htmx:after:swap', this.syncHandler);
      this.sync();
    },
    sync() {
      const raw = this.element.dataset[attribute];
      if (!raw || raw === this.descriptor) return;
      this.descriptor = raw;
      const data = JSON.parse(raw);
      this.labels = data.labels || {};
      Object.assign(this, data.seen);
      if (this.subscription) this.subscription.update(data);
      else this.subscription = Alpine.store('poll').subscribe(data, change => {
        Object.assign(this, change.data);
        this.element.dataset[attribute] = this.descriptor = JSON.stringify({...JSON.parse(this.element.dataset[attribute]), seen: change.state});
        if (kind === 'computer') this.element.dispatchEvent(new CustomEvent('computer-state', {bubbles: true, detail: change.data}));
        return {seen: change.state};
      });
    },
    destroy() {
      this.subscription?.unsubscribe();
      document.removeEventListener('htmx:after:swap', this.syncHandler);
    },
  });
  Alpine.data('agentStatus', () => stateComponent('agent', 'agent'));
  Alpine.data('computerState', () => stateComponent('computer', 'computer'));
  Alpine.bind('agentLink', () => ({
    ':class'() { return {off: this.status === 'offline'}; },
    ':aria-disabled'() { return this.status === 'offline' ? 'true' : null; },
    ':tabindex'() { return this.status === 'offline' ? -1 : 0; },
    '@click.capture'(event) {
      if (this.status === 'offline' && !event.target.closest('.gear')) {
        event.preventDefault(); event.stopImmediatePropagation();
      }
    },
  }));
  Alpine.data('relativeTime', () => ({
    text: '', cancel: null, element: null,
    init() {
      this.element = this.$el;
      this.cancel = Alpine.store('poll').clock(now => {
        const {at, mode, labels} = JSON.parse(this.element.dataset.time);
        const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const parts = value => Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
          timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
        }).formatToParts(value).map(part => [part.type, part.value]));
        let text;
        if (mode === 'ago') {
          const seconds = Math.max(0, Math.floor((now - at) / 1000));
          const [word, n] = seconds < 10 ? ['just_now', 0] : seconds < 60 ? ['seconds_ago', seconds] : seconds < 3600 ? ['minutes_ago', Math.floor(seconds / 60)] : seconds < 86400 ? ['hours_ago', Math.floor(seconds / 3600)] : ['days_ago', Math.floor(seconds / 86400)];
          text = labels[word].replace('{n}', n);
        } else {
          const a = parts(at), b = parts(now);
          const date = `${a.year}-${a.month}-${a.day}`;
          const today = `${b.year}-${b.month}-${b.day}`;
          const clock = `${a.hour}:${a.minute}`;
          if (mode === 'clock') text = `${date === today ? '' : date + ' '}${clock}:${a.second}`;
          else {
            const days = (Date.UTC(+a.year, +a.month - 1, +a.day) - Date.UTC(+b.year, +b.month - 1, +b.day)) / 86400000;
            text = days === 0 ? labels.today.replace('{clock}', clock) : days === 1 ? labels.tomorrow.replace('{clock}', clock) : `${date} ${clock}`;
          }
        }
        if (this.text !== text) this.text = text;
      });
    },
    destroy() { this.cancel?.(); },
  }));

  Alpine.data('application', () => ({stale: false, unreachable: false, pending: new Set()}));
  Alpine.bind('connection', () => ({
    '@poll-online.window'() { this.unreachable = false; },
    '@poll-offline.window'() { this.unreachable = true; },
    '@stale.window'() { this.stale = true; },
    '@htmx:before:request.window'(event) {
      if (this.stale) { event.preventDefault(); return; }
      const ctx = event.detail.ctx;
      if (ctx.target?.id === 'main') this.pending.add(ctx);
    },
    '@htmx:finally:request.window'(event) { this.pending.delete(event.detail.ctx); },
    '@htmx:error.window'(event) {
      if (event.detail?.error instanceof TypeError) this.unreachable = true;
    },
    '@htmx:after:swap.window'() { this.unreachable = false; },
  }));
  Alpine.data('dialog', (open = true) => ({open}));
  Alpine.bind('dialog', () => ({
    'x-show'() { return this.open; },
    '@keydown.escape.stop.prevent'() { this.open = false; },
    '@dialog-open.window'(event) {
      if (event.detail === this.$el.id) this.open = true;
    },
  }));
  Alpine.bind('opener', () => ({
    '@click'() { this.$el.focus({preventScroll: true}); },
  }));
  Alpine.data('conversation', () => ({
    open: true,
    members: [],
    init() {
      this.$watch('open', () => this.$nextTick(() => window.dispatchEvent(new Event('drawer-state'))));
      this.$nextTick(() => this.gather());
    },
    gather() {
      const members = new Map();
      for (const turn of document.querySelectorAll('#history .turn')) {
        members.set(turn.dataset.identity, {
          id: turn.dataset.identity,
          name: turn.dataset.speaker,
          avatar: turn.querySelector('.av').innerHTML,
        });
      }
      this.members = [...members.values()];
    },
  }));
  Alpine.data('roleSharing', () => ({
    roles: [], grants: {}, original: {}, permissions: [], left: [], right: [],
    leftSearch: '', rightSearch: '', labels: {}, saving: false, leaveHandler: null,
    init() {
      const data = JSON.parse(this.$el.dataset.sharing);
      this.roles = data.roles;
      this.original = data.grants;
      this.grants = structuredClone(data.grants);
      this.labels = Object.fromEntries([...this.$el.querySelectorAll('[data-point]')].map(el => [el.dataset.point, el.textContent]));
      this.permissions = Object.keys(this.labels).filter(point => point === 'agents.view');
      this.leaveHandler = event => {
        if (!this.dirty || this.saving || !(event.target instanceof Element)) return;
        const navigation = event.target.closest('a[href], .tab[hx-get]');
        if (!navigation || this.$el.contains(navigation)) return;
        if (!window.confirm(this.$el.dataset.leave)) {
          event.preventDefault();
          event.stopImmediatePropagation();
        }
      };
      document.addEventListener('click', this.leaveHandler, true);
    },
    get available() { return this.roles.filter(role => !this.grants[role.id]?.length && role.name.toLowerCase().includes(this.leftSearch.toLowerCase())); },
    get shared() { return this.roles.filter(role => this.grants[role.id]?.length && role.name.toLowerCase().includes(this.rightSearch.toLowerCase())); },
    get changes() {
      return Object.fromEntries(this.roles.filter(role =>
        JSON.stringify([...(this.grants[role.id] || [])].sort()) !== JSON.stringify([...(this.original[role.id] || [])].sort())
      ).map(role => [role.id, this.grants[role.id] || []]));
    },
    get dirty() { return Object.keys(this.changes).length > 0; },
    grant() { for (const id of this.left) this.grants[id] = [...this.permissions]; this.left = []; },
    revoke() { for (const id of this.right) delete this.grants[id]; this.right = []; },
    apply() { for (const id of this.right) this.grants[id] = [...this.permissions]; this.right = []; },
    reset() { this.grants = structuredClone(Alpine.raw(this.original)); this.left = []; this.right = []; },
    destroy() { document.removeEventListener('click', this.leaveHandler, true); },
  }));
  Alpine.data('profileTabs', () => ({
    tab: '',
    pending: null,
    element: null,
    init() { this.element = this.$el; this.tab = this.element.dataset.tab; },
    before(event) {
      if (event.detail.ctx.target?.id === 'profile-body') this.pending = event.detail.ctx;
    },
    after(event) {
      if (event.detail.ctx.target?.contains(this.element)) this.tab = this.element.dataset.tab;
    },
    finished(event) {
      if (Alpine.raw(this.pending) === event.detail.ctx) this.pending = null;
    },
  }));
  Alpine.data('directory', () => ({
    open: false,
    loaded: false,
    loading: false,
    toggle() {
      this.open = !this.open;
      if (this.open && !this.loaded && !this.loading) this.$dispatch('directory-load');
    },
    finished(event) {
      this.loading = false;
      this.loaded = event.detail.ctx.status === 'swapped' && !event.detail.ctx.target.querySelector('.err');
    },
  }));
  Alpine.data('selection', () => ({
    selected: '',
    init() { this.selected = this.$el.dataset.selected; this.$nextTick(() => this.names()); },
    names() {
      for (const row of this.$el.querySelectorAll('[data-contact]')) {
        this.$dispatch('contact-name', JSON.parse(row.dataset.contact));
      }
    },
  }));
  Alpine.data('contactName', () => ({
    name: '', thread: '', url: '',
    init() { this.name = this.$el.dataset.name; this.thread = this.$el.dataset.thread; this.url = this.$el.getAttribute('hx-get') || ''; },
    update(event) {
      if (event.detail.thread !== this.thread) return;
      this.name = event.detail.name;
      if (this.url) {
        const url = new URL(this.url, location.href);
        url.searchParams.set('name', this.name);
        this.url = url.pathname + url.search;
      }
    },
  }));
  Alpine.data('submission', () => ({
    ready: false,
    busy: false,
    init() { this.ready = Boolean(window.htmx); },
  }));
  Alpine.bind('submission', () => ({
    '@htmx:before:request'(event) {
      if (event.detail.ctx.sourceElement === this.$el) this.busy = true;
    },
    '@htmx:finally:request'(event) {
      if (event.detail.ctx.sourceElement === this.$el) this.busy = false;
    },
  }));
  Alpine.data('navigation', () => ({
    busy: false,
    async open() {
      this.busy = true;
      const href = this.$el.dataset.href;
      try { await htmx.ajax('GET', href, {source: this.$el, target: '#main', swap: 'innerHTML', push: href}); }
      finally { this.busy = false; }
    },
  }));
  Alpine.data('activity', () => ({
    ...stateComponent('agent', 'agent'),
    left: 0,
    top: 0,
    move(event) {
      const card = this.$el.closest('[data-activity]').querySelector('.card');
      this.left = Math.min(event.clientX + 14, innerWidth - card.offsetWidth - 8);
      this.top = Math.min(event.clientY + 14, innerHeight - card.offsetHeight - 8);
    },
  }));
  Alpine.data('history', () => ({
    element: null,
    pending: new Map(),
    active: true,
    init() {
      this.element = this.$el;
      this.$nextTick(() => { if (this.active) this.element.scrollTop = this.element.scrollHeight; });
    },
    destroy() { this.active = false; this.pending.clear(); },
    before(event) {
      const {ctx} = event.detail;
      if (!this.element.contains(ctx.sourceElement)) return;
      this.pending.set(ctx, {
        earlier: ctx.sourceElement.classList.contains('edge'),
        top: this.element.scrollTop,
        height: this.element.scrollHeight,
        bottom: this.element.scrollHeight - this.element.scrollTop - this.element.clientHeight < 48,
      });
    },
    after(event) {
      const position = this.pending.get(event.detail.ctx);
      if (!position) return;
      this.$nextTick(() => {
        if (!this.active) return;
        if (position.earlier) this.element.scrollTop = position.top + this.element.scrollHeight - position.height;
        else if (position.bottom) this.element.scrollTop = this.element.scrollHeight;
      });
    },
  }));
  Alpine.data('clipboard', () => ({
    copied: false,
    timer: null,
    async copy() {
      try {
        await navigator.clipboard.writeText(this.$el.previousElementSibling.textContent);
        if (!this.$el.isConnected) return;
        clearTimeout(this.timer);
        this.copied = true;
        this.timer = setTimeout(() => { this.copied = false; this.timer = null; }, 1500);
      } catch {
        this.copied = false;
      }
    },
    destroy() { clearTimeout(this.timer); },
  }));
}, {once: true});
