from pathlib import Path

from flask import render_template

from app import app


def render_navigation(user):
    with app.test_request_context("/"):
        return render_template("_navigation.html", current_user=user)


def test_navigation_reconstructs_admin_menu_destinations():
    html = render_navigation({
        "username": "admin",
        "full_name": "Admin User",
        "role": "super_admin",
    })

    for destination in (
        'href="/"',
        'href="/daily-reports"',
        'href="/red-book"',
        'href="/trade-builder"',
        'href="/materials"',
        'href="/drawings"',
        'href="/labour-payments"',
        'href="/owner-scope"',
        'href="/building-cost-summary"',
        'href="/ledger-prints"',
        'href="/user-management"',
        'href="/forgot-password"',
        'href="/snapshots"',
        'href="/google-photos"',
        'href="/profile-settings"',
        'href="/change-password"',
        'href="/user-guide"',
        'href="/developer-guide"',
        'action="/logout"',
    ):
        assert destination in html

    assert 'aria-label="Main navigation"' in html
    assert 'aria-controls="mm-nav-list"' in html
    assert "<strong>MANNAT MOON</strong>" in html
    assert "<small>Construction Documentation</small>" in html
    assert 'id="mm-icon-dashboard"' in html
    assert 'id="mm-icon-construction"' in html
    assert 'id="mm-icon-logout"' in html
    assert 'class="mm-menu-icon"' in html
    assert 'class="mm-nav-profile-trigger"' in html
    assert 'aria-controls="mm-nav-account-menu"' in html
    assert 'class="mm-nav-account-menu" id="mm-nav-account-menu" hidden' in html
    assert 'class="mm-nav-menu-link" href="/profile-settings"' in html
    assert 'class="mm-nav-menu-link" href="/change-password"' in html
    tools_item = html.split('aria-controls="mm-nav-tools"', 1)[0].rsplit("<li", 1)[1]
    assert 'class="mm-nav-item mm-nav-parent" hidden' in tools_item
    assert 'id="mm-nav-tools"' in html
    assert 'href="/google-photos"' in html
    assert 'class="mm-nav-avatar"' in html
    assert 'data-animal-avatar="cute_cat"' in html
    assert 'type="module"' in html
    assert "/static/mm_navigation.js" in html


def test_navigation_hides_super_admin_destinations_for_other_roles():
    html = render_navigation({
        "username": "staff",
        "full_name": "Office Staff",
        "role": "office_staff",
    })

    assert 'href="/user-management"' not in html
    assert 'href="/snapshots"' not in html
    assert 'href="/developer-guide"' not in html
    assert 'href="/trade-builder"' not in html
    assert 'href="/daily-reports"' in html
    assert 'action="/logout"' in html
    assert 'class="mm-nav-menu-link" href="/profile-settings"' in html
    assert 'class="mm-nav-menu-link" href="/change-password"' in html


