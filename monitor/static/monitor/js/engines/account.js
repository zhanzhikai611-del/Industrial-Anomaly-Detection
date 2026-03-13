/**
 * Account Management Engine
 * V2.2.3 [Modularized]
 */

window.AccountApp = {
    state: {
        isProcessing: false
    },

    ui: {
        init: function() {
            // Ensure helper $ exists if needed, though this file uses standard DOM
            window.$ = window.$ || (id => document.getElementById(id));

            // Global click listener for routing events to dynamically or statically rendered elements
            document.addEventListener('click', e => {
                const editBtn = e.target.closest('.edit-btn');
                const delBtn = e.target.closest('.del-btn');

                if (editBtn) {
                    const d = editBtn.dataset;
                    AccountApp.ui.openEditModal(d.uid, d.username, d.realname, d.role, d.active === 'true');
                } else if (delBtn) {
                    const d = delBtn.dataset;
                    AccountApp.ui.confirmDelete(d.uid, d.username);
                } else if (e.target.classList.contains('acc-modal-overlay')) {
                    AccountApp.ui.closeModal(e.target.id);
                }
                
                // Close custom selects if clicking outside
                if (!e.target.closest('.custom-select-container')) {
                    AccountApp.ui.closeAllSelects();
                }
            });
        },

        showToast: function(msg, isError = false) {
            const t = document.getElementById('acc-toast');
            if (!t) return;
            t.textContent = msg;
            t.style.background = isError ? '#f56c6c' : '#303133';
            t.classList.add('show');
            setTimeout(() => t.classList.remove('show'), 2800);
        },

        openModal: function(id) { 
            const el = document.getElementById(id);
            if (el) el.classList.add('show'); 
        },

        closeModal: function(id) {
            const el = document.getElementById(id);
            if (!el) return;
            el.classList.remove('show');
            // Clear errors
            el.querySelectorAll('.fm-error').forEach(e => { e.style.display = 'none'; e.textContent = ''; });
            const errBox = document.getElementById(id.replace('-modal', '-error'));
            if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
        },

        toggleCustomSelect: function(event, containerId) {
            event.stopPropagation();
            const container = document.getElementById(containerId);
            if (!container) return;
            const wasOpen = container.classList.contains('open');
            AccountApp.ui.closeAllSelects();
            if (!wasOpen) container.classList.add('open');
        },

        closeAllSelects: function() {
            document.querySelectorAll('.custom-select-container').forEach(c => c.classList.remove('open'));
        },

        togglePw: function(inputId, btn) {
            const inp = document.getElementById(inputId);
            const svg = btn.querySelector('svg');
            if (inp.type === 'password') {
                inp.type = 'text';
                svg.innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
            } else {
                inp.type = 'password';
                svg.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
            }
        },

        openAddModal: function() {
            document.getElementById('add-username').value = '';
            document.getElementById('add-realname').value = '';
            document.getElementById('add-jobnum').value = '';
            document.getElementById('add-pwd').value = '';
            document.getElementById('add-role').value = 'Operator';
            AccountApp.ui.openModal('add-modal');
            setTimeout(() => document.getElementById('add-username').focus(), 100);
        },

        openEditModal: function(uid, username, realname, role, isActive) {
            document.getElementById('edit-uid').value = uid;
            document.getElementById('edit-realname').value = realname;
            document.getElementById('edit-role').value = role;
            document.getElementById('edit-active').value = String(isActive);
            document.getElementById('edit-pwd').value = '';
            const sub = document.getElementById('edit-modal-sub');
            if (sub) sub.textContent = `编辑账号：${username}`;
            AccountApp.ui.openModal('edit-modal');
        },

        confirmDelete: function(uid, username) {
            document.getElementById('del-uid').value = uid;
            const msg = document.getElementById('del-msg');
            if (msg) msg.innerHTML = `您确定要永久删除账号「<strong>${username}</strong>」吗？这将使其立即失去访问权限。`;
            AccountApp.ui.openModal('del-modal');
        }
    },

    filters: {
        selectRole: function(val) {
            const el = document.getElementById('role-filter');
            if (el) el.value = val;
            AccountApp.filters.applySearch();
        },

        applySearch: function() {
            const qInput = document.getElementById('search-input');
            const roleInput = document.getElementById('role-filter');
            const q = qInput ? qInput.value.trim() : '';
            const role = roleInput ? roleInput.value : '';
            const params = new URLSearchParams({ q, role });
            window.location.href = '/accounts/?' + params.toString();
        }
    },

    auth: {
        getCsrf: function() {
            return document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('csrftoken='))?.split('=')[1] || '';
        },

        submitAdd: async function() {
            const username = document.getElementById('add-username').value.trim();
            const realname = document.getElementById('add-realname').value.trim();
            const jobnum = document.getElementById('add-jobnum').value.trim();
            const pwd = document.getElementById('add-pwd').value;
            const role = document.getElementById('add-role').value;

            let valid = true;
            const setErr = (id, msg) => {
                const el = document.getElementById(id);
                if (el) {
                    el.textContent = msg; el.style.display = msg ? 'block' : 'none';
                }
                if (msg) valid = false;
            };

            setErr('err-username', !username ? '用户名为必填项' : '');
            setErr('err-realname', !realname ? '真实姓名为必填项' : '');
            setErr('err-jobnum', !jobnum ? '工号为必填项' : (!/^\d{6}$/.test(jobnum) ? '工号须为6位数字' : ''));
            setErr('err-pwd', !pwd ? '密码为必填项' : (pwd.length < 6 ? '密码至少6位' : ''));

            if (!valid) return;

            try {
                const resp = await fetch('/api/accounts/create/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': AccountApp.auth.getCsrf() },
                    body: JSON.stringify({ username, password: pwd, real_name: realname, job_number: jobnum, role })
                });
                const data = await resp.json();
                if (resp.ok && data.status === 'ok') {
                    AccountApp.ui.closeModal('add-modal');
                    AccountApp.ui.showToast('✓ 账号创建成功');
                    setTimeout(() => location.reload(), 800);
                } else {
                    const errBox = document.getElementById('add-error');
                    if (errBox) {
                        errBox.textContent = data.message || '创建失败，请重试';
                        errBox.style.display = 'block';
                    }
                }
            } catch (e) {
                AccountApp.ui.showToast('网络请求失败', true);
            }
        },

        submitEdit: async function() {
            const uid = document.getElementById('edit-uid').value;
            const realname = document.getElementById('edit-realname').value.trim();
            const role = document.getElementById('edit-role').value;
            const isActive = document.getElementById('edit-active').value === 'true';
            const pwd = document.getElementById('edit-pwd').value;

            const payload = { real_name: realname, role, is_active: isActive };
            if (pwd) {
                if (pwd.length < 6) {
                    const errBox = document.getElementById('edit-error');
                    if (errBox) {
                        errBox.textContent = '新密码至少6位';
                        errBox.style.display = 'block';
                    }
                    return;
                }
                payload.password = pwd;
            }

            try {
                const resp = await fetch(`/api/accounts/${uid}/update/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': AccountApp.auth.getCsrf() },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();
                if (resp.ok && data.status === 'ok') {
                    AccountApp.ui.closeModal('edit-modal');
                    AccountApp.ui.showToast('✓ 账号信息已更新');
                    setTimeout(() => location.reload(), 800);
                } else {
                    const errBox = document.getElementById('edit-error');
                    if (errBox) {
                        errBox.textContent = data.message || '更新失败，请重试';
                        errBox.style.display = 'block';
                    }
                }
            } catch (e) {
                AccountApp.ui.showToast('网络请求失败', true);
            }
        },

        executeDelete: async function() {
            const uid = document.getElementById('del-uid').value;
            const btn = document.getElementById('btn-del-confirm');
            if (!btn || AccountApp.state.isProcessing) return;

            const oldHtml = btn.innerHTML;
            AccountApp.state.isProcessing = true;
            btn.disabled = true;
            btn.innerHTML = '正在处理...';
            btn.style.opacity = '0.7';

            try {
                const resp = await fetch(`/api/accounts/${uid}/delete/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': AccountApp.auth.getCsrf() }
                });
                const data = await resp.json();
                if (resp.ok && data.status === 'ok') {
                    AccountApp.ui.closeModal('del-modal');
                    AccountApp.ui.showToast('✓ 账号已成功注销');
                    setTimeout(() => location.reload(), 800);
                } else {
                    AccountApp.ui.showToast(data.message || '删除失败，请联系管理员', true);
                    btn.disabled = false;
                    btn.innerHTML = oldHtml;
                    btn.style.opacity = '1';
                    AccountApp.state.isProcessing = false;
                }
            } catch (e) {
                AccountApp.ui.showToast('通讯故障，请稍后再试', true);
                btn.disabled = false;
                btn.innerHTML = oldHtml;
                btn.style.opacity = '1';
                AccountApp.state.isProcessing = false;
            }
        }
    }
};

// Initialization
document.addEventListener('DOMContentLoaded', AccountApp.ui.init);
