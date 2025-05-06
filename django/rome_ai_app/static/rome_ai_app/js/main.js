document.addEventListener('DOMContentLoaded', function() {
    // Mobile menu toggle
    const mobileMenuToggle = document.querySelector('.mobile-menu-toggle');
    const mainNav = document.querySelector('.main-nav');

    if (mobileMenuToggle && mainNav) {
        mobileMenuToggle.addEventListener('click', function() {
            mainNav.classList.toggle('active');
            document.body.classList.toggle('menu-open');
        });
    }

    // Slider functionality
    const sliderControls = document.querySelector('.slider-controls');
    const slides = document.querySelectorAll('.research-slide');

    if (sliderControls && slides.length > 0) {
        const prevBtn = sliderControls.querySelector('.prev');
        const nextBtn = sliderControls.querySelector('.next');
        const dotsContainer = sliderControls.querySelector('.slider-dots');

        let currentSlide = 0;

        // Hide all slides except the first one
        slides.forEach((slide, index) => {
            if (index !== 0) slide.style.display = 'none';

            // Create a dot for each slide
            const dot = document.createElement('div');
            dot.classList.add('dot');
            if (index === 0) dot.classList.add('active');
            dotsContainer.appendChild(dot);

            // Add click event to dots
            dot.addEventListener('click', function() {
                showSlide(index);
            });
        });

        // Add click events to prev/next buttons
        if (prevBtn) {
            prevBtn.addEventListener('click', function() {
                showSlide(currentSlide - 1);
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', function() {
                showSlide(currentSlide + 1);
            });
        }

        function showSlide(index) {
            // Handle index overflow
            if (index < 0) index = slides.length - 1;
            if (index >= slides.length) index = 0;

            // Hide current slide and show the new one
            slides[currentSlide].style.display = 'none';
            slides[index].style.display = 'grid';

            // Update dots
            dotsContainer.querySelectorAll('.dot').forEach((dot, i) => {
                dot.classList.toggle('active', i === index);
            });

            currentSlide = index;
        }
    }
});
