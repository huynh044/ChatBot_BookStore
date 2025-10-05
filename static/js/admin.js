(function(){
  // Tabs level 1 (orders/books/chats)
  const tabs = document.querySelectorAll('.tabs .tab[data-tab]');
  const panels = {
    orders: document.getElementById('panel-orders'),
    books: document.getElementById('panel-books'),
    chats: document.getElementById('panel-chats'), // <- THÊM
  };
  tabs.forEach(btn=>{
    btn.addEventListener('click', ()=>{
      tabs.forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      const key = btn.dataset.tab;
      Object.values(panels).forEach(p=>p && p.classList.remove('active'));
      if (panels[key]) panels[key].classList.add('active');
    });
  });

  // Tabs level 2 (orders: pending/approved/cancelled)
  const subtabs = document.querySelectorAll('.tabs .tab[data-subtab]');
  const subpanels = {
    pending: document.getElementById('sub-pending'),
    approved: document.getElementById('sub-approved'),
    cancelled: document.getElementById('sub-cancelled'),
  };
  subtabs.forEach(btn=>{
    btn.addEventListener('click', ()=>{
      subtabs.forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      const key = btn.dataset.subtab;
      Object.values(subpanels).forEach(p=>p && p.classList.remove('active'));
      if (subpanels[key]) subpanels[key].classList.add('active');
    });
  });

  // WS for admin (new orders)
  const wsState = document.getElementById('wsstate');
  function connectWS(){
    const ws = new WebSocket(`ws://${location.host}/ws/admin`);
    ws.onopen = ()=> wsState && (wsState.textContent = 'connected');
    ws.onclose = ()=> { if (wsState) wsState.textContent = 'disconnected'; setTimeout(connectWS, 1500); };
    ws.onmessage = (ev)=>{
      try{
        const msg = JSON.parse(ev.data);
        if(msg.type === 'new_order'){
          alert(`Có đơn mới #${msg.order_id}`);
          location.reload();
        }
      }catch{}
    };
  }
  if (wsState) connectWS();

  // Order actions
  window.approve = async function(orderId){
    const r = await fetch(`/admin/orders/${orderId}/approve`, {method:'POST'});
    const d = await r.json();
    if(d.ok) location.reload(); else alert(d.message || 'Không duyệt được');
  }
  window.cancelOrder = async function(orderId){
    const r = await fetch(`/admin/orders/${orderId}/cancel`, {method:'POST'});
    const d = await r.json();
    if(d.ok) location.reload(); else alert(d.message || 'Không hủy được');
  }
})();
