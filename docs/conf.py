import os
import sys

project   = "Blendshape Editor Tool"
copyright = "2026"
author    = ""
release   = "v.03.003"

extensions = [
    "sphinx.ext.autosectionlabel",
]

templates_path   = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
    "titles_only": False,
}

html_static_path = ["_static"]
html_css_files   = ["custom.css"]
