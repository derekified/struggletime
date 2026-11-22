//! Rasterize struggletime.svg with colourscheme tokens.
use crate::theme::ThemePalette;
use anyhow::Result;

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub fn make_icon(theme: ThemePalette, tracking: bool, paused: bool) -> Result<tray_icon::Icon> {
    use crate::theme::icon_colors;
    use anyhow::Context;
    let (fg, bg) = icon_colors(theme, tracking, paused);
    let (rgba, w, h) = render_svg_rgba(fg, bg, 64)?;
    tray_icon::Icon::from_rgba(rgba, w, h).map_err(|e| anyhow::anyhow!(e))
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn render_svg_rgba(fg: &str, bg: &str, size: u32) -> Result<(Vec<u8>, u32, u32)> {
    use anyhow::Context;
    let raw = include_str!("../assets/struggletime.svg");
    let mut inner = raw.to_string();
    if let Some(idx) = inner.find('>') {
        inner = inner[idx + 1..].to_string();
    }
    if let Some(idx) = inner.rfind("</svg>") {
        inner = inner[..idx].to_string();
    }
    inner = inner.replace("currentColor", fg);
    let wrapped = format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<rect width="128" height="128" rx="28" fill="{bg}"/>
<g transform="translate(10 10) scale(0.84)" stroke="{fg}" fill="none" color="{fg}">
{inner}
</g></svg>"##
    );

    let mut opt = usvg::Options::default();
    opt.fontdb_mut().load_system_fonts();
    let tree = usvg::Tree::from_str(&wrapped, &opt).context("parse themed svg")?;
    let mut pixmap = tiny_skia::Pixmap::new(size, size).context("pixmap")?;
    let scale = size as f32 / 128.0;
    let transform = tiny_skia::Transform::from_scale(scale, scale);
    resvg::render(&tree, transform, &mut pixmap.as_mut());
    Ok((pixmap.take(), size, size))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub fn make_icon(_theme: ThemePalette, _tracking: bool, _paused: bool) -> Result<()> {
    Ok(())
}
