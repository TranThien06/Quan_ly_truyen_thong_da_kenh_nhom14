'use strict';
const membersState = { id: null, rows: [], invitations: [], access: null, owner: null, busy: false, version: 0 };
const memberStatuses = { active: 'Hoạt động', suspended: 'Tạm ngưng', pending: 'Đang chờ', accepted: 'Đã tham gia', expired: 'Hết hạn', revoked: 'Đã thu hồi' };
function memberError(text) { $('#members-error').textContent = text; $('#members-error').hidden = !text; }
function memberButton(text, action, danger = false) {
  const button = element('button', danger ? 'secondary member-danger' : 'secondary', text);
  button.type = 'button'; button.disabled = membersState.busy; button.onclick = action; return button;
}
async function allPages(path) {
  const result = [];
  for (let page = 1; ; page++) { const rows = await api(`${path}?limit=100&page=${page}`); result.push(...rows); if (rows.length < 100) return result; }
}
function managesMembers() { return membersState.access?.permissions.includes('member.manage'); }
function canManageRole(role) { return managesMembers() && (membersState.access.role === 'admin' || role !== 'admin'); }
function roleOptions(select, current) {
  for (const [role, label] of Object.entries(roles)) if (role !== 'admin' || membersState.access?.role === 'admin' || current === 'admin') select.add(new Option(label, role));
  select.value = current;
}
function renderMembers() {
  const query = $('#member-search').value.trim().toLocaleLowerCase('vi');
  const body = $('#member-rows'); body.replaceChildren();
  const visible = membersState.rows.filter(m => `${m.full_name} ${m.email}`.toLocaleLowerCase('vi').includes(query));
  $('#members-status').textContent = !visible.length ? 'Không có thành viên phù hợp.' : `${membersState.rows.length} thành viên`;
  for (const member of visible) {
    const tr = document.createElement('tr'); const name = document.createElement('td');
    name.append(element('strong', '', member.full_name), element('small', '', member.email));
    if (member.joined_at) name.append(element('small', '', 'Tham gia: ' + new Date(member.joined_at.replace(' ', 'T') + 'Z').toLocaleDateString('vi-VN')));
    if (member.user_id === membersState.owner) name.append(element('small', 'owner-tag', 'Chủ sở hữu'));
    else if (member.user_id === state.user?.user_id) name.append(element('small', '', 'Bạn'));
    const roleCell = document.createElement('td'); const actions = document.createElement('td'); actions.className = 'member-actions';
    const editable = canManageRole(member.role) && member.user_id !== membersState.owner;
    if (editable) {
      const select = document.createElement('select'); select.setAttribute('aria-label', 'Vai trò của ' + member.full_name); roleOptions(select, member.role); select.disabled = membersState.busy;
      roleCell.append(select);
      actions.append(memberButton('Lưu vai trò', () => confirmMember(`Đổi vai trò của ${member.full_name} thành ${roles[select.value]}?`, () => mutateMembers(`/members/${member.member_id}`, 'PATCH', { role: select.value }), select.value === member.role)));
      actions.append(memberButton('Loại', () => confirmMember(`Loại ${member.full_name} khỏi workspace? Người này sẽ mất quyền truy cập ngay.`, () => mutateMembers(`/members/${member.member_id}`, 'DELETE')), true));
    } else { roleCell.textContent = roles[member.role] || member.role; actions.textContent = '—'; }
    tr.append(name, roleCell, element('td', '', memberStatuses[member.status] || member.status), actions); body.append(tr);
  }
  $('#invitation-section').hidden = !managesMembers();
  const inviteRole = $('#invite-form').elements.role; const previous = inviteRole.value || 'viewer'; inviteRole.replaceChildren(); roleOptions(inviteRole, previous); if (!inviteRole.value) inviteRole.value = 'viewer';
  const invites = $('#invitation-rows'); invites.replaceChildren();
  for (const invitation of membersState.invitations) {
    const tr = document.createElement('tr'); const actions = document.createElement('td'); actions.className = 'member-actions';
    const expired = invitation.status === 'pending' && new Date(invitation.expires_at.replace(' ', 'T') + 'Z') <= new Date();
    if (['pending', 'expired'].includes(invitation.status) && canManageRole(invitation.role)) actions.append(memberButton('Mời lại', () => mutateMembers(`/invitations/${invitation.invitation_id}/resend`, 'POST')));
    tr.append(element('td', '', invitation.email), element('td', '', roles[invitation.role] || invitation.role), element('td', '', expired ? 'Hết hạn' : memberStatuses[invitation.status] || invitation.status), actions); invites.append(tr);
  }
  for (const control of $('#invite-form').elements) control.disabled = membersState.busy;
}
async function refreshMembers() {
  const version = ++membersState.version; const id = membersState.id;
  memberError(''); $('#members-status').textContent = 'Đang tải…';
  membersState.access = null; membersState.rows = []; membersState.invitations = [];
  // Hide stale controls until the server has checked the current permissions.
  $('#member-rows').replaceChildren(); $('#invitation-section').hidden = true;
  try {
    const base = `/workspaces/${id}`;
    const [ws, access, rows] = await Promise.all([api(base), api(base + '/access'), allPages(base + '/members')]);
    const invitations = access.permissions.includes('member.manage') ? await allPages(base + '/invitations') : [];
    if (version !== membersState.version || !$('#members-dialog').open) return;
    Object.assign(membersState, { rows, invitations, access, owner: ws.owner_id });
    $('#members-workspace').textContent = ws.name; renderMembers();
  } catch (error) { if (version === membersState.version) { membersState.access = null; membersState.rows = []; membersState.invitations = []; $('#member-rows').replaceChildren(); $('#members-status').textContent = ''; memberError(error.message); } }
}
async function mutateMembers(path, method, values) {
  if (membersState.busy) return;
  membersState.busy = true; renderMembers(); memberError('');
  try {
    await api(`/workspaces/${membersState.id}${path}`, { method, ...(values ? { body: JSON.stringify(values) } : {}) });
    notify(path.includes('invitation') ? 'Lời mời đã được đưa vào hàng đợi gửi email.' : 'Đã lưu thay đổi thành viên.');
    await refreshMembers(); await load();
  } catch (error) { await refreshMembers(); memberError(error.message); }
  finally { membersState.busy = false; renderMembers(); }
}
function confirmMember(text, action, unchanged = false) {
  if (unchanged) { notify('Vai trò chưa thay đổi.'); return; }
  $('#member-confirm-text').textContent = text; $('#member-confirm-dialog').showModal();
  $('#member-confirm-ok').onclick = () => { $('#member-confirm-dialog').close(); action(); };
}
$('#member-confirm-cancel').onclick = () => $('#member-confirm-dialog').close();
$('#members-button').onclick = () => {
  if (state.busy) return;
  membersState.id = state.editing; $('#workspace-dialog').close(); $('#member-search').value = '';
  membersState.rows = []; membersState.invitations = []; membersState.access = null;
  $('#members-dialog').showModal(); refreshMembers();
};
$('#close-members').onclick = () => { if (!membersState.busy) $('#members-dialog').close(); };
$('#members-dialog').addEventListener('cancel', event => { if (membersState.busy) event.preventDefault(); });
$('#members-dialog').addEventListener('close', () => { membersState.version++; });
$('#refresh-members').onclick = () => { if (!membersState.busy) refreshMembers(); };
$('#member-search').oninput = renderMembers;
$('#invite-form').onsubmit = event => {
  event.preventDefault(); if (!managesMembers() || membersState.busy) return;
  mutateMembers('/invitations', 'POST', { email: event.target.elements.email.value.trim(), role: event.target.elements.role.value });
};
const invitationToken = new URLSearchParams(location.search).get('token');
if (location.pathname === '/accept-invitation' && invitationToken) $('#accept-dialog').showModal();
$('#accept-close').onclick = () => $('#accept-dialog').close();
$('#accept-button').onclick = async () => {
  if (!state.token) { $('#accept-dialog').close(); $('#auth-dialog').showModal(); return; }
  $('#accept-button').disabled = true; $('#accept-error').hidden = true;
  try { await api('/invitations/accept', { method: 'POST', body: JSON.stringify({ token: invitationToken }) }); $('#accept-dialog').close(); history.replaceState({}, '', '/'); notify('Đã tham gia workspace.'); await load(); }
  catch (error) { $('#accept-error').textContent = error.message; $('#accept-error').hidden = false; }
  finally { $('#accept-button').disabled = false; }
};
$('#auth-dialog').addEventListener('close', () => { if (invitationToken && state.token && location.pathname === '/accept-invitation') $('#accept-dialog').showModal(); });
