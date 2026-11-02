(() => {
  const order = ['pr', 'issue', 'discussion', 'ci', 'alert'];
  document.querySelectorAll('.threadle-preview').forEach(preview => {
    const toggle = preview.querySelector('[data-preview-pause]');
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    let index = 0, paused = motion.matches, timer;
    function schedule() {
      clearTimeout(timer);
      if (!paused && !document.hidden && !preview.querySelector('.preview-window').contains(document.activeElement)) {
        timer = setTimeout(() => select((index + 1) % order.length), order[index] === 'ci' ? 6000 : 6000);
      }
    }
    function select(next) {
      const focused = preview.querySelector('.preview-window').contains(document.activeElement);
      index = next;
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
      schedule();
    }
    preview.querySelectorAll('[data-preview-key]').forEach(button => {
      button.addEventListener('click', () => select(order.indexOf(button.dataset.previewKey)));
    });
    toggle.addEventListener('click', () => { paused = !paused; playback(); });
    preview.addEventListener('focusin', () => clearTimeout(timer));
    preview.addEventListener('focusout', () => setTimeout(schedule, 0));
    document.addEventListener('visibilitychange', schedule);
    motion.addEventListener('change', () => { paused = motion.matches; playback(); });
    select(0);
    playback();
  });
})();
