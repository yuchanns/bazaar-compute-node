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
  Alpine.store('history', {
    key: '', version: 0, reading: null, requests: new Map(),
    advance(key, message = null) {
      this.version++;
      for (const ctx of this.requests.keys()) ctx.request.abort();
      this.key = key;
      this.reading = message ? {message, offset: null} : null;
    },
  });
  document.addEventListener('htmx:before:request', event => {
    const {ctx} = event.detail, state = Alpine.store('history');
    const url = new URL(ctx.request.action, location.href);
    const navigation = ['chat', 'main'].includes(ctx.target?.id) || ctx.target === document.body;
    const history = ctx.sourceElement?.closest('#history');
    if (!navigation && !history) return;
    const key = url.pathname.replace(/\/messages$/, '');
    if (navigation) state.advance(key, url.searchParams.get('focus'));
    else if (ctx.target?.id === 'history' && key === state.key) {
      if (state.reading) {
        url.searchParams.set('latest', state.reading.message);
        if (state.reading.offset !== null) url.searchParams.delete('focus');
      } else if (history.matches('[x-data="history"]')) url.searchParams.delete('focus');
      ctx.request.action = url.href;
    }
    state.requests.set(ctx, state.version);
  });
  for (const name of ['htmx:before:response', 'htmx:after:request', 'htmx:before:swap']) {
    document.addEventListener(name, event => {
      const {ctx} = event.detail, state = Alpine.store('history');
      if (state.requests.has(ctx) && state.requests.get(ctx) !== state.version) event.preventDefault();
    });
  }
  document.addEventListener('htmx:finally:request', event => Alpine.store('history').requests.delete(event.detail.ctx));
  Alpine.store('search', {states: {}});
  Alpine.data('messageSearch', () => ({
    s: null, scope: '', base: '', element: null, trigger: null, focusTarget: null,
    timer: null, composing: false, controller: null, optionsController: null,
    viewport: null, viewportHandler: null, swapHandler: null, active: true,
    observer: null, width: 0, renderedScope: '', renderedVersion: -1,
    bounds: '', panelBounds: '', labels: {},
    init() {
      this.element = this.$el;
      this.labels = JSON.parse(this.element.dataset.labels);
      this.viewportHandler = () => this.measure();
      this.swapHandler = () => this.sync();
      this.observer = new ResizeObserver(entries => {
        if (!this.active || !this.s || !this.$refs.results.clientHeight) return;
        this.rememberResults();
        let changed = false;
        for (const entry of entries) {
          if (entry.target === this.$refs.results) {
            if (this.width !== entry.contentRect.width) {
              this.width = entry.contentRect.width;
              for (const item of this.s.items) item.height = 128;
              changed = true;
            }
          } else {
            const item = this.s.items[Number(entry.target.dataset.index)];
            const height = entry.target.offsetHeight + 2;
            if (item && height > 2 && item.height !== height) { item.height = height; changed = true; }
          }
        }
        if (changed) this.positions();
        this.rendered();
      });
      this.observer.observe(this.$refs.results);
      document.addEventListener('htmx:after:swap', this.swapHandler);
      this.sync();
      this.$watch('s.open', open => {
        if (open) this.listenViewport(); else this.stopViewport();
      });
      this.$watch('s.calendar', () => this.$nextTick(() => this.rendered()));
      this.$watch('s.panel', () => this.$nextTick(() => this.rendered()));
    },
    sync() {
      const scope = this.element.dataset.scope;
      const changed = this.scope !== scope;
      if (changed) {
        this.cancel(); this.focusTarget = null;
        this.scope = scope;
        this.base = `/agents/${scope}/search`;
        const states = Alpine.store('search').states;
        states[scope] ||= {
          open: false, panel: '', calendar: '', query: '', context: null, target: '', targetLabel: '',
          sender: '', senderLabel: '', time: '', after: '', before: '', sort: 'time',
          draftTime: '', draftAfter: '', draftBefore: '', month: '', cursor: '',
          html: '', items: [], ends: [], count: 0, more: false, offset: 0, selected: -1, scroll: 0,
          version: 0, resultsVersion: 0, anchor: null, busy: false, error: '',
          contacts: {items: [], offset: 0, more: true, loaded: false, busy: false, error: ''},
          senders: [], contactCounts: {},
        };
        this.s = states[scope];
        if (this.s.open) this.listenViewport(); else this.stopViewport();
      }
      this.$nextTick(() => {
        this.rendered();
        if (this.s.open && this.focusTarget && !this.element.contains(document.activeElement)) {
          if (this.focusTarget.isConnected) this.focusTarget.focus({preventScroll: true});
          else if (this.s.calendar) this.focusDate();
        }
        if (changed && this.s.open) {
          if (this.s.calendar) this.focusDate();
          else if (!this.s.panel) this.$refs.input.focus({preventScroll: true});
        }
        if (changed && this.s.version !== this.s.resultsVersion && (this.s.query.trim() || this.s.target || this.s.sender || this.s.time)) this.load(false);
      });
    },
    show(event) {
      this.trigger = event?.detail instanceof Element ? event.detail : event?.target;
      const chat = document.getElementById('chat'), target = chat?.dataset.target || '';
      if (this.s.context !== target) {
        this.s.context = target; this.s.target = target;
        this.s.targetLabel = target ? chat.querySelector('.heading').textContent.trim() : '';
        this.s.panel = ''; this.s.calendar = ''; this.change();
      }
      this.s.open = true;
      this.listenViewport();
      this.$nextTick(() => {
        this.rendered();
        if (!this.s.panel) this.$refs.input.focus({preventScroll: true});
      });
    },
    close() {
      this.rememberResults();
      this.$refs.input.blur();
      this.s.open = false;
      this.stopViewport();
      this.$nextTick(() => {
        const trigger = this.trigger?.isConnected ? this.trigger : document.querySelector('.search-open');
        trigger?.focus({preventScroll: true});
      });
    },
    escape() {
      if (this.s.calendar) {
        const field = this.s.calendar; this.s.calendar = '';
        this.$nextTick(() => this.element.querySelectorAll('.search-date')[field === 'after' ? 0 : 1].focus({preventScroll: true}));
      }
      else if (this.s.panel === 'time') this.cancelDates();
      else if (this.s.panel) { const panel = this.s.panel; this.s.panel = ''; this.focusFilter(panel); }
      else this.close();
    },
    listenViewport() {
      this.stopViewport();
      this.viewport = window.visualViewport;
      this.viewport?.addEventListener('resize', this.viewportHandler);
      this.viewport?.addEventListener('scroll', this.viewportHandler);
      window.addEventListener('resize', this.viewportHandler);
      this.measure();
    },
    stopViewport() {
      this.viewport?.removeEventListener('resize', this.viewportHandler);
      this.viewport?.removeEventListener('scroll', this.viewportHandler);
      window.removeEventListener('resize', this.viewportHandler);
      this.viewport = null;
    },
    measure() {
      const v = this.viewport;
      this.bounds = `--search-width:${v?.width ?? innerWidth}px;--search-height:${v?.height ?? innerHeight}px;--search-left:${v?.offsetLeft ?? 0}px;--search-top:${v?.offsetTop ?? 0}px`;
      this.$nextTick(() => this.rendered());
    },
    cancel() {
      clearTimeout(this.timer); this.timer = null;
      this.controller?.abort(); this.controller = null;
      this.optionsController?.abort(); this.optionsController = null;
      if (this.s) {
        this.s.busy = false;
        this.s.contacts.busy = false;
      }
    },
    change(delay = 0) {
      clearTimeout(this.timer);
      this.controller?.abort(); this.controller = null;
      this.s.version++; this.s.busy = false; this.s.error = '';
      if (!this.s.query.trim() && !this.s.target && !this.s.sender && !this.s.time) {
        this.s.html = ''; this.s.items = []; this.s.ends = []; this.s.count = 0; this.s.more = false; this.s.senders = []; this.s.contactCounts = {};
        this.s.offset = 0; this.s.scroll = 0; this.s.anchor = null; this.s.selected = -1;
        this.$nextTick(() => this.rendered());
        return;
      }
      this.timer = setTimeout(() => { this.timer = null; this.load(false); }, delay);
    },
    input() { if (!this.composing) this.change(300); },
    startComposition() {
      this.composing = true;
      clearTimeout(this.timer);
      this.controller?.abort(); this.s.version++; this.s.busy = false;
    },
    endComposition() { this.composing = false; this.change(300); },
    async load(append) {
      if (this.s.busy || (append && (!this.s.more || this.s.resultsVersion !== this.s.version))) return;
      const state = this.s, scope = this.scope, version = state.version;
      const controller = this.controller = new AbortController();
      const params = new URLSearchParams({query: state.query, sort: state.sort, version, offset: append ? state.offset : 0});
      if (state.target) params.set('target', state.target);
      if (state.sender) params.set('sender', state.sender);
      let after = state.after, before = state.before;
      if (state.time === 'today' || state.time === 'week') {
        const date = new Date();
        before = this.dateKey(date);
        if (state.time === 'week') date.setDate(date.getDate() - 6);
        after = this.dateKey(date);
      }
      if (state.time && after) params.set('after_ms', new Date(`${after}T00:00:00`).getTime());
      if (state.time && before) { const date = new Date(`${before}T00:00:00`); date.setDate(date.getDate() + 1); params.set('before_ms', date.getTime() - 1); }
      state.busy = true;
      try {
        const response = await fetch(`${this.base}?${params}`, {signal: controller.signal, headers: this.headers()});
        if (!response.ok) throw new Error('refused');
        if (response.headers.get('HX-Trigger') === 'stale') { window.dispatchEvent(new Event('stale')); return; }
        const html = await response.text();
        if (!this.active || scope !== this.scope || version !== state.version) return;
        const fragment = document.createElement('template'); fragment.innerHTML = html;
        const page = fragment.content.querySelector('[data-search-page]');
        const data = JSON.parse(page.dataset.searchPage);
        if (data.scope !== scope || data.version !== version) return;
        const hits = page.querySelectorAll('.search-hit');
        const count = hits.length;
        const senders = new Map(append ? state.senders.map(item => [item.token, item]) : []);
        const contacts = append ? {...state.contactCounts} : {};
        for (const hit of hits) {
          if (hit.dataset.sender) senders.set(hit.dataset.sender, {token: hit.dataset.sender, label: hit.dataset.speaker, avatar: hit.dataset.avatar, count: (senders.get(hit.dataset.sender)?.count || 0) + 1});
          contacts[hit.dataset.target] = (contacts[hit.dataset.target] || 0) + 1;
        }
        state.senders = [...senders.values()];
        state.contactCounts = contacts;
        const items = [...hits].map(hit => ({html: hit.outerHTML, message: hit.dataset.message, height: 128}));
        state.items = append ? state.items.concat(items) : items;
        state.html = state.items.length ? '' : page.innerHTML;
        state.count = append ? state.count + count : count;
        state.more = Boolean(data.has_more); state.offset = data.next_offset; state.resultsVersion = version;
        if (data.sort) state.sort = data.sort;
        if (!append) { state.scroll = 0; state.anchor = null; state.selected = count ? 0 : -1; }
        this.positions();
        this.$nextTick(() => this.rendered());
      } catch (error) {
        if (error.name !== 'AbortError' && this.active && scope === this.scope && version === state.version) state.error = this.labels.failed;
      } finally {
        if (scope === this.scope && version === state.version) { state.busy = false; this.controller = null; }
      }
    },
    headers() { return {'HX-Request': 'true', 'X-Build': document.body.dataset.build, 'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone}; },
    rendered() {
      if (!this.active || !this.$refs.results) return;
      if (this.s.panel && this.$refs.panel?.offsetWidth) {
        const dialog = this.$refs.dialog.getBoundingClientRect();
        const button = this.element.querySelectorAll('.search-filters > button')[{contacts: 0, senders: 1, time: 2}[this.s.panel]].getBoundingClientRect();
        const width = this.$refs.panel.offsetWidth;
        const left = Math.max(8, Math.min(button.left - dialog.left, dialog.width - width - 8));
        const top = button.bottom - dialog.top + 7;
        const bottom = this.element.querySelector('.search-veil').getBoundingClientRect().bottom;
        this.panelBounds = `left:${left}px;top:${top}px;max-height:${Math.max(0, bottom - dialog.top - top - 12)}px`;
      }
      const results = this.$refs.results;
      if (!results.clientHeight) return;
      this.windowResults(this.s.scroll);
      results.scrollTop = this.s.scroll;
      if (this.s.anchor) {
        const row = [...results.querySelectorAll('.search-hit')].find(row => row.dataset.message === this.s.anchor.message);
        if (row) results.scrollTop += row.getBoundingClientRect().top - results.getBoundingClientRect().top - this.s.anchor.offset;
      }
      this.s.scroll = results.scrollTop;
      this.windowResults();
      this.markSelection();
    },
    positions() {
      let height = 0;
      this.s.ends = this.s.items.map(item => height += item.height);
      if (this.s.anchor) {
        const index = this.s.items.findIndex(item => item.message === this.s.anchor.message);
        if (index >= 0) this.s.scroll = Math.max(0, (index ? this.s.ends[index - 1] : 0) - this.s.anchor.offset);
      }
    },
    windowResults(scroll = this.$refs.results.scrollTop) {
      const results = this.$refs.results, items = this.$refs.items, ends = this.s.ends;
      if (!results.clientHeight) return;
      const bounds = [];
      for (const edge of [Math.max(0, scroll - results.clientHeight), scroll + results.clientHeight * 2]) {
        let left = 0, right = ends.length;
        while (left < right) {
          const middle = (left + right) >>> 1;
          if (ends[middle] <= edge) left = middle + 1; else right = middle;
        }
        bounds.push(left);
      }
      const start = bounds[0], end = Math.min(ends.length, bounds[1] + 1);
      const reset = this.renderedScope !== this.scope || this.renderedVersion !== this.s.resultsVersion;
      Alpine.mutateDom(() => {
        for (const row of [...items.children]) {
          const index = Number(row.dataset.index);
          if (reset || index < start || index >= end) {
            this.observer.unobserve(row); Alpine.destroyTree(row); row.remove();
          }
        }
        let row = items.firstElementChild;
        for (let index = start; index < end; index++) {
          if (Number(row?.dataset.index) === index) { row = row.nextElementSibling; continue; }
          const fragment = document.createElement('template'); fragment.innerHTML = this.s.items[index].html;
          const hit = fragment.content.firstElementChild;
          hit.dataset.index = index;
          hit.setAttribute('aria-posinset', index + 1);
          items.insertBefore(hit, row); Alpine.initTree(hit); htmx.process(hit); this.observer.observe(hit);
        }
        this.$refs.top.style.height = `${start ? ends[start - 1] : 0}px`;
        this.$refs.bottom.style.height = `${(ends.at(-1) || 0) - (end ? ends[end - 1] : 0)}px`;
      });
      this.renderedScope = this.scope; this.renderedVersion = this.s.resultsVersion;
      this.markSelection();
    },
    rememberResults() {
      const results = this.$refs.results, top = results.getBoundingClientRect().top;
      if (!results.clientHeight) return;
      this.s.scroll = results.scrollTop;
      const row = [...results.querySelectorAll('.search-hit')].find(row => row.getBoundingClientRect().bottom > top);
      this.s.anchor = row ? {message: row.dataset.message, offset: row.getBoundingClientRect().top - top} : null;
    },
    scrolled() {
      const results = this.$refs.results;
      this.rememberResults();
      this.windowResults();
      if (results.scrollHeight - results.scrollTop - results.clientHeight < 100) this.load(true);
    },
    markSelection() {
      this.$refs.items.querySelectorAll('.search-hit').forEach(row => {
        const selected = Number(row.dataset.index) === this.s.selected;
        row.classList.toggle('on', selected);
        row.setAttribute('aria-current', selected ? 'true' : 'false');
      });
    },
    key(event) {
      if (event.target !== this.$refs.input || this.composing || event.isComposing || this.s.panel || !['ArrowUp', 'ArrowDown', 'Enter'].includes(event.key)) return;
      if (!this.s.count) return;
      event.preventDefault();
      if (event.key !== 'Enter') {
        this.s.selected = Math.max(0, Math.min(this.s.count - 1, this.s.selected + (event.key === 'ArrowDown' ? 1 : -1)));
      }
      const results = this.$refs.results, top = this.s.selected ? this.s.ends[this.s.selected - 1] : 0, bottom = this.s.ends[this.s.selected];
      if (top < results.scrollTop) results.scrollTop = top;
      else if (bottom > results.scrollTop + results.clientHeight) results.scrollTop = bottom - results.clientHeight;
      this.windowResults();
      this.rememberResults();
      if (event.key === 'Enter') this.$refs.items.querySelector(`[data-index="${this.s.selected}"]`)?.click();
    },
    pick(event) {
      this.s.selected = Number(event.currentTarget.dataset.index);
      this.close();
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(event.currentTarget.href), message = url.searchParams.get('focus');
      const history = document.getElementById('history');
      const row = document.getElementById(`message-${message}`);
      if (history?.dataset.url && new URL(history.dataset.url, location.href).pathname === url.pathname + '/messages' && history.contains(row)) {
        event.preventDefault(); event.stopImmediatePropagation();
        history.dispatchEvent(new CustomEvent('history-focus', {bubbles: true, detail: message}));
        window.history.replaceState(window.history.state, '', url.href);
      }
    },
    togglePanel(panel) {
      this.$refs.input.blur();
      if (this.s.panel === panel) { if (panel === 'time') this.cancelDates(); else this.s.panel = ''; return; }
      this.s.calendar = ''; this.s.panel = panel;
      if (panel === 'time') {
        this.s.draftTime = this.s.time; this.s.draftAfter = this.s.after; this.s.draftBefore = this.s.before;
      } else if (panel === 'contacts') this.options();
    },
    focusFilter(kind) { this.$nextTick(() => this.element.querySelectorAll('.search-filters button')[{contacts: 0, senders: 1, time: 2}[kind]].focus({preventScroll: true})); },
    async options(append = false) {
      const state = this.s, scope = this.scope, options = state.contacts;
      if (options.busy || (append && !options.more)) return;
      const controller = this.optionsController = new AbortController();
      const params = new URLSearchParams({offset: append ? options.offset : 0});
      options.busy = true; options.error = '';
      try {
        const response = await fetch(`${this.base}/options?${params}`, {signal: controller.signal, headers: this.headers()});
        if (!response.ok) throw new Error('refused');
        if (response.headers.get('HX-Trigger') === 'stale') { window.dispatchEvent(new Event('stale')); return; }
        const data = await response.json();
        if (!this.active || scope !== this.scope) return;
        if (data.answer !== 'listed') { options.error = this.labels[data.answer] || this.labels.failed; return; }
        options.items = append ? options.items.concat(data.options) : data.options;
        options.more = data.has_more; options.offset = data.next_offset; options.loaded = true;
      } catch (error) {
        if (error.name !== 'AbortError' && this.active && scope === this.scope) options.error = this.labels.failed;
      } finally {
        if (this.optionsController === controller) { options.busy = false; this.optionsController = null; }
      }
    },
    choose(kind, item) {
      if (kind === 'contacts') {
        this.s.target = item.token; this.s.targetLabel = item.label;
      } else { this.s.sender = item.token; this.s.senderLabel = item.label; }
      this.s.panel = ''; this.focusFilter(kind); this.change();
    },
    clear() {
      this.s.target = ''; this.s.targetLabel = ''; this.s.sender = ''; this.s.senderLabel = '';
      this.s.time = ''; this.s.after = ''; this.s.before = ''; this.s.panel = ''; this.s.calendar = '';
      this.change();
    },
    get timeLabel() {
      if (this.s.time === 'custom') return [this.dateLabel(this.s.after), this.dateLabel(this.s.before)].filter(Boolean).join(' – ');
      return this.labels[this.s.time] || this.labels.anytime;
    },
    dateKey(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; },
    dateLabel(value) { return value ? new Intl.DateTimeFormat(document.documentElement.lang, {year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(`${value}T12:00:00`)) : ''; },
    openCalendar(field) {
      this.s.calendar = field; this.s.draftTime = 'custom';
      this.s.cursor = this.s[field === 'after' ? 'draftAfter' : 'draftBefore'] || this.dateKey(new Date());
      this.s.month = this.s.cursor.slice(0, 7);
      this.$nextTick(() => this.focusDate());
    },
    get monthLabel() { return new Intl.DateTimeFormat(document.documentElement.lang, {year: 'numeric', month: 'long'}).format(new Date(`${this.s.month || this.dateKey(new Date()).slice(0, 7)}-01T12:00:00`)); },
    get weekdays() { return Array.from({length: 7}, (_, index) => new Intl.DateTimeFormat(document.documentElement.lang, {weekday: 'narrow'}).format(new Date(2026, 4, 4 + index, 12))); },
    get days() {
      const first = new Date(`${this.s.month || this.dateKey(new Date()).slice(0, 7)}-01T12:00:00`);
      const offset = (first.getDay() + 6) % 7;
      const count = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
      const today = this.dateKey(new Date());
      return Array.from({length: Math.ceil((offset + count) / 7) * 7}, (_, index) => {
        const date = new Date(first.getFullYear(), first.getMonth(), index - offset + 1, 12);
        const value = this.dateKey(date);
        return {value, day: date.getDate(), label: new Intl.DateTimeFormat(document.documentElement.lang, {dateStyle: 'full'}).format(date), outside: date.getMonth() !== first.getMonth(), today: value === today,
          selected: value === this.s.draftAfter || value === this.s.draftBefore,
          range: Boolean(this.s.draftAfter && this.s.draftBefore && value > this.s.draftAfter && value < this.s.draftBefore)};
      });
    },
    moveMonth(delta) {
      const date = new Date(`${this.s.month}-01T12:00:00`); date.setMonth(date.getMonth() + delta);
      this.s.month = this.dateKey(date).slice(0, 7); this.s.cursor = this.s.month + '-01';
    },
    focusDate() { this.$refs.calendar?.querySelector(`[data-date="${this.s.cursor}"]`)?.focus({preventScroll: true}); },
    calendarKey(event) {
      const date = new Date(`${this.s.cursor}T12:00:00`);
      const shift = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
      if (shift) date.setDate(date.getDate() + shift);
      else if (event.key === 'Home') date.setDate(date.getDate() - (date.getDay() + 6) % 7);
      else if (event.key === 'End') date.setDate(date.getDate() + 6 - (date.getDay() + 6) % 7);
      else if (event.key === 'PageUp' || event.key === 'PageDown') { this.moveMonth(event.key === 'PageUp' ? -1 : 1); event.preventDefault(); this.$nextTick(() => this.focusDate()); return; }
      else return;
      event.preventDefault(); this.s.cursor = this.dateKey(date); this.s.month = this.s.cursor.slice(0, 7);
      this.$nextTick(() => this.focusDate());
    },
    chooseDate(value) {
      this.s.cursor = value;
      if (this.s.calendar === 'after') {
        this.s.draftAfter = value;
        if (this.s.draftBefore && this.s.draftBefore < value) this.s.draftBefore = value;
        this.s.calendar = 'before';
      } else {
        this.s.draftBefore = value;
        if (this.s.draftAfter && this.s.draftAfter > value) this.s.draftAfter = value;
        this.s.calendar = '';
        this.$nextTick(() => this.element.querySelectorAll('.search-date')[1].focus({preventScroll: true}));
      }
    },
    applyDates() {
      if (this.s.draftTime === 'custom' && (!this.s.draftAfter || !this.s.draftBefore || this.s.draftAfter > this.s.draftBefore)) return;
      this.s.time = this.s.draftTime; this.s.after = this.s.draftAfter; this.s.before = this.s.draftBefore;
      this.s.panel = ''; this.s.calendar = ''; this.focusFilter('time'); this.change();
    },
    cancelDates() { this.s.draftTime = this.s.time; this.s.draftAfter = this.s.after; this.s.draftBefore = this.s.before; this.s.panel = ''; this.s.calendar = ''; this.focusFilter('time'); },
    destroy() {
      this.active = false; this.cancel(); this.stopViewport();
      this.observer.disconnect();
      document.removeEventListener('htmx:after:swap', this.swapHandler);
    },
  }));
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
      this.permissions = Object.keys(this.labels).filter(point => point === data.default);
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
      const chat = document.getElementById('chat');
      if (chat) this.selected = chat.dataset.thread ? 'contact-' + chat.dataset.thread : '';
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
    element: null, state: null, key: '', atBottom: true, active: true,
    pending: new Map(), focused: null, timer: null,
    init() {
      this.element = this.$el; this.state = Alpine.store('history');
      this.key = new URL(this.element.dataset.url, location.href).pathname.replace(/\/messages$/, '');
      if (this.state.key !== this.key) this.state.advance(this.key, this.element.dataset.focus || null);
      if (this.element.dataset.focus && !this.state.reading) this.state.reading = {message: this.element.dataset.focus, offset: null};
      this.$nextTick(() => {
        if (!this.active) return;
        if (this.state.reading?.offset === null) this.locate(this.state.reading.message, false);
        else if (this.state.reading) this.restore();
        else this.element.scrollTop = this.element.scrollHeight;
        this.updateBottom();
      });
    },
    destroy() { this.active = false; this.pending.clear(); this.clearFocus(); },
    clearFocus() {
      clearTimeout(this.timer); this.timer = null;
      this.focused?.classList.remove('message-focus', 'message-fade'); this.focused = null;
    },
    locate(message, advance = true) {
      const row = document.getElementById(`message-${message}`);
      if (!this.active || !this.element.contains(row)) return;
      if (advance) this.state.advance(this.key, message);
      this.clearFocus();
      const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
      const top = Math.max(0, Math.min(this.element.scrollHeight - this.element.clientHeight, this.element.scrollTop + row.getBoundingClientRect().top - this.element.getBoundingClientRect().top - this.element.clientHeight * .32));
      this.element.scrollTop = reduced ? top : Math.max(0, Math.min(this.element.scrollHeight - this.element.clientHeight, top + (top > this.element.scrollTop ? -40 : 40)));
      this.state.reading = {message, offset: row.getBoundingClientRect().top - this.element.getBoundingClientRect().top};
      this.element.scrollTo({top, behavior: reduced ? 'instant' : 'smooth'});
      this.focused = row; row.classList.add('message-focus');
      this.timer = setTimeout(() => {
        if (reduced) this.clearFocus();
        else { row.classList.add('message-fade'); this.timer = setTimeout(() => this.clearFocus(), 350); }
      }, 1800);
      this.updateBottom();
    },
    remember() {
      if (!this.active || this.state.key !== this.key || !this.state.reading) return;
      const top = this.element.getBoundingClientRect().top;
      const row = [...this.element.querySelectorAll('.line[id], .note[id]')].find(row => row.getBoundingClientRect().bottom > top);
      if (row) this.state.reading = {message: row.id.slice(8), offset: row.getBoundingClientRect().top - top};
    },
    restore() {
      if (!this.active || this.state.key !== this.key || !this.state.reading) return;
      const {message, offset} = this.state.reading;
      const row = document.getElementById(`message-${message}`);
      if (this.element.contains(row) && offset !== null) this.element.scrollTop += row.getBoundingClientRect().top - this.element.getBoundingClientRect().top - offset;
      this.updateBottom();
    },
    scrolled() { this.remember(); this.updateBottom(); },
    resize() { if (this.state.reading) this.restore(); else this.updateBottom(); },
    jump() {
      this.state.reading = null;
      this.element.scrollTo({top: this.element.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    },
    updateBottom() { this.atBottom = this.element.scrollHeight - this.element.scrollTop - this.element.clientHeight < 2; },
    before(event) {
      const {ctx} = event.detail;
      if (!this.element.contains(ctx.sourceElement)) return;
      this.remember();
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
        if (this.state.reading) this.restore();
        else if (position.earlier) this.element.scrollTop = position.top + this.element.scrollHeight - position.height;
        else if (position.bottom) this.element.scrollTop = this.element.scrollHeight;
        this.updateBottom();
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
