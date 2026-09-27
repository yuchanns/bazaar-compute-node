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
