const threadlePreviewSteps = {arrival:1200,thread:3500,open:5600,description:6500,reply:9300,review:12000,summary:5600,command:10000,result:13500};
function renderThreadleSequence(panel,elapsed,reduced,closed=false) {
  const position=reduced?17000:elapsed;
  const layout=panel.matches('.preview-case')?panel:panel.querySelector('.preview-case');
  const detail=panel.querySelector('[data-feature-detail]');
  const opened=position>=threadlePreviewSteps.open&&!closed;
  layout.classList.toggle('is-thread-open',opened&&!!detail);
  if(detail) detail.hidden=!opened;
  panel.querySelectorAll('[data-feature-step]').forEach(item=>{
    const wasHidden=item.hidden;
    item.hidden=position<threadlePreviewSteps[item.dataset.featureStep];
    if(wasHidden&&!item.hidden&&!item.closest('[hidden]')){
      const feed=item.closest('.preview-feed,.preview-detail-scroll');
      if(feed) feed.scrollTop=feed.scrollHeight;
    }
  });
  const messages=detail?[...detail.querySelectorAll('.preview-post[data-feature-step]')].filter(item=>!item.hidden).length:0;
  const count=position>=threadlePreviewSteps.thread?Math.max(1,messages):0;
  panel.querySelectorAll('[data-thread-count]').forEach(label=>{label.textContent=label.dataset.countLabel.replace('{count}',String(count));});
}

(() => {
  const order = ['pr', 'issue', 'discussion', 'ci', 'alert'];
  document.querySelectorAll('.threadle-preview:not([data-feature-preview])').forEach(preview => {
    const toggle = preview.querySelector('[data-preview-pause]');
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    let visible = !('IntersectionObserver' in window);
    let index = 0, paused = motion.matches, timer, ciTimer, ciElapsed = 0, ciLast = null;
    const ci = preview.querySelector('.preview-ci');
    function renderState() {
      preview.querySelectorAll('.preview-case').forEach(pane => {
        const active = pane.classList.contains('preview-' + order[index]);
        pane.classList.toggle('is-state-updated', motion.matches || (active && ciElapsed >= 16000));
        pane.classList.remove('is-state-answered');
        if(pane.hasAttribute('data-topic-preview')) renderThreadleSequence(pane,active?ciElapsed:0,motion.matches);
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
      if (paused || document.hidden || !visible || motion.matches) {
        ciLast = null; renderCi(); renderState(); return;
      }
      const now = performance.now();
      if (ciLast !== null) ciElapsed += now - ciLast;
      ciLast = now;
      renderCi(); renderState();
      if (ciElapsed < (order[index] === 'ci' ? 5000 : 17000)) ciTimer = setTimeout(ciClock, 100);
    }
    function schedule() {
      clearTimeout(timer);
      if (!paused && !document.hidden && visible && !preview.querySelector('.preview-window').contains(document.activeElement)) {
        timer = setTimeout(advance, ['pr','issue','discussion'].includes(order[index])?21000:order[index]==='ci'?6500:12500);
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
    preview.querySelectorAll('[data-feature-open]').forEach(button=>button.addEventListener('click',()=>{
      ciElapsed=Math.max(ciElapsed,threadlePreviewSteps.open);ciLast=null;ciClock();
    }));
    toggle.addEventListener('click', () => { paused = !paused; playback(); });
    preview.addEventListener('focusin', () => clearTimeout(timer));
    preview.addEventListener('focusout', () => setTimeout(schedule, 0));
    document.addEventListener('visibilitychange', () => { ciClock(); schedule(); });
    motion.addEventListener('change', () => { paused = motion.matches; playback(); });
    if('IntersectionObserver' in window) new IntersectionObserver(entries=>{
      visible=entries[0].intersectionRatio>=.5;ciLast=null;ciClock();schedule();
    },{threshold:[0,.5]}).observe(preview.querySelector('.preview-window'));
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
    const replay = root.querySelector('[data-feature-replay]');
    let visible = !('IntersectionObserver' in window);
    let selected = 0, paused = motion.matches, elapsed = 0, last = null, timer, manuallyClosed = false;
    function clock() {
      clearTimeout(timer);
      const now = performance.now();
      if (last !== null && !paused && !document.hidden && visible) elapsed += now-last;
      last = !paused && !document.hidden && visible ? now : null;
      const panel = panels[selected];
      panel.classList.toggle('is-state-updated', selected === 0 && (motion.matches||elapsed >= 16000));
      renderThreadleSequence(panel,elapsed,motion.matches,manuallyClosed);
      if (!paused && !document.hidden && visible && elapsed < 17000) timer = setTimeout(clock,100);
    }
    function playback() {
      pause.setAttribute('aria-pressed',String(paused));
      pause.textContent = paused ? pause.dataset.play : pause.dataset.pause;
      root.classList.toggle('is-paused',paused);
      clock();
    }
    function select(index, focus = false) {
      selected = index; elapsed = 0; last = null; manuallyClosed = false;
      tabs.forEach((tab,i) => {tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});
      panels[index].querySelectorAll('.preview-feed, .preview-detail-scroll').forEach(feed => {feed.scrollTop=0;});
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
    replay.addEventListener('click',() => {paused=motion.matches;select(selected);playback();});
    root.querySelectorAll('[data-feature-open]').forEach(button => button.addEventListener('click',() => {
      elapsed=Math.max(elapsed,threadlePreviewSteps.open);manuallyClosed=false;last=null;clock();
    }));
    root.querySelectorAll('[data-feature-back]').forEach(button => button.addEventListener('click',() => {
      manuallyClosed=true;clock();
    }));
    document.addEventListener('visibilitychange',() => {last=null;clock();});
    motion.addEventListener('change',() => {paused=motion.matches;last=null;playback();});
    if ('IntersectionObserver' in window) new IntersectionObserver(entries => {
      visible=entries[0].intersectionRatio>=.5;last=null;clock();
    },{threshold:[0,.5]}).observe(root.querySelector('[data-feature-preview]'));
    select(0);playback();
  });
})();
