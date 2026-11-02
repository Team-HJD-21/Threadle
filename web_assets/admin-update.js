(() => {
  const node = document.querySelector('[data-update-status]');
  if (!node) return;
  const en = document.documentElement.lang === 'en';
  const labels = {
    staged: en ? 'Ready for confirmation' : '적용 확인 대기',
    scheduled: en ? 'Preparing update' : '업데이트 준비 중',
    applying: en ? 'Applying update' : '업데이트 적용 중',
    restarting: en ? 'Restarting service' : '서비스 재시작 중',
    success: en ? 'Update complete' : '업데이트 완료',
    'rolled-back': en ? 'Previous version restored' : '이전 버전으로 복구됨',
    failed: en ? 'Update needs attention' : '업데이트 확인 필요',
    cancelled: en ? 'Cancelled' : '취소됨'
  };
  async function check() {
    let again = true;
    try {
      const response = await fetch('/connect/admin/update/status', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (data.job) {
        node.textContent = (labels[data.job.state] || data.job.state) + ' · ' + data.job.version;
        const detail = document.querySelector('[data-update-detail]');
        if (detail) detail.textContent = data.job.detail || '';
        again = ['scheduled', 'applying', 'restarting'].includes(data.job.state);
      } else again = false;
    } catch (_) {
      node.textContent = en ? 'Waiting for the service to restart…' : '서비스가 다시 켜지기를 기다리고 있어요…';
    }
    if (again) setTimeout(check, 4000);
  }
  if (['scheduled', 'applying', 'restarting'].includes(node.dataset.updateStatus)) check();
})();
