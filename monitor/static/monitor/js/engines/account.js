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
            // Legacy init - DOM logic moved to Alpine.js
            window.$ = window.$ || (id => document.getElementById(id));
        },

        showToast: function(msg, isError = false) {
            const el = document.querySelector('.acc-wrap-global');
            if (el) el.dispatchEvent(new CustomEvent('show-toast', { detail: { msg, isError }, bubbles: true }));
        },

        openModal: function(id) { 
            // Legacy fallback, mostly handled by Alpine now
            const el = document.getElementById(id);
            if (el) el.classList.add('show'); 
        },

        closeModal: function(id) {
            const el = document.querySelector('.acc-wrap-global');
            if (!el) return;
            if (id === 'add-modal') el.dispatchEvent(new CustomEvent('close-add-modal', { bubbles: true }));
            if (id === 'edit-modal') el.dispatchEvent(new CustomEvent('close-edit-modal', { bubbles: true }));
            if (id === 'del-modal') el.dispatchEvent(new CustomEvent('close-del-modal', { bubbles: true }));
            
            // Clear errors
            const targetEl = document.getElementById(id);
            if (targetEl) {
                targetEl.querySelectorAll('.fm-error').forEach(e => { e.style.display = 'none'; e.textContent = ''; });
                const errBox = document.getElementById(id.replace('-modal', '-error'));
                if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
            }
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
            setTimeout(() => {
                const un = document.getElementById('add-username');
                const rn = document.getElementById('add-realname');
                const jn = document.getElementById('add-jobnum');
                const pw = document.getElementById('add-pwd');
                const rl = document.getElementById('add-role');
                if(un) un.value = '';
                if(rn) rn.value = '';
                if(jn) jn.value = '';
                if(pw) pw.value = '';
                if(rl) rl.value = 'Operator';
                if(un) setTimeout(() => un.focus(), 100);
            }, 50);
        },

        setEditData: function(uid, username, realname, role, isActive) {
            setTimeout(() => {
                const eUid = document.getElementById('edit-uid');
                const eRn = document.getElementById('edit-realname');
                const eRl = document.getElementById('edit-role');
                const eAc = document.getElementById('edit-active');
                const ePw = document.getElementById('edit-pwd');
                const sub = document.getElementById('edit-modal-sub');
                if(eUid) eUid.value = uid;
                if(eRn) eRn.value = realname;
                if(eRl) eRl.value = role;
                if(eAc) eAc.value = String(isActive);
                if(ePw) ePw.value = '';
                if(sub) sub.textContent = `编辑账号：${username}`;
            }, 50);
        },

        setDelData: function(uid, username) {
            document.getElementById('del-uid').value = uid;
            const msg = document.getElementById('del-msg');
            if (msg) msg.innerHTML = `您确定要永久删除账号「<strong>${username}</strong>」吗？这将使其立即失去访问权限。`;
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
