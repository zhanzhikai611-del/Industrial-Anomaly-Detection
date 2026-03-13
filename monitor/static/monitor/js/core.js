/**
 * Core Global Helpers & Common Logic
 */

// Global DOM Selector Helper
window.$ = (id) => document.getElementById(id);

window.CoreApp = {
    // Shared Utilities
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
        },
        
        formatDate: function(dateStr) {
            const d = new Date(dateStr);
            return d.toLocaleString('zh-CN', { hour12: false });
        }
    },
    
    // Global UI Managers
    ui: {
        // Toggle the AI Agent dropdown in header
        toggleAiMenu: function() {
            const menu = window.$('aiMenu');
            if (menu) menu.classList.toggle('show');
        },
        
        // Hide AI Menu on outside click
        initAiMenuDismiss: function() {
            document.addEventListener('click', (e) => {
                const btn = window.$('aiSplitBtn');
                const menu = window.$('aiMenu');
                if (menu && btn && !btn.contains(e.target) && !menu.contains(e.target)) {
                    menu.classList.remove('show');
                }
            });
        }
    }
};

// Initialize Global UI Components
document.addEventListener('DOMContentLoaded', () => {
    window.CoreApp.ui.initAiMenuDismiss();
});
