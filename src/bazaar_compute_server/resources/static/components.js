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
