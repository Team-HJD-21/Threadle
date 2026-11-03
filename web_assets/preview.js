(() => {
  const order = ['pr', 'issue', 'discussion', 'ci', 'alert'];
  document.querySelectorAll('.threadle-preview').forEach(preview => {
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
