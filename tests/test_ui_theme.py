import unittest

from app.ui.theme import COLORS, DEFAULT_COLORS, STATE_VISUALS, apply_ui_theme, blend


class UIThemeTests(unittest.TestCase):
    def setUp(self) -> None:
        apply_ui_theme({})

    def tearDown(self) -> None:
        apply_ui_theme({})

    def test_alias_keys_apply_to_glass_and_skeuo_colors(self) -> None:
        apply_ui_theme(
            {
                "visual_system": {
                    "frost_tint": "#112233",
                    "frost_edge": "#445566",
                    "skeuomorphic_highlight": "#778899",
                    "skeuomorphic_shadow": "#AABBCC",
                }
            }
        )
        self.assertEqual("#112233", COLORS["glass_tint"])
        self.assertEqual("#445566", COLORS["glass_edge"])
        self.assertEqual("#778899", COLORS["skeuo_highlight"])
        self.assertEqual("#AABBCC", COLORS["skeuo_shadow"])

    def test_missing_glass_colors_are_derived(self) -> None:
        panel_elevated = "#203040"
        accent_secondary = "#507090"
        accent_primary = "#406080"
        apply_ui_theme(
            {
                "visual_system": {
                    "panel": "#102030",
                    "panel_elevated": panel_elevated,
                    "accent_primary": accent_primary,
                    "accent_secondary": accent_secondary,
                }
            }
        )
        self.assertEqual(blend(panel_elevated, accent_secondary, 0.18), COLORS["glass_tint"])
        self.assertEqual(blend(accent_primary, "#FFFFFF", 0.32), COLORS["glass_edge"])

    def test_state_visual_overrides_are_applied(self) -> None:
        apply_ui_theme(
            {
                "state_visuals": {
                    "normal": {
                        "accent": "#010203",
                        "glow": "#111213",
                        "ring_speed": 0.91,
                        "pulse_speed": 0.33,
                        "label": "steady",
                    }
                }
            }
        )
        normal = STATE_VISUALS["NORMAL"]
        self.assertEqual("#010203", normal.accent)
        self.assertEqual("#111213", normal.glow)
        self.assertEqual(0.91, normal.ring_speed)
        self.assertEqual(0.33, normal.pulse_speed)
        self.assertEqual("STEADY", normal.label)

    def test_theme_resets_between_calls(self) -> None:
        apply_ui_theme({"visual_system": {"background": "#010203"}})
        self.assertEqual("#010203", COLORS["background"])
        apply_ui_theme({})
        self.assertEqual(DEFAULT_COLORS["background"], COLORS["background"])


if __name__ == "__main__":
    unittest.main()
