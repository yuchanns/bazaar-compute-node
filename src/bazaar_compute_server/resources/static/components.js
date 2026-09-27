document.addEventListener('alpine:init', () => {
  Alpine.data('application', () => ({stale: false, unreachable: false}));
  Alpine.bind('connection', () => ({
    '@stale.window'() { this.stale = true; },
    '@htmx:before:request.window'(event) {
      if (this.stale) event.preventDefault();
    },
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
    init() { this.$nextTick(() => this.gather()); },
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
    init() { this.selected = this.$el.dataset.selected; },
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
