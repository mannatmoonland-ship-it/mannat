from app import app


def test_login_password_field_has_accessible_non_submit_visibility_toggle():
    response = app.test_client().get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<form method="post" action="/login">' in html
    assert '<button type="submit">Sign In</button>' in html
    assert 'addEventListener("submit"' not in html
    assert "preventDefault()" not in html
    assert '<input id="password" name="password" type="password" required>' in html
    assert '<button class="password-toggle" id="passwordToggle" type="button"' in html
    assert 'aria-label="Show password"' in html
    assert 'title="Show password"' in html
    assert 'passwordInput.type = isVisible ? "text" : "password";' in html
    assert 'passwordToggle.setAttribute("aria-label", label);' in html
    assert "passwordInput.value" not in html
    assert "passwordInput.form" not in html


def test_login_page_shows_large_animated_mannat_moon_brand_panel():
    response = app.test_client().get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<h2>MANNAT<br>MOON</h2>' in html
    assert "Construction Documentation" in html
    assert 'class="login-scene"' in html
    assert 'class="scene-crane"' in html
    assert "@keyframes scene-draw" in html
    assert "@keyframes crane-sway" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
