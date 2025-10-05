// static/js/book.js
(function(){
  const $table = document.getElementById('books-table');
  const $tbody = $table ? $table.querySelector('tbody') : null;
  const $btnAdd = document.getElementById('btn-add-book');

  const $modal = document.getElementById('book-modal');
  const $form  = document.getElementById('book-form');
  const $hint  = document.getElementById('book-hint');
  const $title = document.getElementById('book-modal-title');
  const $toast = document.getElementById('toast');

  const $id    = document.getElementById('book-id');
  const $fTitle= document.getElementById('book-title');
  const $fAuthor=document.getElementById('book-author');
  const $fPrice= document.getElementById('book-price');
  const $fStock= document.getElementById('book-stock');
  const $fCat  = document.getElementById('book-category');
  const $submit= document.getElementById('book-submit');

  if (!$table) return;

  // --------- helpers UI ----------
  const onlyInt = v => parseInt(String(v).replace(/[^\d]/g,''),10)||0;
  const fmt = v => new Intl.NumberFormat('vi-VN').format(onlyInt(v));

  function openModal(){
  document.body.classList.add('modal-open');  // << thêm
  $hint.textContent = '';
  $modal.classList.add('show');
  $modal.setAttribute('aria-hidden','false');
    }
    function closeModal(){
    $modal.classList.remove('show');
    $modal.setAttribute('aria-hidden','true');
    document.body.classList.remove('modal-open'); // << thêm
    }
  document.querySelectorAll('[data-close="book-modal"]').forEach(el=> el.addEventListener('click', closeModal));

  function clearForm(){
    $id.value=''; $fTitle.value=''; $fAuthor.value='';
    $fPrice.value=''; $fStock.value=''; $fCat.value='';
  }
  function fillFormFromRow(tr){
    $id.value     = tr.dataset.id || '';
    $fTitle.value = tr.querySelector('.c-title').textContent.trim();
    $fAuthor.value= tr.querySelector('.c-author').textContent.trim();
    $fPrice.value = tr.querySelector('.c-price').textContent.trim();
    $fStock.value = tr.querySelector('.c-stock').textContent.trim();
    $fCat.value   = tr.querySelector('.c-category').textContent.trim();
  }
  function toast(msg){
    if (!$toast) return;
    $toast.textContent = msg;
    $toast.classList.add('show');
    setTimeout(()=> $toast.classList.remove('show'), 2200);
  }

  // render 1 row
  function renderRow(b){
    const tr = document.createElement('tr');
    tr.dataset.id = b.book_id;
    tr.innerHTML = `
      <td class="c-id">${b.book_id}</td>
      <td class="c-title">${b.title}</td>
      <td class="c-author">${b.author}</td>
      <td class="c-price">${b.price}</td>
      <td class="c-stock">${b.stock}</td>
      <td class="c-category">${b.category}</td>
      <td class="actions">
        <button class="btn btn-sm btn-edit" type="button">Sửa</button>
        <button class="btn btn-sm btn-danger btn-delete" type="button">Xoá</button>
      </td>
    `;
    return tr;
  }

  // --------- Add ----------
  $btnAdd && $btnAdd.addEventListener('click', ()=>{
    clearForm();
    $title.textContent = 'Thêm sách';
    openModal();
    $fTitle.focus();
  });

  // --------- Edit / Delete (delegation) ----------
  $tbody.addEventListener('click', (e)=>{
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;

    if (e.target.closest('.btn-edit')){
      fillFormFromRow(tr);
      $title.textContent = `Sửa sách #${tr.dataset.id}`;
      openModal();
      return;
    }

    if (e.target.closest('.btn-delete')){
      const id = tr.dataset.id;
      if (!id) return;
      if (!confirm(`Xoá sách #${id}?`)) return;

      fetch(`/admin/books/${id}`, { method: 'DELETE' })
        .then(r=>r.json().catch(()=>({ok:false})))
        .then(d=>{
          if (d && d.ok){
            tr.classList.add('tr-fadeout');
            setTimeout(()=> tr.remove(), 350);
            toast('Đã xoá sách');
          } else {
            alert((d && d.message) || 'Không xoá được.');
          }
        })
        .catch(()=> alert('Lỗi mạng khi xoá.'));
    }
  });

  // --------- Submit (Add/Update) ----------
  $form.addEventListener('submit', async (e)=>{
    e.preventDefault();
    $hint.textContent = '';

    const payload = {
      title:  $fTitle.value.trim(),
      author: $fAuthor.value.trim(),
      price:  onlyInt($fPrice.value),
      stock:  onlyInt($fStock.value),
      category: $fCat.value.trim()
    };
    if (!payload.title || !payload.author || !payload.category){
      $hint.textContent = 'Vui lòng điền đầy đủ các trường.';
      return;
    }
    if (payload.price <= 0){ $hint.textContent = 'Giá phải > 0.'; return; }
    if (payload.stock < 0){ $hint.textContent = 'Tồn kho không âm.'; return; }

    const id = $id.value.trim();
    const url = id ? `/admin/books/${id}` : '/admin/books';
    const method = id ? 'PUT' : 'POST';

    // disable trong lúc gửi
    $submit.disabled = true;

    try{
      const res = await fetch(url, {
        method,
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(()=>({ok:false}));

      if (!res.ok || !data.ok) throw new Error(data.message || `HTTP ${res.status}`);

      // Cập nhật UI ngay
      if (id) {
        // update row tồn tại
        const tr = $tbody.querySelector(`tr[data-id="${id}"]`);
        if (tr){
          tr.querySelector('.c-title').textContent = payload.title;
          tr.querySelector('.c-author').textContent = payload.author;
          tr.querySelector('.c-price').textContent = payload.price; // để dạng số thô cho dễ xử lý
          tr.querySelector('.c-stock').textContent = payload.stock;
          tr.querySelector('.c-category').textContent = payload.category;
          tr.classList.add('tr-highlight');
          setTimeout(()=> tr.classList.remove('tr-highlight'), 1000);
        }
        toast('Đã lưu chỉnh sửa');
      } else {
        // thêm mới: backend trả {book_id: ...}
        const newId = (data.book_id || data.id);
        const bookObj = {
          book_id: newId,
          ...payload
        };
        const tr = renderRow(bookObj);
        $tbody.prepend(tr);
        tr.classList.add('tr-highlight');
        setTimeout(()=> tr.classList.remove('tr-highlight'), 1000);
        toast('Đã thêm sách mới');
      }

      closeModal();
    }catch(err){
      console.error(err);
      $hint.textContent = 'Không lưu được: ' + (err.message || 'lỗi không rõ');
    }finally{
      $submit.disabled = false;
    }
  });
})();
