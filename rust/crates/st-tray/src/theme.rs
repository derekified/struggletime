//! Colourscheme tokens mirrored from server/static/styles.css.
#[derive(Clone, Copy, Debug)]
pub struct ThemePalette {
    pub id: &'static str,
    pub bg: &'static str,
    pub accent: &'static str,
    pub mut_fg: &'static str,
    pub trust_yellow: &'static str,
}

pub const CLASSIC: ThemePalette = ThemePalette {
    id: "classic",
    bg: "#1C1517",
    accent: "#EC3750",
    mut_fg: "#B5A4A8",
    trust_yellow: "#FF8C37",
};

const THEMES: &[ThemePalette] = &[
    CLASSIC,
    ThemePalette {
        id: "midnight",
        bg: "#141416",
        accent: "#EC3750",
        mut_fg: "#9E9EA8",
        trust_yellow: "#FACC15",
    },
    ThemePalette {
        id: "neon",
        bg: "#0D0F0D",
        accent: "#B8FF3B",
        mut_fg: "#96A396",
        trust_yellow: "#FCD34D",
    },
    ThemePalette {
        id: "catppuccin-mocha",
        bg: "#1E1E2E",
        accent: "#CBA6F7",
        mut_fg: "#A6ADC8",
        trust_yellow: "#FAB387",
    },
    ThemePalette {
        id: "catppuccin-iced-latte",
        bg: "#DCE0E8",
        accent: "#1E66F5",
        mut_fg: "#6C6F85",
        trust_yellow: "#DF8E1D",
    },
    ThemePalette {
        id: "gruvbox-dark",
        bg: "#282828",
        accent: "#D79921",
        mut_fg: "#A89984",
        trust_yellow: "#FABD2F",
    },
    ThemePalette {
        id: "github-dark",
        bg: "#0D1117",
        accent: "#1F6FEB",
        mut_fg: "#8B949E",
        trust_yellow: "#F78166",
    },
    ThemePalette {
        id: "github-light",
        bg: "#F6F8FA",
        accent: "#0969DA",
        mut_fg: "#656D76",
        trust_yellow: "#E16F24",
    },
    ThemePalette {
        id: "nord",
        bg: "#2E3440",
        accent: "#88C0D0",
        mut_fg: "#D8DEE9",
        trust_yellow: "#EBCB8B",
    },
    ThemePalette {
        id: "rose-pine",
        bg: "#191724",
        accent: "#EB6F92",
        mut_fg: "#908CAA",
        trust_yellow: "#F6C177",
    },
    ThemePalette {
        id: "rose-pine-dawn",
        bg: "#FAF4ED",
        accent: "#B4637A",
        mut_fg: "#797593",
        trust_yellow: "#EA9D34",
    },
    ThemePalette {
        id: "amoled",
        bg: "#000000",
        accent: "#00E5FF",
        mut_fg: "#8E8E93",
        trust_yellow: "#EAB308",
    },
];

pub fn resolve_theme(name: &str) -> ThemePalette {
    let n = name.trim().to_ascii_lowercase();
    let n = match n.as_str() {
        "catppuccin-latte" => "catppuccin-iced-latte",
        "lime" | "ember" | "ocean" | "forest" | "ink" | "obsidian" | "paper" | "crt" | "nordic"
        | "sand" => "classic",
        other => other,
    };
    THEMES
        .iter()
        .copied()
        .find(|t| t.id == n)
        .unwrap_or(CLASSIC)
}

/// Foreground / background for the tray glyph (tracking / paused / idle).
pub fn icon_colors(
    theme: ThemePalette,
    tracking: bool,
    paused: bool,
) -> (&'static str, &'static str) {
    let fg = if paused {
        theme.trust_yellow
    } else if tracking {
        theme.accent
    } else {
        theme.mut_fg
    };
    (fg, theme.bg)
}
