document.addEventListener('alpine:init', () => {
  const records = new Set();
  const clocks = new Set();
  let timer = null;
  let request = null;
  let next = 0;
  let stopped = false;
  const key = value => JSON.stringify(value);
  const changed = (seen, state) => seen === null || Object.entries(state).some(
    ([name, value]) => name === 'since' ? value > seen[name] : value !== seen[name]);
  const emit = name => window.dispatchEvent(new CustomEvent(name));
  const arm = () => {
    if (timer === null && !stopped && (records.size || clocks.size)) timer = setTimeout(tick, 0);
  };
  const run = async (record, change) => {
    if (!records.has(record)) return;
    if (record.running) { record.pending = change; return; }
    const generation = record.generation;
    record.running = true;
    record.controller = new AbortController();
    let success = false;
    try {
      const result = await record.callback(change, record.controller.signal);
      if (records.has(record) && record.generation === generation && result?.seen) {
        record.seen = result.seen;
        success = true;
      }
    } catch (error) {
      if (error.name !== 'AbortError') console.warn('Subscription refresh failed', error);
    } finally {
      record.running = false;
      record.controller = null;
      if (change.unavailable) records.delete(record);
      const pending = record.pending;
      record.pending = null;
      if (success && pending && records.has(record) && (pending.unavailable || changed(record.seen, pending.state))) {
        void run(record, pending);
      }
    }
  };
  const check = async () => {
    const groups = new Map();
    for (const record of records) {
      const identity = key([record.scope, record.seen]);
      if (!groups.has(identity)) groups.set(identity, {id: String(groups.size), scope: record.scope, seen: record.seen, targets: []});
      groups.get(identity).targets.push({record, generation: record.generation, seen: key(record.seen)});
    }
    const items = [...groups.values()];
    const controller = new AbortController();
    request = {controller, started: Date.now()};
    try {
      const response = await fetch('/poll', {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true',
          'X-Build': document.body.dataset.build,
          'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone},
        body: JSON.stringify({subscriptions: items.map(({id, scope, seen}) => ({id, scope, seen}))}),
      });
      if (response.headers.get('HX-Trigger')?.includes('stale')) { stopped = true; emit('stale'); return; }
      if (response.status === 401) { stopped = true; location.assign(response.headers.get('HX-Redirect') || '/login'); return; }
      if (response.status === 400 || response.status === 403) {
        stopped = true;
        throw new Error(`Poll refused: ${response.status}`);
      }
      if (!response.ok) throw new Error(`Poll failed: ${response.status}`);
      const {changes} = await response.json();
      emit('poll-online');
      const jobs = [];
      for (const change of changes) {
        for (const {record, generation, seen} of items[Number(change.id)].targets) {
          if (records.has(record) && record.generation === generation && key(record.seen) === seen) jobs.push(run(record, change));
        }
      }
      void Promise.allSettled(jobs);
    } catch (error) {
      if (records.size && (error.name !== 'AbortError' || Date.now() - request.started >= 15000)) emit('poll-offline');
    } finally {
      request = null;
      next = Date.now() + 5000;
    }
  };
  function tick() {
    timer = null;
    if (stopped) return;
    const now = Date.now();
    for (const callback of clocks) {
      try { callback(now); } catch (error) { console.error(error); }
    }
    if (request && now - request.started >= 15000) request.controller.abort();
    if (!request && records.size && now >= next) void check();
    if (records.size || clocks.size) timer = setTimeout(tick, 1000);
  }
  const manager = {
    subscribe({scope, seen}, callback) {
      const record = {scope, seen, callback, generation: 0, running: false, pending: null, controller: null};
      records.add(record);
      if (records.size === 1) next = 0;
      arm();
      return {
        update({scope, seen}) {
          if (key(record.scope) !== key(scope)) {
            record.generation++;
            record.controller?.abort();
            record.pending = null;
          }
          record.scope = scope;
          record.seen = seen;
        },
        unsubscribe() {
          records.delete(record);
          record.pending = null;
          record.controller?.abort();
          if (!records.size) request?.controller.abort();
          if (!records.size && !clocks.size) { clearTimeout(timer); timer = null; }
        },
      };
    },
    clock(callback) {
      clocks.add(callback);
      callback(Date.now());
      arm();
      return () => {
        clocks.delete(callback);
        if (!records.size && !clocks.size) { clearTimeout(timer); timer = null; }
      };
    },
  };
  Alpine.store('poll', manager);
  window.addEventListener('stale', () => {
    stopped = true; clearTimeout(timer); timer = null; request?.controller.abort();
    for (const record of records) record.controller?.abort();
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { next = 0; clearTimeout(timer); timer = null; arm(); }
  });

  Alpine.directive('poll', (element, {}, {cleanup}) => {
    let subscription = null;
    let descriptor = null;
    let source = '';
    let active = true;
    const host = element.dataset.pollHover !== undefined ? element.closest('.agent, .turn') : null;
    const enabled = () => (!host || host.matches(':hover')) && (!element.closest('[data-reminders]') || element.closest('.drawer')?.getAttribute('data-open') === 'true');
    const refresh = async (change, signal) => {
      if (!element.isConnected || !active) return;
      let ctx = null;
      const before = event => { if (!ctx && event.detail.ctx.sourceElement === element) ctx = event.detail.ctx; };
      const abort = () => { if (ctx && !['swapped', 'response received'].includes(ctx.status)) ctx.request.abort(); };
      document.addEventListener('htmx:before:request', before);
      signal.addEventListener('abort', abort);
      try {
        const options = {source: element};
        if (element.dataset.pollUrl) Object.assign(options, {target: element, swap: 'outerMorph', push: false, replace: false});
        await htmx.ajax('GET', element.dataset.pollUrl || element.getAttribute('hx-get'), options);
        if (signal.aborted && ctx?.status !== 'swapped') return;
        const response = ctx?.response?.raw;
        if (!response?.ok || !(ctx.status === 'swapped' || response.status === 204)) return;
        const header = response.headers.get('X-Poll-State');
        if (!header) return;
        const seen = JSON.parse(header);
        if (active && element.isConnected) {
          descriptor = {...descriptor, seen};
          element.dataset.poll = JSON.stringify(descriptor);
          source = element.dataset.poll;
        }
        return {seen};
      } finally {
        document.removeEventListener('htmx:before:request', before);
        signal.removeEventListener('abort', abort);
      }
    };
    const sync = () => {
      if (!active || !element.isConnected) return;
      if (!enabled()) { subscription?.unsubscribe(); subscription = null; return; }
      const text = element.dataset.poll;
      if (text !== source) { descriptor = JSON.parse(text); source = text; subscription?.update(descriptor); }
      subscription ||= manager.subscribe(descriptor, refresh);
    };
    const after = event => {
      const target = event.detail.ctx.target instanceof Node ? event.detail.ctx.target : event.detail.ctx.sourceElement;
      if (target && (element.contains(target) || target.contains(element))) {
        sync();
        if (descriptor && ['agents', 'computers'].includes(descriptor.scope.topic)) {
          const until = element.querySelector('.edge')?.dataset.after || null;
          if (descriptor.scope.until !== until) {
            descriptor = {...descriptor, scope: {...descriptor.scope, until}};
            element.dataset.poll = source = JSON.stringify(descriptor);
            subscription?.update(descriptor);
          }
        }
      }
    };
    document.addEventListener('htmx:after:swap', after);
    window.addEventListener('drawer-state', sync);
    host?.addEventListener('mouseenter', sync);
    host?.addEventListener('mouseleave', sync);
    queueMicrotask(sync);
    cleanup(() => {
      active = false; subscription?.unsubscribe();
      document.removeEventListener('htmx:after:swap', after);
      window.removeEventListener('drawer-state', sync);
      host?.removeEventListener('mouseenter', sync);
      host?.removeEventListener('mouseleave', sync);
    });
  });
}, {once: true});
