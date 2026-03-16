/**
 * System Settings Engine
 * V2.2.3 [Modularized]
 */

window.SettingApp = {
    state: {
        isStreamRunning: false,
        isProcessing: false,
        isInitialized: false,
        pollInterval: null
    },

    utils: {
        getCookie: function(name) {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }
    },

    ui: {
        init: function() {
            if (SettingApp.state.isInitialized) {
                console.log('[SettingApp] Refreshing...');
                SettingApp.stream.checkStatus();
                return;
            }

            SettingApp.stream.checkStatus();
            // Regular status polling
            if (SettingApp.state.pollInterval) clearInterval(SettingApp.state.pollInterval);
            SettingApp.state.pollInterval = setInterval(SettingApp.stream.checkStatus, 5000);
            
            SettingApp.state.isInitialized = true;
        },

        updateStreamUI: function(isActive) {
            SettingApp.state.isStreamRunning = isActive;
            const el = document.querySelector('.set-wrap-global');
            if (el) el.dispatchEvent(new CustomEvent('stream-status-updated', { detail: { isActive }, bubbles: true }));
        },

        openResetModal: function() {
            // Handled by Alpine now
        },

        closeResetModal: function() {
            const el = document.querySelector('.set-wrap-global');
            if (el) el.dispatchEvent(new CustomEvent('close-reset-modal', { bubbles: true }));
        },

        showSuccessToast: function(title, message) {
            const el = document.querySelector('.set-wrap-global');
            if (el) el.dispatchEvent(new CustomEvent('show-success-toast', { 
                detail: { title, msg: message }, bubbles: true
            }));
        }
    },

    stream: {
        checkStatus: async function() {
            // Guard: Check if we are still on the settings page
            const checkEl = document.getElementById('base-metrics');
            if (!checkEl) {
                if (SettingApp.state.pollInterval) clearInterval(SettingApp.state.pollInterval);
                return;
            }

            try {
                const res = await fetch('/api/stream-status/');
                const data = await res.json();
                SettingApp.ui.updateStreamUI(data.is_active);
                SettingApp.stream.refreshStats();
            } catch (e) {
                console.error('Failed to get status', e);
            }
        },

        toggle: async function(newState) {
            // Remove the early return check to ensure force-syncing from UI works
            const segmentContainer = document.getElementById('stream-segment');
            if (segmentContainer) segmentContainer.classList.add('disabled');

            const action = newState ? 'start' : 'stop';
            try {
                const res = await fetch(`/api/system/toggle_stream/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': SettingApp.utils.getCookie('csrftoken')
                    },
                    body: JSON.stringify({ action: action })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    SettingApp.ui.updateStreamUI(data.is_active);
                }
            } catch (e) {
                console.error('Failed to toggle stream', e);
                alert('网络或访问错误');
                SettingApp.ui.updateStreamUI(!newState);
            } finally {
                if (segmentContainer) segmentContainer.classList.remove('disabled');
            }
        },

        confirmReset: async function() {
            const btn = document.getElementById('btn-execute-reset');
            if (!btn || SettingApp.state.isProcessing) return;

            SettingApp.state.isProcessing = true;
            btn.disabled = true;
            btn.innerText = '重置中...';

            try {
                const res = await fetch('/api/system/reset_groups/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': SettingApp.utils.getCookie('csrftoken')
                    }
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    SettingApp.ui.closeResetModal();
                    SettingApp.ui.showSuccessToast('成功重置风险分布', '已恢复初始的分组梯度分布');
                } else {
                    alert('× 重置失败: ' + data.message);
                }
            } catch (e) {
                console.error('Reset failed', e);
                alert('网络错误，请稍后重试');
            } finally {
                btn.disabled = false;
                btn.innerText = '立即重置';
                SettingApp.state.isProcessing = false;
            }
        },

        refreshStats: function() {
            // Future implementation for real-time stats update if needed
        }
    },

    config: {
        saveThresh: async function() {
            const payload = {
                ai: document.getElementById('thresh-ai').value,
                cur: document.getElementById('thresh-cur').value,
                pwr: document.getElementById('thresh-pwr').value,
                fv: document.getElementById('thresh-fv').value
            };

            const msg = document.getElementById('thresh-msg');
            if (msg) msg.innerText = '保存中...';

            // Mock saving delay for feedback
            setTimeout(() => {
                if (msg) {
                    msg.innerText = '✓ 保存成功';
                    setTimeout(() => { msg.innerText = ''; }, 3000);
                }
            }, 500);
        }
    }
};

// Initial entry point
document.addEventListener('DOMContentLoaded', SettingApp.ui.init);
