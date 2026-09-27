document.addEventListener('alpine:init', () => {
  Alpine.data('agentForm', () => ({
    form: null,
    active: true,
    labels: {},
    cards: {channel: [], runtime: []},
    selected: {channel: 0, runtime: 0},
    kinds: {channel: 'idle', runtime: 'idle'},
    requests: new Map(),
    kindRequests: new Map(),
    step: 0,
    wizard: false,
    init() {
      this.form = this.$el;
      const {fields, labels} = JSON.parse(this.form.querySelector('[data-editor]').textContent);
      Object.assign(this, fields);
      this.labels = labels;
      this.wizard = this.form.classList.contains('wizard');
      this.step = Number(this.form.dataset.at || 0);
      for (const family of ['channel', 'runtime']) {
        this.cards[family] = this.cards[family].map(card => ({
          app_id: '', region: 'feishu', bot_id: '', websocket_url: '',
          model: '', effort: '', sandbox_mode: 'workspace-write', network_access: true,
          models: [{value: card.model || '', label: card.model || labels.modelDefault, efforts: []}],
          efforts: card.effort ? [card.effort] : [], modelState: 'idle', modelError: '',
          ...card, secret: '', change: !card.secret_set,
          nextEnv: card.env.length,
          env: card.env.map(row => ({...row, value: '', change: false})),
        }));
      }
      this.$nextTick(() => this.reveal());
    },
    destroy() {
      this.active = false;
      for (const request of this.requests.values()) request.controller.abort();
      for (const abort of this.kindRequests.values()) abort();
      this.requests.clear();
      this.kindRequests.clear();
    },
    addCard(family, kind, version = '') {
      this.cards[family].push({
        id: this.next++, kind, version, was: null,
        app_id: '', region: 'feishu', bot_id: '', websocket_url: '',
        model: '', effort: '', sandbox_mode: 'workspace-write', network_access: true,
        models: [{value: '', label: this.labels.modelDefault, efforts: []}],
        efforts: [], modelState: 'idle', modelError: '',
        secret_set: false, secret: '', change: true, nextEnv: 0, env: [],
      });
      this.selected[family] = this.cards[family].length - 1;
    },
    removeCard(family, card) {
      const index = this.cards[family].indexOf(card);
      this.cards[family].splice(index, 1);
      this.selected[family] = Math.max(index - 1, 0);
      if (family === 'runtime' && card.modelState === 'loading' &&
          !this.cards.runtime.some(item => item.kind === card.kind && item.modelState === 'loading')) {
        this.requests.get(card.kind)?.controller.abort();
        this.requests.delete(card.kind);
      }
      this.$nextTick(() => this.reveal());
    },
    turn(family, amount) {
      this.selected[family] += amount;
      this.$nextTick(() => this.reveal());
    },
    addEnv(card) {
      card.env.push({id: card.nextEnv++, name: '', was: null, value: '', change: true});
    },
    reveal() {
      if (!this.active) return;
      for (const family of ['channel', 'runtime']) {
        if (this.form.querySelector(`[data-blank="${family}"]`).checkVisibility()) this.loadKinds(family);
      }
    },
    kindStarted(event) {
      const {ctx} = event.detail;
      const blank = ctx.sourceElement.closest('[data-blank]');
      if (blank) this.kindRequests.set(blank.dataset.blank, ctx.request.abort);
    },
    kindFinished(event) {
      if (!this.active) return;
      const {ctx} = event.detail;
      const blank = ctx.sourceElement.closest('[data-blank]');
      if (!blank) return;
      this.kindRequests.delete(blank.dataset.blank);
      this.kinds[blank.dataset.blank] = ctx.status === 'swapped' && !blank.querySelector('.err') ? 'ready' : 'error';
    },
    async loadKinds(family, retry = false) {
      if (!this.active || this.kinds[family] === 'loading' || (!retry && this.kinds[family] !== 'idle')) return;
      this.kinds[family] = 'loading';
      const target = this.form.querySelector(`[data-blank="${family}"] .kinds`);
      try {
        await htmx.ajax('GET', `${this.form.dataset.computer}/kinds/${family}`, {
          source: target, target, swap: 'innerHTML',
        });
      } finally {
        if (this.active && this.kinds[family] === 'loading') this.kinds[family] = 'error';
      }
    },
    async loadModels(card, event) {
      if (event?.type === 'keydown' && !['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
      if (card.modelState === 'ready') return;
      event?.preventDefault();
      if (card.modelState === 'loading') return;
      const select = event?.currentTarget;
      card.modelState = 'loading';
      card.modelError = '';
      if (!this.requests.has(card.kind)) {
        const controller = new AbortController();
        const promise = fetch(`${this.form.dataset.computer}/models/${encodeURIComponent(card.kind)}`, {signal: controller.signal})
          .then(async response => {
            const text = await response.text();
            if (!response.ok) throw new Error(text);
            return [...new DOMParser().parseFromString(text, 'text/html').querySelectorAll('option')]
              .map(option => ({value: option.value, label: option.textContent, efforts: (option.dataset.efforts || '').split(' ').filter(Boolean)}));
          });
        this.requests.set(card.kind, {controller, promise});
      }
      const request = this.requests.get(card.kind);
      try {
        const models = await request.promise;
        if (!this.active || !this.cards.runtime.includes(card)) return;
        card.models = [...models];
        if (!card.models.some(item => item.value === card.model)) {
          card.models.push({value: card.model, label: card.model, efforts: card.effort ? [card.effort] : []});
        }
        card.modelState = 'ready';
        this.updateEffort(card);
        await this.$nextTick();
        if (this.active && select?.isConnected && select.checkVisibility()) {
          select.focus();
          if (navigator.userActivation.isActive) select.showPicker?.();
        }
      } catch (error) {
        if (this.requests.get(card.kind) === request) this.requests.delete(card.kind);
        if (!this.active || !this.cards.runtime.includes(card)) return;
        card.modelState = 'error';
        card.modelError = error.message;
      }
    },
    updateEffort(card) {
      if (card.modelState !== 'ready') return;
      card.efforts = card.models.find(item => item.value === card.model)?.efforts || [];
      if (!card.efforts.includes(card.effort)) card.effort = '';
    },
    validStep(step) {
      if (step === 0) return Boolean(this.name.trim()) && Number.isInteger(Number(this.idle_timeout)) && Number(this.idle_timeout) >= 0;
      if (step === 1) return this.cards.channel.length > 0 && this.cards.channel.every(card =>
        (card.kind !== 'lark' || card.app_id) && (card.kind !== 'wecom' || card.bot_id) &&
        (!this.labels.secrets[card.kind] || card.secret_set || card.secret));
      return this.cards.runtime.length > 0;
    },
    go(step) {
      this.step = step;
      this.$nextTick(() => {
        if (!this.active) return;
        this.form.querySelector('.steps').scrollTop = 0;
        this.reveal();
      });
    },
    submit(event) {
      const missing = [...this.form.querySelectorAll('input, select, textarea')].find(field => !field.checkValidity());
      const family = this.wizard && ['channel', 'runtime'].find(family => !this.cards[family].length);
      if (!missing && !family) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (missing) {
        const deck = missing.closest('[data-family]');
        if (deck) this.selected[deck.dataset.family] = this.cards[deck.dataset.family].findIndex(card => card.id === Number(missing.closest('[data-card]').dataset.cardId));
        if (this.wizard) this.step = ['basic', 'channels', 'runtimes'].indexOf(missing.closest('[data-step]').dataset.step);
      } else {
        this.step = family === 'channel' ? 1 : 2;
      }
      this.$nextTick(() => {
        if (!this.active) return;
        this.reveal();
        if (missing) { this.$focus.focus(missing); missing.reportValidity(); }
      });
    },
    get summary() {
      const labels = this.labels;
      if (!labels.fields) return [];
      const sections = [
        {id: 'basic', title: labels.basic, rows: [
          {label: labels.fields.name, value: this.name || labels.none},
          {label: labels.fields.mode, value: labels.values.mode[this.mode]},
          {label: labels.fields.idle_timeout, value: `${this.idle_timeout} ${labels.seconds}`},
        ]},
        {id: 'reply', title: labels.reply, rows: [{label: '', value: this.reply || labels.none}]},
      ];
      for (const family of ['channel', 'runtime']) {
        for (const card of this.cards[family]) {
          const rows = [];
          if (family === 'channel') {
            for (const field of card.kind === 'lark' ? ['app_id', 'region'] : card.kind === 'wecom' ? ['bot_id', 'websocket_url'] : []) {
              rows.push({label: labels.fields[field], value: labels.values[field]?.[card[field]] || card[field] || labels.none});
            }
            if (labels.secrets[card.kind]) rows.push({label: labels.fields[labels.secrets[card.kind]], value: card.secret || card.secret_set ? labels.filled : labels.empty});
          } else {
            rows.push({label: labels.fields.model, value: card.models.find(item => item.value === card.model)?.label || card.model || labels.modelDefault});
            if (card.modelState !== 'ready' || card.efforts.length) rows.push({label: labels.fields.effort, value: card.effort || labels.effortDefault});
            rows.push(
              {label: labels.fields.sandbox_mode, value: labels.values.sandbox_mode[card.sandbox_mode]},
              {label: labels.fields.network_access, value: card.network_access ? labels.on : labels.off},
              {label: labels.fields.env, value: card.env.filter(row => row.name && (row.was || row.value)).map(row => row.name).join('\n') || labels.none},
            );
          }
          sections.push({id: card.id, family, kind: card.kind, title: labels[family][card.kind] || card.kind, version: card.version, rows});
        }
      }
      return sections;
    },
  }));
}, {once: true});
