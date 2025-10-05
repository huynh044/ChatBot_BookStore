(function(){
  const form = document.getElementById('login-form');
  const hint = document.getElementById('login-hint');
  form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    hint.textContent = '';
    const payload = Object.fromEntries(new FormData(form).entries());
    try{
      const res = await fetch('/admin/login', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if(data.ok){ location.href = data.redirect || '/admin'; } // <- đổi /admin
      else { hint.textContent = data.message || 'Đăng nhập thất bại'; }
    }catch(err){
      hint.textContent = 'Lỗi mạng. Thử lại.';
    }
  });
})();
