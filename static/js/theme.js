(function () {
    // 1. Immediately apply the saved theme to prevent white flash
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);

    // 2. Wait for DOM content to load to inject the toggle button
    document.addEventListener('DOMContentLoaded', () => {
        const navbarActions = document.querySelector('.app-navbar-actions');
        if (!navbarActions) return;

        // Create toggle button
        const toggleBtn = document.createElement('button');
        toggleBtn.id = 'theme-toggle-btn';
        toggleBtn.setAttribute('title', 'Toggle Dark/Light Mode');
        toggleBtn.style.background = 'none';
        toggleBtn.style.border = 'none';
        toggleBtn.style.cursor = 'pointer';
        toggleBtn.style.fontSize = '1.3rem';
        toggleBtn.style.color = 'var(--text-secondary)';
        toggleBtn.style.padding = '8px';
        toggleBtn.style.borderRadius = '50%';
        toggleBtn.style.display = 'flex';
        toggleBtn.style.alignItems = 'center';
        toggleBtn.style.justifyContent = 'center';
        toggleBtn.style.transition = 'background-color 0.2s, color 0.2s';
        
        // Hover effects
        toggleBtn.addEventListener('mouseenter', () => {
            toggleBtn.style.backgroundColor = 'rgba(9, 30, 66, 0.08)';
            toggleBtn.style.color = 'var(--text-primary)';
        });
        toggleBtn.addEventListener('mouseleave', () => {
            toggleBtn.style.backgroundColor = 'transparent';
            toggleBtn.style.color = 'var(--text-secondary)';
        });

        // Set initial icon based on theme
        const icon = document.createElement('i');
        icon.className = document.documentElement.getAttribute('data-theme') === 'dark' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
        toggleBtn.appendChild(icon);

        // Click handler to toggle theme
        toggleBtn.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            icon.className = newTheme === 'dark' ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
        });

        // Prepend to actions
        navbarActions.insertBefore(toggleBtn, navbarActions.firstChild);
    });
})();
