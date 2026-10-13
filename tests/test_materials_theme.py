from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
STYLES = (ROOT / "static/site_motion.css").read_text(encoding="utf-8")


def test_material_investment_category_amounts_use_readable_mint_text():
    selector = "body:not(.login-page) .materials-main .investment-category-total > strong"
    match = re.search(
        rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}",
        STYLES,
    )

    assert match, "Missing dark-theme amount color override for Materials investment cards"
    assert "color: #83dfbb;" in match.group(1)


def test_material_investment_labels_keep_muted_light_theme_color():
    label_rule = STYLES.split(
        "body:not(.login-page) .investment-category-total span,",
        1,
    )[1].split("}", 1)[0]

    assert "color: #a4b7ad;" in label_rule
