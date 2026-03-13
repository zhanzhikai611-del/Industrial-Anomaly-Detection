/**
 * Login Page Logic
 */

function togglePassword() {
    const input = document.getElementById('id_password');
    const eyeOn = document.getElementById('icon-eye');
    const eyeOff = document.getElementById('icon-eye-off');
    if (!input || !eyeOn || !eyeOff) return;

    if (input.type === 'password') {
        input.type = 'text';
        eyeOn.style.display = 'none';
        eyeOff.style.display = '';
    } else {
        input.type = 'password';
        eyeOn.style.display = '';
        eyeOff.style.display = 'none';
    }
}

// Bind event listener if needed, or keep it as a global function for inline onclick
window.togglePassword = togglePassword;
