document.addEventListener("DOMContentLoaded", function () {

    // MOBILE NAVIGATION

    const menuButton = document.getElementById("mobileMenuBtn");
    const sidebar = document.querySelector(".sidebar");
    const overlay = document.getElementById("sidebarOverlay");

    function closeMenu() {
        if (!menuButton || !sidebar || !overlay) return;

        sidebar.classList.remove("open");
        overlay.classList.remove("show");
        menuButton.setAttribute("aria-expanded", "false");
        menuButton.innerHTML = "☰";
    }

    if (menuButton && sidebar && overlay) {

        menuButton.addEventListener("click", function () {

            const isOpen = sidebar.classList.contains("open");

            if (isOpen) {
                closeMenu();
            } else {
                sidebar.classList.add("open");
                overlay.classList.add("show");
                menuButton.setAttribute("aria-expanded", "true");
                menuButton.innerHTML = "✕";
            }

        });

        overlay.addEventListener("click", closeMenu);

        sidebar.querySelectorAll("a").forEach(function (link) {
            link.addEventListener("click", closeMenu);
        });
        // PROFILE PHOTO PREVIEW
const photoInput = document.getElementById("profilePhotoInput");
const profileImage = document.getElementById("profileImage");
const profileBtn = document.getElementById("profileBtn");

if (photoInput && profileImage && profileBtn) {

    photoInput.addEventListener("change", function () {

        const file = this.files[0];

        if (!file) return;

        if (!file.type.startsWith("image/")) {
            alert("Please select a valid image.");
            this.value = "";
            return;
        }

        if (file.size > 5 * 1024 * 1024) {
            alert("Please select an image smaller than 5 MB.");
            this.value = "";
            return;
        }

        const reader = new FileReader();

        reader.onload = function (event) {
            profileImage.src = event.target.result;
            profileImage.hidden = false;

            const svg = profileBtn.querySelector("svg");
            if (svg) {
                svg.style.display = "none";
            }
        };

        reader.readAsDataURL(file);
    });
}
    }


    // PROFILE DROPDOWN

    const profileBtn = document.getElementById("profileBtn");
    const profileDropdown = document.getElementById("profileDropdown");

    if (profileBtn && profileDropdown) {

        profileBtn.addEventListener("click", function (event) {

            event.stopPropagation();

            const isOpen = profileDropdown.classList.toggle("show");

            profileBtn.setAttribute("aria-expanded", isOpen);

        });

        document.addEventListener("click", function (event) {

            if (!event.target.closest(".profile-container")) {

                profileDropdown.classList.remove("show");
                profileBtn.setAttribute("aria-expanded", "false");

            }

        });

    }

});