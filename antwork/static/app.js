const $ = id => document.getElementById(id);
let state = { jobs: [] }, filter = 'all', socket = null, imageUrl = null, submitting = false, requestId = null;
const statuses = {queued:'Queued',running:'Running',need_attention:'Need attention',completed:'Completed',failed:'Failed',cancelled:'Cancelled',interrupted:'Interrupted'};
const terminal = new Set(['completed','failed','cancelled','interrupted']);

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { ...options, headers: {'Content-Type':'application/json', ...options.headers} });
  const data = await response.json();
  if (response.status === 401) showLogin();
  if (!response.ok) throw new Error(data.detail || 'Permintaan gagal');
  return data;
}
const post = (path, body) => api(path, {method:'POST', ...(body === undefined ? {} : {body:JSON.stringify(body)})});
function showLogin() { $('workspace').hidden = true; $('login').hidden = false; closeBrowser(); }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; }
function accounts() {
  return $('accounts').value.replace(/^\uFEFF/, '').split(/\r?\n/).filter(line => line.trim()).map((line, index) => {
    // A CSV containing one quoted column per row is accepted as well as plain TXT.
    if (line.startsWith('"') && line.endsWith('"')) line = line.slice(1,-1).replace(/""/g, '"');
    const colon = line.indexOf(':');
    if (colon < 1 || !line.slice(colon + 1)) throw new Error(`Baris ${index + 1}: gunakan email:password`);
    return {email:line.slice(0,colon).trim(), password:line.slice(colon + 1)};
  });
}
function render() {
  const jobs = state.jobs;
  $('total').textContent = jobs.length;
  $('running').textContent = jobs.filter(j => j.status === 'running').length;
  $('attention').textContent = jobs.filter(j => j.status === 'need_attention').length;
  $('completed').textContent = jobs.filter(j => j.status === 'completed' && (!j.action || j.action === 'purchase' || j.result === 'payment_confirmed')).length;
  $('worker-count').textContent = jobs.length;
  $('release-notice').hidden = state.live_enabled;
  $('cancel-batch').hidden = !state.active_batch;
  const select = $('concurrency');
  if (select.options.length !== state.max_concurrency) {
    select.replaceChildren(...Array.from({length:state.max_concurrency}, (_, i) => new Option(i+1, i+1)));
  }
  select.value = state.concurrency || 1;
  $('batch-concurrency').max = state.max_concurrency;
  const visible = jobs.filter(j => filter === 'all' || j.status === filter);
  $('jobs').replaceChildren(...visible.map(job => {
    const row = document.createElement('tr');
    const account = row.insertCell();
    const email = document.createElement('strong'); email.textContent = job.email;
    const actionNames = {purchase:'Pendaftaran & saldo',open_session:'Buka sesi',check_email:'Cek email',check_account:'Cek dashboard',refresh_session:'Refresh sesi',check_topup:'Cek & isi saldo'};
    const id = document.createElement('small'); id.textContent = `${actionNames[job.action] || 'Worker'} · ${job.id.slice(0,8)}`;
    const session = document.createElement('small');
    session.textContent = job.session_status === 'saved' ? 'Sesi tersimpan' : job.session_status === 'save_failed' ? 'Sesi gagal disimpan' : 'Belum ada sesi tersimpan';
    session.className = 'session-note';
    account.append(email,id,session);
    const badge = document.createElement('span'); badge.className = `status ${job.status}`; badge.textContent = statuses[job.status] || job.status;
    row.insertCell().append(badge);
    const phaseCell = row.insertCell(); phaseCell.textContent = job.phase;
    const accountLabels = {suspended:'Akun: suspend terdeteksi',no_suspend_detected:'Akun: belum terdeteksi suspend',verification_required:'Akun: perlu verifikasi',needs_review:'Akun: perlu diperiksa',needs_login:'Sesi: perlu login ulang',ready_for_topup:'Sesi: login valid · siap isi saldo'};
    if(accountLabels[job.account_status]) { const accountStatus=document.createElement('strong'); accountStatus.textContent=accountLabels[job.account_status]; accountStatus.className=job.account_status==='suspended'?'suspended-note':'account-note'; phaseCell.append(accountStatus); }
    const actions = row.insertCell();
    function button(text, action, className = 'secondary') {
      const b = document.createElement('button'); b.textContent = text; b.className = className;
      b.onclick = async () => { b.disabled = true; try { await action(); } catch(e) { toast(e.message); } finally { b.disabled = false; } };
      actions.append(b);
      return b;
    }
    if (job.status === 'need_attention') {
      button('Buka browser', () => openBrowser(job));
      button('Retry', async () => { await post(`/jobs/${job.id}/actions/refresh_session`); await refresh(); });
      button('Lanjutkan', async () => { await post(`/jobs/${job.id}/resume`); await refresh(); });
    }
    if (!terminal.has(job.status)) button('Batalkan', async () => {
      if (confirm('Batalkan worker? Transaksi yang sudah dikirim tidak dapat ditarik kembali.')) {
        await post(`/jobs/${job.id}/cancel`); await refresh();
      }
    }, 'danger-text');
    if (terminal.has(job.status) && job.action === 'purchase' && job.session_status === 'saved') {
      const queueAction = async action => { await post(`/jobs/${job.id}/actions/${action}`); await refresh(); };
      button('Buka sesi', () => queueAction('open_session'));
      button('Cek', () => openTopup(job));
      const check = button('Cek email', () => queueAction('check_email'));
      check.disabled = !state.email_check_enabled;
      if(check.disabled) check.title = 'Pemeriksa email belum diverifikasi';
      const retry = button('Retry', () => queueAction('refresh_session'));
      retry.title = 'Buka sesi worker ini dan muat ulang halaman tersimpannya';
    }
    return row;
  }));
  $('empty').hidden = visible.length > 0;
  $('last-updated').textContent = `Diperbarui ${new Date().toLocaleTimeString('id-ID')}`;
}
async function refresh() {
  state = await api('/state');
  $('login').hidden = true; $('workspace').hidden = false; render();
}
$('login-form').onsubmit = async e => {
  e.preventDefault(); $('login-error').textContent = '';
  try { await post('/login', {password:$('admin-password').value}); $('admin-password').value=''; await refresh(); }
  catch(error) { $('login-error').textContent = error.message; }
};
$('logout').onclick = async () => { try { await post('/logout'); } finally { showLogin(); } };
async function newBatch() { requestId = crypto.randomUUID(); $('batch-error').textContent = ''; $('start').disabled = !state.live_enabled || !!state.active_batch; $('batch-dialog').showModal(); updateStart(); try { const data = await api('/address'); if(data.address) for(const [key,value] of Object.entries(data.address)) $(key.replace('_','-')).value = value; } catch(e) { $('batch-error').textContent=e.message; } updateStart(); }
$('new-batch').onclick = newBatch; $('empty-new').onclick = newBatch;
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => $(b.dataset.close).close());
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
  filter = b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('selected',x===b)); render();
});
$('account-file').onchange = async e => {
  const file = e.target.files[0]; if (!file) return;
  if (!/\.(txt|csv)$/i.test(file.name) || file.size > 200000) { $('batch-error').textContent='Gunakan TXT/CSV maksimal 200 KB'; return; }
  $('accounts').value = await file.text(); updateSummary(); updateStart();
};
function updateSummary() {
  try { const count = accounts().length; const cents = Math.round(Number($('amount').value || 0)*100); $('batch-summary').textContent = `${count} akun · USD ${(count*cents/100).toFixed(2)}`; }
  catch(e) { $('batch-summary').textContent=e.message; }
}
$('accounts').oninput=updateSummary; $('amount').oninput=updateSummary;
$('batch-dialog').addEventListener('close', () => { if (!submitting) { $('batch-form').reset(); updateSummary(); } });
$('batch-form').onsubmit = async e => {
  e.preventDefault(); if (submitting) return;
  $('batch-error').textContent='';
  try {
    const list = accounts();
    if (!list.length || list.length > 100) throw new Error('Batch harus berisi 1–100 akun');
    if (new Set(list.map(a=>a.email.toLowerCase())).size !== list.length) throw new Error('Ada email duplikat');
    const amount = Math.round(Number($('amount').value)*100), limit = Math.round(Number($('total-limit').value)*100);
    if (amount*list.length > limit) throw new Error('Total rencana pembelian melebihi batas batch');
    const payload = {request_id:requestId, accounts:list, concurrency:Number($('batch-concurrency').value), amount_usd:$('amount').value, total_limit_usd:$('total-limit').value,
      identity:{full_name:$('full-name').value,organization:$('organization').value,address:$('address').value,city:$('city').value,region:$('region').value,postal_code:$('postal-code').value,country:$('country').value.toUpperCase()},
      card:{holder:$('card-holder').value,number:$('card-number').value,expiry:$('expiry').value,cvv:$('cvv').value}};
    submitting = true; $('start').disabled = true;
    await post('/batches',payload);
    $('batch-form').reset(); $('batch-dialog').close(); await refresh(); toast('Batch dibuat. Worker mulai sesuai concurrency.');
  } catch(error) { $('batch-error').textContent=error.message; }
  finally { submitting = false; $('start').disabled = !state.live_enabled || !!state.active_batch; }
};
$('concurrency').onchange = async e => { try { await post(`/concurrency/${e.target.value}`); await refresh(); } catch(error){toast(error.message);} };
$('cancel-batch').onclick = async () => {
  if (!confirm('Batalkan semua worker dalam batch? Periksa transaksi terakhir sebelum mengulang.')) return;
  try { await post('/batch/cancel'); await refresh(); } catch(error){toast(error.message);}
};
function closeBrowser() {
  if (socket) { socket.close(); socket = null; }
  if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl=null; }
  $('screen').removeAttribute('src'); $('remote-text').value='';
  if ($('browser-dialog').open) $('browser-dialog').close();
}
function openBrowser(job) {
  closeBrowser(); $('browser-title').textContent=job.email; $('connection').textContent='Menghubungkan…';
  $('browser-dialog').showModal();
  socket = new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/jobs/${job.id}/browser`);
  socket.binaryType='blob';
  socket.onopen=()=>{$('connection').textContent='Terhubung · kontrol manual';};
  socket.onmessage=event=>{
    if (event.data instanceof Blob) {
      const previous=imageUrl; imageUrl=URL.createObjectURL(event.data); $('screen').src=imageUrl;
      if(previous) URL.revokeObjectURL(previous);
    } else {
      const data=JSON.parse(event.data);
      if(data.tabs){const selected=$('browser-tabs').value; $('browser-tabs').replaceChildren(...data.tabs.map(t=>new Option(`${t.index+1} · ${t.host}`,t.index))); if([...$('browser-tabs').options].some(o=>o.value===selected)) $('browser-tabs').value=selected;}
    }
  };
  socket.onclose=()=>{$('connection').textContent='Kontrol terputus. Tutup lalu buka lagi untuk melanjutkan.';};
  socket.onerror=()=>{$('connection').textContent='Tidak dapat membuka sesi worker';};
}
function send(event) { if(socket?.readyState===WebSocket.OPEN) socket.send(JSON.stringify(event)); }
$('browser-dialog').addEventListener('close',closeBrowser);
$('screen').onclick=e=>{const rect=e.target.getBoundingClientRect();send({kind:'click',x:Math.round((e.clientX-rect.left)*1280/rect.width),y:Math.round((e.clientY-rect.top)*800/rect.height)});e.target.focus();};
$('screen').onwheel=e=>{e.preventDefault();send({kind:'scroll',delta:Math.max(-1600,Math.min(1600,Math.round(e.deltaY)))});};
$('screen').onkeydown=e=>{
  e.preventDefault();
  if((e.ctrlKey||e.metaKey)&&e.key==='a')send({kind:'key',key:'Control+a'});
  else if(e.key.length===1&&!e.ctrlKey&&!e.metaKey)send({kind:'text',text:e.key});
  else if(['Tab','Enter','Backspace','Delete','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))send({kind:'key',key:e.shiftKey&&e.key==='Tab'?'Shift+Tab':e.key});
};
$('send-text').onclick=()=>{send({kind:'text',text:$('remote-text').value});$('remote-text').value='';};
document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>send({kind:'key',key:b.dataset.key}));
$('browser-tabs').onchange=e=>send({kind:'tab',tab:Number(e.target.value)});
refresh().catch(()=>{});
setInterval(()=>{if(!$('workspace').hidden)refresh().catch(e=>toast(e.message));},3000);

$('save-address').onclick = async () => { const address = {}; for(const key of ['address','city','region','postal_code','country']) address[key]=$(key.replace('_','-')).value; address.country=address.country.toUpperCase(); try { await api('/address',{method:'PUT',body:JSON.stringify(address)}); $('batch-error').textContent='Alamat tersimpan.'; } catch(e) { $('batch-error').textContent=e.message; } };

function updateStart() { let valid = false; try { const list=accounts(); valid=list.length>0 && list.length<=100 && new Set(list.map(a=>a.email.toLowerCase())).size===list.length && Math.round(Number($('amount').value)*100)*list.length<=Math.round(Number($('total-limit').value)*100); } catch(_) {} $('start').disabled=submitting || !state.live_enabled || !!state.active_batch || !$('batch-form').checkValidity() || !valid; }
$('batch-form').addEventListener('input',updateStart);
$('batch-form').addEventListener('change',updateStart);

let topupSource=null, topupRequest=null, topupSubmitting=false;
function openTopup(job) {
  topupSource=job.id; topupRequest=crypto.randomUUID();
  $('topup-form').reset(); $('topup-account').textContent=job.email;
  $('topup-error').textContent=state.saved_card_topup_enabled?'':'Pembelian kartu tertaut belum diaktifkan; pengecekan login tersedia.';
  $('topup-submit').disabled=!state.saved_card_topup_enabled;
  $('topup-dialog').showModal();
}
$('check-login').onclick=async()=>{
  try { await post(`/jobs/${topupSource}/actions/check_account`); $('topup-dialog').close(); await refresh(); }
  catch(e){$('topup-error').textContent=e.message;}
};
$('topup-form').onsubmit=async e=>{
  e.preventDefault(); if(topupSubmitting)return;
  if(Number($('topup-amount').value)>Number($('topup-limit').value)){$('topup-error').textContent='Nominal melebihi batas pembayaran';return;}
  topupSubmitting=true; $('topup-submit').disabled=true;
  try { await post(`/jobs/${topupSource}/topup`,{request_id:topupRequest,amount_usd:$('topup-amount').value,limit_usd:$('topup-limit').value}); $('topup-dialog').close(); await refresh(); }
  catch(error){$('topup-error').textContent=error.message;}
  finally{topupSubmitting=false;$('topup-submit').disabled=!state.saved_card_topup_enabled;}
};
