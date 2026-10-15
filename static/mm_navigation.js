import { renderAnimalAvatar } from "./animal_avatars.js";

document.querySelectorAll("[data-animal-avatar]").forEach(avatar => {
    avatar.innerHTML = renderAnimalAvatar(avatar.dataset.animalAvatar);
});

(() => {
    const supportsNavigationHover = window.matchMedia(
        "(hover: hover) and (pointer: fine) and (min-width: 1281px)"
    ).matches;
    const navs = document.querySelectorAll(".mm-top-nav");

    navs.forEach(nav => {
        const mobileToggle = nav.querySelector(".mm-nav-mobile-toggle");
        const parents = [...nav.querySelectorAll(".mm-nav-parent")];
        const closeDropdowns = except => {
            parents.forEach(parent => {
                if (parent === except) return;
                parent.classList.remove("mm-nav-open");
                parent.querySelector(".mm-nav-button")?.setAttribute("aria-expanded", "false");
            });
        };

        parents.forEach(parent => {
            const button = parent.querySelector(".mm-nav-button");
            if (!button) return;
            let closeTimer;

            if (supportsNavigationHover) {
                parent.addEventListener("pointerenter", () => {
                    window.clearTimeout(closeTimer);
                    closeDropdowns(parent);
                    parent.classList.add("mm-nav-open");
                    button.setAttribute("aria-expanded", "true");
                });
                parent.addEventListener("pointerleave", () => {
                    closeTimer = window.setTimeout(() => {
                        parent.classList.remove("mm-nav-open");
                        button.setAttribute("aria-expanded", "false");
                    }, 150);
                });
            }

            button.addEventListener("click", event => {
                event.stopPropagation();
                if (supportsNavigationHover) {
                    closeDropdowns(parent);
                    parent.classList.add("mm-nav-open");
                    button.setAttribute("aria-expanded", "true");
                    return;
                }
                const shouldOpen = !parent.classList.contains("mm-nav-open");
                closeDropdowns(parent);
                parent.classList.toggle("mm-nav-open", shouldOpen);
                button.setAttribute("aria-expanded", String(shouldOpen));
            });
        });

        mobileToggle?.addEventListener("click", () => {
            const shouldOpen = mobileToggle.getAttribute("aria-expanded") !== "true";
            mobileToggle.setAttribute("aria-expanded", String(shouldOpen));
            nav.classList.toggle("mm-nav-mobile-open", shouldOpen);
            if (!shouldOpen) closeDropdowns(null);
        });

        document.addEventListener("click", event => {
            if (!nav.contains(event.target)) {
                closeDropdowns(null);
                nav.classList.remove("mm-nav-mobile-open");
                mobileToggle?.setAttribute("aria-expanded", "false");
            }
        });

        nav.addEventListener("keydown", event => {
            if (event.key !== "Escape") return;
            closeDropdowns(null);
            nav.classList.remove("mm-nav-mobile-open");
            mobileToggle?.setAttribute("aria-expanded", "false");
            nav.querySelector(".mm-nav-mobile-toggle")?.focus();
        });

        nav.querySelectorAll(".mm-nav-submenu a").forEach(link => {
            link.addEventListener("click", () => {
                closeDropdowns(null);
                nav.classList.remove("mm-nav-mobile-open");
                mobileToggle?.setAttribute("aria-expanded", "false");
            });
        });
    });

    document.querySelectorAll(".mm-nav-account").forEach(account => {
        const trigger = account.querySelector(".mm-nav-profile-trigger");
        const menu = account.querySelector(".mm-nav-account-menu");
        if (!trigger || !menu) return;

        const setOpen = shouldOpen => {
            trigger.setAttribute("aria-expanded", String(shouldOpen));
            menu.hidden = !shouldOpen;
        };

        let closeTimer;
        if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
            account.addEventListener("pointerenter", () => {
                window.clearTimeout(closeTimer);
                setOpen(true);
            });
            account.addEventListener("pointerleave", () => {
                closeTimer = window.setTimeout(() => setOpen(false), 150);
            });
        }

        trigger.addEventListener("click", event => {
            event.stopPropagation();
            if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
                setOpen(true);
                return;
            }
            setOpen(trigger.getAttribute("aria-expanded") !== "true");
        });

        account.addEventListener("keydown", event => {
            if (event.key !== "Escape" || menu.hidden) return;
            setOpen(false);
            trigger.focus();
        });

        document.addEventListener("click", event => {
            if (!account.contains(event.target)) setOpen(false);
        });

        menu.querySelectorAll("a").forEach(link => {
            link.addEventListener("click", () => setOpen(false));
        });
    });
})();