def test_authenticated_pages_share_navigation_partial_and_styles():
    templates_dir = Path(__file__).resolve().parents[1] / "templates"
    template_names = (
        "index.html",
        "dashboard.html",
        "profile_settings.html",
        "daily_reports.html",
        "red_book.html",
        "materials.html",
        "drawings.html",
        "owner_scope.html",
        "building_cost_summary.html",
        "ledger_prints.html",
        "google_photos.html",
        "user_management.html",
        "user_details.html",
        "forgot_password.html",
        "snapshots.html",
        "change_password.html",
        "user_guide.html",
        "developer_guide.html",
        "404.html",
    )

    for template_name in template_names:
        source = (templates_dir / template_name).read_text(encoding="utf-8")
        assert "_navigation.html" in source, template_name
        assert "mm_navigation.css" in source, template_name

    brand_source = (templates_dir / "_navigation.html").read_text(encoding="utf-8")
    assert 'include "_brand.html"' in brand_source

    for template_name in ("password_reset_request.html", "reset_password.html", "super_admin_recovery.html", "daily_reports_viewer.html"):
        source = (templates_dir / template_name).read_text(encoding="utf-8")
        assert "_brand.html" in source, template_name
        assert "mm_navigation.css" in source, template_name

    for path in templates_dir.glob("*.html"):
        if path.name.startswith("_") or path.name in {"labour_payments.html", "login.html"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert "_navigation.html" in source or "_brand.html" in source, path.name

    labour_template = (templates_dir / "labour_payments.html").read_text(encoding="utf-8")
    assert 'include "index.html"' in labour_template


def test_mobile_navigation_has_accessible_open_close_behavior():
    css = (Path(__file__).resolve().parents[1] / "static" / "mm_navigation.css").read_text(encoding="utf-8")
    javascript = (Path(__file__).resolve().parents[1] / "static" / "mm_navigation.js").read_text(encoding="utf-8")

    assert ".mm-nav-mobile-open > .mm-nav-list { display: grid;" in css
    assert "@media (max-width: 620px)" in css
    assert "width: calc(100vw - 24px);" in css
    assert "transform: translateX(-50%);" in css
    assert ".mm-nav-account { position: static; }" in css
    assert "right: 12px;" in css
    assert "left: 12px;" in css
    assert "color: #f0fff7;" in css
    assert "background: transparent;" in css
    assert ".mm-nav-submenu a:focus-visible" in css
    assert "outline: 2px solid #83e5c1;" in css
    assert ".mm-menu-icon" in css
    assert "@keyframes mm-nav-reveal" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert 'event.key !== "Escape"' in javascript
    assert 'setAttribute("aria-expanded", String(shouldOpen))' in javascript


def test_page_titles_follow_the_shared_header_and_brand_stays_left_aligned():
    root = Path(__file__).resolve().parents[1]
    templates_dir = root / "templates"
    page_templates = (
        "building_cost_summary.html",
        "owner_scope.html",
        "drawings.html",
        "materials.html",
        "ledger_prints.html",
        "index.html",
        "user_guide.html",
        "developer_guide.html",
        "google_photos.html",
    )

    for template_name in page_templates:
        source = (templates_dir / template_name).read_text(encoding="utf-8")
        header = source.split("</header>", 1)[0]
        assert "<h1" not in header and "<h2" not in header, template_name
        assert 'class="mm-page-heading"' in source, template_name

    navigation_css = (root / "static" / "mm_navigation.css").read_text(encoding="utf-8")
    navigation_template = (templates_dir / "_navigation.html").read_text(encoding="utf-8")
    assert "header.mm-app-header" in navigation_css
    assert "position: sticky;" in navigation_css
    assert "justify-content: flex-start;" in navigation_css
    assert ".mm-page-heading" in navigation_css
    assert navigation_template.index('include "_brand.html"') < navigation_template.index('<ul class="mm-nav-list"')


def test_daily_record_action_is_in_the_daily_reports_hero():
    root = Path(__file__).resolve().parents[1]
    page = (root / "templates" / "daily_reports.html").read_text(encoding="utf-8")
    header = page.split("</header>", 1)[0]
    hero = page.split('<section class="hero">', 1)[1].split("</section>", 1)[0]
    stylesheet = (root / "static" / "daily_reports.css").read_text(encoding="utf-8")

    assert 'id="dr-addBtn"' not in header
    assert 'id="dr-addBtn"' in hero
    assert "grid-template-columns: minmax(0, 1fr) auto auto;" in stylesheet
    assert "grid-column: 1;" in stylesheet


def test_shared_navigation_renders_animals_and_has_account_menu_behavior():
    root = Path(__file__).resolve().parents[1]
    script = (root / "static" / "mm_navigation.js").read_text(encoding="utf-8")
    stylesheet = (root / "static" / "mm_navigation.css").read_text(encoding="utf-8")
    template = (root / "templates" / "_navigation.html").read_text(encoding="utf-8")

    assert 'from "./animal_avatars.js"' in script
    assert 'document.querySelectorAll("[data-animal-avatar]")' in script
    assert 'querySelector(".mm-nav-profile-trigger")' in script
    assert 'querySelector(".mm-nav-account-menu")' in script
    assert 'event.key !== "Escape"' in script
    assert ".mm-nav-account-menu[hidden] { display: none; }" in stylesheet
    assert 'class="mm-nav-button{% if nav_endpoint in settings_endpoints %} is-active{% endif %}"' not in template
    assert '<span>Settings</span>' not in template
    assert 'url_for(\'change_password\')' in template
