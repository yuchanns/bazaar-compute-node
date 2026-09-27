document.addEventListener('alpine:init', () => {
  Alpine.data('agentForm', () => ({
    labels: {},
    form: null,
    active: true,
    destroy() { this.active = false; },
    selected: {channel: 0, runtime: 0},
    kinds: {channel: 'idle', runtime: 'idle'},
    init() {
      this.form = this.$el;
      const {fields, labels} = JSON.parse(this.form.querySelector('[data-editor]').textContent);
      Object.assign(this, fields);
      this.labels = labels;
      for (const family of ['channel', 'runtime']) {
        this.cards[family] = this.cards[family].map(card => ({
          app_id: '', region: 'feishu', bot_id: '', websocket_url: '',
          model: '', effort: '', sandbox_mode: 'workspace-write', network_access: true,
          models: [{value: card.model || '', label: card.model || labels.modelDefault, efforts: []}], efforts: card.effort ? [card.effort] : [], listed: false,
          ...card, secret: '', change: !card.secret_set,
          nextEnv: card.env.length,
          env: card.env.map(row => ({...row, value: '', change: false})),
        }));
      }
      this.$nextTick(() => { if (this.active) { this.reveal(); this.legacy(); } });
    },
    addCard(family, kind, version = '') {
      this.cards[family].push({
        id: this.next++, kind, version, was: null,
        app_id: '', region: 'feishu', bot_id: '', websocket_url: '',
        model: '', effort: '', sandbox_mode: 'workspace-write', network_access: true,
        models: [{value: '', label: this.labels.modelDefault, efforts: []}], efforts: [], listed: false,
        secret_set: false, secret: '', change: true, nextEnv: 0, env: [],
      });
      this.selected[family] = this.cards[family].length - 1;
    },
    removeCard(family, card) {
      const index = this.cards[family].indexOf(card);
      this.cards[family].splice(index, 1);
      this.selected[family] = Math.max(index - 1, 0);
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
    legacy() {

  const form = this.form;
  const state = () => this;
  const steps = [...form.querySelectorAll('[data-step]')];
  const pins = [...form.querySelectorAll('[data-go]')];
  const back = form.querySelector('[data-back]'), forward = form.querySelector('[data-forward]'), done = form.querySelector('[data-done]');
  // an agent needs a channel and a runtime: a step whose stack has no card
  // is as unfinished as one with an empty field
  const bad = (step) => [...step.querySelectorAll('.deck')].some((deck) => !deck.querySelector(':scope > [data-card]'))
    || [...step.querySelectorAll('input, select')].some((field) => !field.checkValidity());

  const reveal = () => state().$nextTick(() => state().reveal());
  // a runtime's models are asked for when its model or effort is first
  // opened, once for every card of that kind; not answering, the field
  // offers to ask again
  const lists = {};
  const list = async (card, field) => {
    const model = card.querySelector('select[data-model]');
    const kind = card.querySelector('input[name$="-kind"]').value;
    const wait = field.closest('.field').querySelector('[data-wait]');
    for (const again of card.querySelectorAll('[data-again]')) again.hidden = true;
    wait.hidden = false;
    lists[kind] ??= fetch(`${form.dataset.computer}/models/${encodeURIComponent(kind)}`).then(async (response) => {
      if (!response.ok) throw new Error(await response.text());
      return response.text();
    });
    let options;
    try {
      options = await lists[kind];
    } catch (error) {
      delete lists[kind];
      const again = field.closest('.field').querySelector('[data-again]');
      again.title = error.message;
      again.hidden = false;
      return;
    } finally {
      wait.hidden = true;
    }
    if (!card.isConnected) return;
    const data = Alpine.$data(card).card;
    data.models = [...new DOMParser().parseFromString(options, 'text/html').querySelectorAll('option')]
      .map(option => ({value: option.value, label: option.textContent, efforts: (option.dataset.efforts || '').split(' ').filter(Boolean)}));
    if (!data.models.some(item => item.value === data.model)) data.models.push({value: data.model, label: data.model, efforts: []});
    data.listed = true;
    efforts(model);
  };
  const efforts = model => {
    const data = Alpine.$data(model).card;
    data.efforts = data.models.find(item => item.value === data.model)?.efforts || [];
    if (!data.efforts.includes(data.effort)) data.effort = '';
  };

  const draw = (deck, shown) => { state().selected[deck.dataset.family] = shown; reveal(); };
  // the summary reads each card's fields back as they will be kept
  const said = (field) => field.tagName === 'SELECT' ? field.selectedOptions[0]?.textContent
    : field.type === 'password' ? form.dataset[field.value ? 'filled' : 'empty']
    : field.type === 'checkbox' ? form.dataset[field.checked ? 'on' : 'off']
    : field.value || form.dataset.none;
  const summary = () => {
    const out = form.querySelector('[data-summary]');
    out.replaceChildren();
    for (const card of form.querySelectorAll('[data-step]:not([data-summary-step]) .part:not([data-blank])')) {
      const box = Object.assign(document.createElement('div'), { className: 'dialog part' });
      const title = card.querySelector('.title');
      const head = Object.assign(document.createElement('div'), { className: 'title' });
      if (title) {
        head.textContent = [...title.childNodes].filter(node => node.nodeName !== 'BUTTON').map(node => node.textContent).join('');
      } else {
        head.textContent = form.dataset.basic;
      }
      box.append(head);
      const rows = Object.assign(document.createElement('div'), { className: 'rows' });
      for (const row of card.querySelectorAll('.field')) {
        if (row.hidden) continue;
        const names = [...row.querySelectorAll('.kv')].filter((kv) => kv.querySelector('[name$="-value"]').value || kv.querySelector('[name$="-was"]')).map((kv) => kv.querySelector('[name$="-name"]').value).filter(Boolean);
        const fields = [...row.querySelectorAll('input:not([type=hidden]), select, textarea')].filter((field) => !field.closest('.kv'));
        const value = row.querySelector('.kv, [data-env-add]') ? names.join('\n') || form.dataset.none
          : [...fields.map(said), row.querySelector('.unit')?.textContent].filter(Boolean).join(' ');
        rows.append(Object.assign(document.createElement('span'), { className: 'k', textContent: row.querySelector('label')?.textContent ?? '' }),
          Object.assign(document.createElement('span'), { textContent: value }));
      }
      box.append(rows);
      out.append(box);
    }
  };

  form.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    const deck = button.closest('.deck');
    if (button.hasAttribute('data-again')) {
      list(button.closest('[data-card]'), button.closest('.field').querySelector('select'));
    } else if (button.type === 'submit') {
      // a missing field on a card out of sight is shown before it is asked for
      const missing = [...form.querySelectorAll('input, select')].find((field) => !field.checkValidity());
      const hidden = missing?.closest('.deck');
      if (hidden && !missing.closest('.shown')) {
        event.preventDefault();
        draw(hidden, [...hidden.querySelectorAll(':scope > [data-card]')].indexOf(missing.closest('[data-card]')));
        missing.reportValidity();
      }
    }
  });

  // a form in steps: one shown; those behind say whether they are filled in
  if (steps.length) {
    let at = 0;
    const go = (step) => {
      at = step;
      form.querySelector('.steps').scrollTop = 0;
      steps.forEach((section, n) => section.hidden = n !== step);
      pins.forEach((pin, n) => {
        const item = pin.parentElement;
        item.classList.toggle('on', n === step);
        item.classList.toggle('done', n < step && !bad(steps[n]));
        item.classList.toggle('bad', n < step && bad(steps[n]));
      });
      back.hidden = step === 0;
      forward.hidden = step === steps.length - 1;
      done.hidden = step !== steps.length - 1;
      if (step === steps.length - 1) summary();
      reveal();
    };
    pins.forEach((pin, n) => pin.addEventListener('click', () => go(n)));
    back.addEventListener('click', () => go(at - 1));
    forward.addEventListener('click', () => go(at + 1));
    // sending with something missing goes to the step and the card it is on
    // first - a field out of sight cannot say what is wrong with it - and
    // lets the field say it
    done.addEventListener('click', () => {
      const missing = [...form.querySelectorAll('input, select')].find((field) => !field.checkValidity());
      if (!missing) return form.requestSubmit();
      go(steps.findIndex((section) => section.contains(missing)));
      const deck = missing.closest('.deck');
      if (deck) draw(deck, [...deck.querySelectorAll(':scope > [data-card]')].indexOf(missing.closest('[data-card]')));
      missing.reportValidity();
    });
    go(Number(form.dataset.at ?? 0));
  }

  form.addEventListener('change', (event) => {
    const model = event.target.closest('select[data-model]');
    if (model) efforts(model);
  });
  // a model or an effort not yet listed is listed before it opens
  const opening = (event) => {
    const field = event.target.closest('select[data-model], select[data-effort]');
    const card = field?.closest('[data-card]');
    if (!card || Alpine.$data(card).card.listed || event.key === 'Tab') return;
    event.preventDefault();
    field.focus();
    list(card, field);
  };
  form.addEventListener('pointerdown', opening);
  form.addEventListener('keydown', opening);


    },
    async loadKinds(family, retry = false) {
      if (this.kinds[family] === 'loading' || (!retry && this.kinds[family] !== 'idle')) return;
      this.kinds[family] = 'loading';
      try {
        await htmx.ajax('GET', `${this.form.dataset.computer}/kinds/${family}`, {
          source: this.form.querySelector(`[data-blank="${family}"] .kinds`),
          target: this.form.querySelector(`[data-blank="${family}"] .kinds`), swap: 'innerHTML',
        });
        this.kinds[family] = 'ready';
      } catch {
        this.kinds[family] = 'error';
      }
    },
  }));
}, {once: true});
