(function(){
  const $list = document.getElementById('chat-sessions');
  const $log  = document.getElementById('chatlog');
  const $sid  = document.getElementById('chat-current-sid');
  const $meta = document.getElementById('chat-meta');
  const $q    = document.getElementById('chat-search');
  const $btn  = document.getElementById('chat-search-btn');

  if (!$list) return; // chưa ở tab chats

  async function fetchSessions(q){
    const url = q ? `/admin/api/chats?q=${encodeURIComponent(q)}` : `/admin/api/chats`;
    const res = await fetch(url);
    const data = await res.json();
    return data.items || [];
  }

  function renderSessions(items){
    $list.innerHTML = '';
    if (!items.length){
      $list.innerHTML = '<li class="empty">Không có session nào</li>';
      return;
    }
    for (const it of items){
      const li = document.createElement('li');
      li.className = 'chat-item';
      li.innerHTML = `
        <div class="sid">${it.session_id}</div>
        <div class="sub muted">${it.msg_count} tin nhắn • ${it.last_time || ''}</div>
      `;
      li.addEventListener('click', ()=> openSession(it.session_id));
      $list.appendChild(li);
    }
  }

  async function openSession(sessionId){
    try{
      const res = await fetch(`/admin/api/chats/${sessionId}`);
      const data = await res.json();
      $sid.textContent = sessionId;
      renderLog(data.messages || []);
    }catch{
      $sid.textContent = '—';
      $log.innerHTML = '<div class="muted">Không tải được lịch sử.</div>';
    }
  }

  function renderLog(messages){
    $log.innerHTML = '';
    if (!messages.length){
      $meta.textContent = 'Không có tin nhắn nào trong session này.';
      return;
    }
    $meta.textContent = `${messages.length} tin nhắn`;
    for (const m of messages){
      const div = document.createElement('div');
      div.className = `bubble ${m.role === 'user' ? 'user' : 'bot'}`;
      div.title = m.created_at || '';
      div.textContent = m.content;
      $log.appendChild(div);
    }
    $log.scrollTop = $log.scrollHeight;
  }

  async function loadInitial(){
    const items = await fetchSessions();
    renderSessions(items);
    if (items.length) openSession(items[0].session_id);
  }

  $btn && $btn.addEventListener('click', async ()=>{
    const items = await fetchSessions($q.value.trim());
    renderSessions(items);
  });
  $q && $q.addEventListener('keydown', async (e)=>{
    if (e.key === 'Enter'){ e.preventDefault(); const items = await fetchSessions($q.value.trim()); renderSessions(items); }
  });

  loadInitial();
})();
