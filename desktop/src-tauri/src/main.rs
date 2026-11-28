#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod cli;
mod commands;
mod navigation;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::dashboard_config,
            commands::tracker_status,
            commands::tracker_action,
            commands::set_api_key,
            commands::clear_api_key,
            commands::choose_project_folder,
            commands::link_project,
            commands::list_linked_projects,
            commands::unlink_project,
            commands::adapters_list,
            commands::adapters_install,
        ])
        .setup(|app| {
            let base = navigation::dashboard_base();
            let navigation_base = base.clone();
            let injected = Arc::new(AtomicBool::new(false));
            let injected_on_load = Arc::clone(&injected);
            // On macOS the menu is app-global (set on `app` below), so the
            // window handle is unused there; allow it to keep -D warnings clean.
            #[cfg_attr(target_os = "macos", allow(unused_variables))]
            let main = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(navigation::dashboard_page("/home")),
            )
            .title("StruggleTime Dashboard")
            .inner_size(1280.0, 860.0)
            .min_inner_size(900.0, 620.0)
            .on_navigation(
                move |url| match navigation::navigation_action(&navigation_base, url) {
                    navigation::NavigationAction::Allow => true,
                    navigation::NavigationAction::OpenExternal => {
                        let _ = open::that(url.to_string());
                        false
                    }
                    navigation::NavigationAction::Reject => false,
                },
            )
            .on_page_load(move |window, payload| {
                if injected_on_load.swap(true, Ordering::SeqCst) {
                    return;
                }
                let token = st_core::load_settings()
                    .map(|s| s.token)
                    .unwrap_or_default();
                if let Some(script) = navigation::token_bootstrap_script(
                    payload.url(),
                    &navigation::dashboard_base(),
                    token.trim(),
                ) {
                    let _ = window.eval(&script);
                }
            })
            .build()?;

            let dashboard = MenuItem::with_id(app, "dashboard", "Dashboard", true, None::<&str>)?;
            let settings = MenuItem::with_id(
                app,
                "tracker-settings",
                "Tracker Settings",
                true,
                None::<&str>,
            )?;
            let reload = MenuItem::with_id(app, "reload", "Reload Dashboard", true, None::<&str>)?;
            let browser = MenuItem::with_id(app, "browser", "Open in Browser", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&dashboard, &settings, &separator, &reload, &browser, &quit],
            )?;
            #[cfg(target_os = "macos")]
            app.set_menu(menu.clone())?;
            #[cfg(not(target_os = "macos"))]
            main.set_menu(menu)?;
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "dashboard" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.navigate(navigation::dashboard_page("/home"));
                    let _ = window.set_focus();
                }
            }
            "tracker-settings" => {
                let _ = commands::open_settings(app);
            }
            "reload" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.eval("location.reload()");
                }
            }
            "browser" => {
                let _ = open::that(navigation::dashboard_page("/home").to_string());
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error running StruggleTime Dashboard");
}
