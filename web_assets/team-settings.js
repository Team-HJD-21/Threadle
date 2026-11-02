(() => {
  'use strict';
  if (!document.getElementById('team-settings')) return;
  let busy = false;
  const words = () => document.documentElement.lang === 'en' ? {
    failed: 'We could not complete this request. Your inputs are still here. Please try again.',
    signIn: 'Sign in again',
  } : {
    failed: '요청을 완료하지 못했습니다. 입력은 유지되어 있으니 다시 시도해 주세요.',
    signIn: '로그인 다시 하기',
  };

  function feedback(form, message, error = true, reconnect = false) {
    const old = form.querySelector('.request-feedback');
    if (old) old.remove();
    const box = document.createElement('div');
    box.className = 'team-feedback request-feedback' + (error ? ' error' : '');
    box.setAttribute('role', error ? 'alert' : 'status');
    box.textContent = message;
    if (reconnect) {
      const link = document.createElement('a');
      link.href = '/connect';
      link.textContent = words().signIn;
      box.append(link);
    }
    form.append(box);
  }

  function upsert(remote, id) {
    const old = document.getElementById(id);
    const fresh = remote.getElementById(id);
    if (old && fresh) old.replaceWith(document.importNode(fresh, true));
    else if (old) old.remove();
    else if (fresh) {
      const previous = document.getElementById(id === 'work-areas' ? 'team-mode' : 'work-areas');
      if (previous) previous.after(document.importNode(fresh, true));
    }
  }

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest('#team-settings')) return;
    const button = event.submitter;
    const action = new URL(button?.getAttribute('formaction') || form.getAttribute('action'), location.href);
    if (action.origin !== location.origin || !action.pathname.startsWith('/connect/team/')) return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    const data = new FormData(form);
    if (button?.name) data.append(button.name, button.value);
    const top = window.scrollY;
    const focusName = button?.name;
    const focusValue = button?.value;
    const path = action.pathname;
    const anchorSelector = path.endsWith('/preview') ? '#pr-preview .preview-form'
      : path.endsWith('/options') ? '#team-options button[name="apply"]'
      : /\/(group|select|unselect|clear)$/.test(path) ? '#work-areas .area-actions'
      : path.endsWith('/browse') ? '#work-areas .directory-browser' : '#team-mode';
    const anchorBefore = document.querySelector(anchorSelector)?.getBoundingClientRect().top;
    const numberBefore = document.getElementById('number')?.value;
    const folderBefore = document.getElementById('team-settings').dataset.folder;
    const listScroll = document.querySelector('.directory-list')?.scrollTop || 0;
    const disabled = [...form.querySelectorAll('button')].map(node => [node, node.disabled]);
    disabled.forEach(([node]) => { node.disabled = true; });
    if (button) button.classList.add('is-busy');
    form.setAttribute('aria-busy', 'true');
    form.inert = true;
    try {
      const response = await fetch(action, {
        method: 'POST', body: new URLSearchParams(data), credentials: 'same-origin',
        headers: {'Accept': 'text/html', 'Content-Type': 'application/x-www-form-urlencoded'},
        signal: AbortSignal.timeout(45000),
      });
      const url = new URL(response.url);
      if (url.origin !== location.origin) throw new Error('Unexpected response origin');
      const remote = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (!response.ok) {
        const message = remote.querySelector('main .error p, main > p')?.textContent?.trim();
        feedback(form, message || words().failed, true, response.status === 401 || response.status === 403);
        return;
      }
      if (!remote.getElementById('team-settings')) {
        if (url.pathname === '/connect/done') location.assign(url.href);
        else feedback(form, remote.querySelector('main p')?.textContent?.trim() || words().failed, true, true);
        return;
      }
      if (path.endsWith('/preview')) upsert(remote, 'pr-preview');
      else if (path.endsWith('/options')) upsert(remote, 'team-options');
      else {
        if (path.endsWith('/mode')) upsert(remote, 'team-mode');
        upsert(remote, 'work-areas');
        upsert(remote, 'pr-preview');
        const number = document.getElementById('number');
        if (number && numberBefore !== undefined) number.value = numberBefore;
      }
      const root = document.getElementById('team-settings');
      if (!path.endsWith('/preview') && !path.endsWith('/options')) {
        root.dataset.folder = remote.getElementById('team-settings').dataset.folder || '';
      }
      if (folderBefore === root.dataset.folder) {
        const list = document.querySelector('.directory-list');
        if (list) list.scrollTop = listScroll;
      }
      history.replaceState(null, '', url.pathname + url.search);
      const anchorAfter = document.querySelector(anchorSelector)?.getBoundingClientRect().top;
      const delta = anchorBefore === undefined || anchorAfter === undefined ? 0 : anchorAfter - anchorBefore;
      window.scrollTo({top: top + delta, behavior: 'auto'});
      const nextPanel = document.getElementById(path.endsWith('/preview') ? 'pr-preview' : path.endsWith('/options') ? 'team-options' : path.endsWith('/mode') ? 'team-mode' : 'work-areas');
      const nextButton = [...(nextPanel?.querySelectorAll('button') || [])].find(node => focusName
        ? node.name === focusName && node.value === focusValue
        : node.type === 'submit' && !node.hasAttribute('formaction'));
      if (nextButton) nextButton.focus({preventScroll: true});
    } catch {
      feedback(form, words().failed);
    } finally {
      form.inert = false;
      form.removeAttribute('aria-busy');
      disabled.forEach(([node, wasDisabled]) => { node.disabled = wasDisabled; });
      if (button) button.classList.remove('is-busy');
      busy = false;
    }
  });
})();
