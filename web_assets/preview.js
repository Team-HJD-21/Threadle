(() => {
  const order = ['pr', 'issue', 'discussion', 'ci', 'alert'];
  document.querySelectorAll('.threadle-preview:not([data-feature-preview])').forEach(preview => {
    const toggle = preview.querySelector('[data-preview-pause]');
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    let index = 0, paused = motion.matches, timer, ciTimer, ciElapsed = 0, ciLast = null;
    const ci = preview.querySelector('.preview-ci');
    function renderState() {
      preview.querySelectorAll('.preview-case').forEach(pane => {
        const active = pane.classList.contains('preview-' + order[index]);
        pane.classList.toggle('is-state-updated', motion.matches || (active && ciElapsed >= 8500));
        pane.classList.remove('is-state-answered');
      });
    }
    preview.classList.add('has-ci-clock');
    function renderCi() {
      ci.querySelectorAll('.preview-check-step').forEach((step, n) => {
        step.classList.toggle('is-complete', motion.matches || ciElapsed >= 1200 + n * 800);
      });
      ci.classList.toggle('is-complete', motion.matches || ciElapsed >= 5000);
    }
    function ciClock() {
      clearTimeout(ciTimer);
      if (paused || document.hidden || motion.matches) {
        ciLast = null; renderCi(); renderState(); return;
      }
      const now = performance.now();
      if (ciLast !== null) ciElapsed += now - ciLast;
      ciLast = now;
      renderCi(); renderState();
      if (ciElapsed < (order[index] === 'ci' ? 5000 : 8500)) ciTimer = setTimeout(ciClock, 100);
    }
    function schedule() {
      clearTimeout(timer);
      if (!paused && !document.hidden && !preview.querySelector('.preview-window').contains(document.activeElement)) {
        timer = setTimeout(advance, 12500);
      }
    }
    function advance() {
      if (order[index] === 'ci' && !motion.matches) {
        const complete = ci.classList.contains('is-complete');
        if (!complete) { timer = setTimeout(advance, 250); return; }
        timer = setTimeout(() => select((index + 1) % order.length), 1500);
        return;
      }
      select((index + 1) % order.length);
    }
    function select(next) {
      const focused = preview.querySelector('.preview-window').contains(document.activeElement);
      index = next;
      ciElapsed = 0; ciLast = null; ciClock();
      preview.querySelector('#preview-' + order[index]).checked = true;
      preview.querySelectorAll('.preview-case').forEach(pane => {
        pane.setAttribute('aria-hidden', String(!pane.classList.contains('preview-' + order[index])));
      });
      if (focused) preview.querySelector('.preview-' + order[index] + ' [data-preview-key="' + order[index] + '"]').focus({preventScroll:true});
      schedule();
    }
    function playback() {
      toggle.setAttribute('aria-pressed', String(paused));
      toggle.textContent = paused ? toggle.dataset.play : toggle.dataset.pause;
      preview.classList.toggle('is-paused', paused);
      ciClock();
      schedule();
    }
    preview.querySelectorAll('[data-preview-key]').forEach(button => {
      button.addEventListener('click', () => select(order.indexOf(button.dataset.previewKey)));
    });
    toggle.addEventListener('click', () => { paused = !paused; playback(); });
    preview.addEventListener('focusin', () => clearTimeout(timer));
    preview.addEventListener('focusout', () => setTimeout(schedule, 0));
    document.addEventListener('visibilitychange', () => { ciClock(); schedule(); });
    motion.addEventListener('change', () => { paused = motion.matches; playback(); });
    select(0);
    playback();
  });
})();

(() => {
  document.querySelectorAll('[data-feature-showcase]').forEach(root => {
    const tabs = [...root.querySelectorAll('[role="tab"]')];
    const panels = [...root.querySelectorAll('[role="tabpanel"]')];
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const pause = root.querySelector('[data-feature-pause]');
    let selected = 0, paused = motion.matches, elapsed = 0, last = null, timer;
    function clock() {
      clearTimeout(timer);
      const now = performance.now();
      if (last !== null && !paused && !document.hidden) elapsed += now-last;
      last = !paused && !document.hidden ? now : null;
      panels[selected].classList.toggle('is-state-updated', selected === 0 && (motion.matches || elapsed >= 8500));
      if (!paused && !document.hidden && elapsed < 8500) timer = setTimeout(clock,100);
    }
    function playback() {
      pause.setAttribute('aria-pressed',String(paused));
      pause.textContent = paused ? pause.dataset.play : pause.dataset.pause;
      root.classList.toggle('is-paused',paused);
      clock();
    }
    function select(index, focus = false) {
      selected = index; elapsed = 0; last = null;
      tabs.forEach((tab,i) => {tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});
      if (focus) tabs[index].focus({preventScroll:true});
      clock();
    }
    tabs.forEach((tab,i) => {
      tab.addEventListener('click',() => select(i));
      tab.addEventListener('keydown',event => {
        let next;
        if (event.key==='ArrowRight' || event.key==='ArrowDown') next=(i+1)%tabs.length;
        if (event.key==='ArrowLeft' || event.key==='ArrowUp') next=(i+tabs.length-1)%tabs.length;
        if (event.key==='Home') next=0;
        if (event.key==='End') next=tabs.length-1;
        if (next!==undefined) {event.preventDefault();select(next,true);}
      });
    });
    pause.addEventListener('click',() => {paused=!paused;last=null;playback();});
    document.addEventListener('visibilitychange',() => {last=null;clock();});
    motion.addEventListener('change',() => {paused=motion.matches;last=null;playback();});
    select(0);playback();
  });
})();
