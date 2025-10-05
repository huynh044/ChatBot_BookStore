(async function () {
  let sessionId = window.__SESSION_ID__;
  const $messages = document.getElementById('messages');
  const $form = document.getElementById('chat-form');
  const $input = document.getElementById('msg');
  const $wsState = document.getElementById('wsstate');
  const $mode = document.getElementById('mode-badge');
  const $sid = document.getElementById('sid');

  // NEW buttons + modal (nếu có trong HTML)
  const $btnNew = document.getElementById('btn-new-chat');
  const $btnHistory = document.getElementById('btn-history');
  const $sessionsModal = document.getElementById('sessions-modal');
  const $sessionsList  = document.getElementById('sessions-list');
  const $sessionsEmpty = document.getElementById('sessions-empty');

  let ws = null, wsManualClose = false;

  // ----- Local sessions history -----
  function getStoredSessions(){
    try{ return JSON.parse(localStorage.getItem('chat_sessions') || '[]'); }
    catch{ return []; }
  }
  function saveStoredSessions(arr){
    localStorage.setItem('chat_sessions', JSON.stringify(arr.slice(0,100)));
  }
  function rememberSession(id){
    const arr = getStoredSessions();
    if (!arr.includes(id)){ arr.unshift(id); saveStoredSessions(arr); }
  }

  function addBubble(text, who = 'user') {
    const div = document.createElement('div');
    div.className = `bubble ${who}`;
    div.textContent = text;
    $messages.appendChild(div);
    $messages.scrollTop = $messages.scrollHeight;
  }

  async function loadHistory(id = sessionId){
    try{
      const res = await fetch(`/api/chat/history?session_id=${id}`);
      const data = await res.json();
      $messages.innerHTML = '';
      (data.messages || []).forEach(m => addBubble(m.content, m.role === 'user' ? 'user' : 'bot'));
    }catch{
      addBubble('Không tải được lịch sử chat.', 'bot');
    }
  }

  async function sendMessage(e) {
    e.preventDefault();
    const text = $input.value.trim();
    if (!text) return;
    addBubble(text, 'user');
    $input.value = '';
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sessionId, message: text })
      });
      const data = await res.json();
      addBubble(data.reply || '[no reply]', 'bot');
      $mode && ($mode.textContent = (data.state === 'order_collect' || data.state === 'await_confirm')
        ? 'Ordering' : 'Catalog');
    } catch {
      addBubble('Xin lỗi, có lỗi mạng. Thử lại sau nhé.', 'bot');
    }
  }

  function connectWS() {
    ws = new WebSocket(`ws://${location.host}/ws/${sessionId}`);
    ws.onopen = () => $wsState && ($wsState.textContent = 'connected');
    ws.onclose = () => {
      $wsState && ($wsState.textContent = 'disconnected');
      if (!wsManualClose) setTimeout(connectWS, 1500);
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'order_approved') addBubble(`Đơn #${msg.order_id} đã được duyệt.`, 'bot');
        else if (msg.type === 'order_cancelled') addBubble(`Đơn #${msg.order_id} đã bị hủy.`, 'bot');
      } catch {}
    };
  }
  function reconnectWS(){
    if (ws) {
      wsManualClose = true;
      try { ws.close(); } catch {}
      ws = null; wsManualClose = false;
    }
    connectWS();
  }

  // ----- Reset session -----
  async function resetChat(){
    try{
      const res = await fetch('/api/chat/reset', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) throw new Error('reset failed');

      sessionId = data.session_id;
      window.__SESSION_ID__ = sessionId;
      $sid && ($sid.textContent = sessionId);
      rememberSession(sessionId);

      $messages.innerHTML = '';
      addBubble('Đã tạo đoạn chat mới. Bạn có thể bắt đầu hỏi hoặc đặt sách nhé!', 'bot');
      $mode && ($mode.textContent = 'Catalog');

      reconnectWS();
    }catch{
      alert('Không reset được phiên chat. Thử lại.');
    }
  }

  // ----- Modal lịch sử session -----
  function openSessionsModal(){
    renderSessionsList();
    if ($sessionsModal){
      $sessionsModal.classList.add('show');
      $sessionsModal.setAttribute('aria-hidden','false');
    }
  }
  function closeSessionsModal(){
    if ($sessionsModal){
      $sessionsModal.classList.remove('show');
      $sessionsModal.setAttribute('aria-hidden','true');
    }
  }
  window.closeSessionsModal = closeSessionsModal;

  function renderSessionsList(){
    if (!$sessionsList) return;
    const arr = getStoredSessions();
    $sessionsList.innerHTML = '';
    if ($sessionsEmpty) $sessionsEmpty.style.display = arr.length ? 'none' : 'block';
    arr.forEach(id=>{
      const li = document.createElement('li');
      li.className = 'chat-item';
      li.innerHTML = `
        <div class="sid">${id}</div>
        <div class="actions"><button class="btn">Mở</button></div>
      `;
      li.querySelector('button').addEventListener('click', ()=> switchToSession(id));
      $sessionsList.appendChild(li);
    });
  }

  async function switchToSession(id){
    sessionId = id;
    window.__SESSION_ID__ = sessionId;
    $sid && ($sid.textContent = sessionId);
    rememberSession(sessionId);
    await loadHistory(sessionId);
    reconnectWS();
    closeSessionsModal();
  }

  // ----- Init -----
  rememberSession(sessionId);
  await loadHistory();
  $form.addEventListener('submit', sendMessage);
  $btnNew && $btnNew.addEventListener('click', resetChat);
  $btnHistory && $btnHistory.addEventListener('click', openSessionsModal);
  connectWS();
})();
