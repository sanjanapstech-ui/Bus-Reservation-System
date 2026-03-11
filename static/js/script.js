// Initialize QR code scanner
function initQRScanner() {
    const html5QrcodeScanner = new Html5QrcodeScanner(
        "qr-reader", { fps: 10, qrbox: 250 });
    
    html5QrcodeScanner.render((decodedText, decodedResult) => {
        // Handle the scanned code
        handleQRCode(decodedText);
    });
}

// Handle QR code scan
function handleQRCode(code) {
    // Send the scanned code to the server
    fetch('/scan-qr', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ bus_number: code })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification('Fare deducted successfully!', 'success');
        } else {
            showNotification(data.message || 'Error processing QR code', 'error');
        }
    })
    .catch(error => {
        showNotification('Error processing QR code', 'error');
    });
}

// Show notification
function showNotification(message, type) {
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show`;
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    document.body.appendChild(notification);
    
    // Remove notification after 5 seconds
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

// Handle notification responses
function handleNotificationResponse(notificationId, response) {
    const form = document.getElementById('notification-response-form');
    form.notification_id.value = notificationId;
    form.response.value = response;
    form.submit();
}

// Toggle password visibility
function togglePasswordVisibility(inputId, iconId) {
    const input = document.getElementById(inputId);
    const icon = document.getElementById(iconId);
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    }
}


function validateRegistrationForm() {
    const form = document.getElementById('registration-form');
    if (!form) return true;
    
    const usn = form.usn.value.trim();
    const name = form.name.value.trim();
    const password = form.password.value;
    const confirmPassword = form.confirm_password.value;
    const phone = form.phone.value.trim();
    const email = form.email.value.trim();
    
    
    const usnRegex = /^[0-9A-Z]{10}$/;
    if (!usnRegex.test(usn)) {
        showNotification('USN should be 10 characters, alphanumeric format.', 'error');
        return false;
    }
    
    

    if (password.length < 6) {
        showNotification('Password should be at least 6 characters.', 'error');
        return false;
    }
    
   
    if (password !== confirmPassword) {
        showNotification('Passwords do not match.', 'error');
        return false;
    }
    
    
    const phoneRegex = /^[0-9]{10}$/;
    if (!phoneRegex.test(phone)) {
        showNotification('Please enter a valid 10-digit phone number.', 'error');
        return false;
    }
    
  
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
        showNotification('Please enter a valid email address.', 'error');
        return false;
    }
    
    return true;
}

function validateTopUpForm() {
    const form = document.getElementById('topup-form');
    if (!form) return true;
    
    const amount = parseFloat(form.amount.value);
    
    if (isNaN(amount) || amount <= 0) {
        showNotification('Please enter a valid amount greater than 0.', 'error');
        return false;
    }
    
    return true;
}


document.addEventListener('DOMContentLoaded', function() {
   
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
   
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    var popoverList = popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
    
   
    if (document.getElementById('qr-reader')) {
        initQRScanner();
    }
}); 