use std::time::Duration;

use tauri::{WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::UpdaterExt;

// The server the desktop app talks to when the user hasn't chosen one in-app.
//
// The bundled web UI is origin-agnostic: `frontend/src/config.js` resolves the
// backend from `window.__RELAY_SERVER__` first, then a stored `relay_server`,
// then same-origin. In a desktop webview there is no useful same-origin server,
// so the shell injects that global before the page loads. We seed it from the
// in-app picker's stored value when present, and fall back to this default —
// so switching servers is just "write localStorage + reload", no rebuild.
//
// Repoint the default build by changing this constant (or set RELAY_SERVER in
// the environment at launch to override it).
const DEFAULT_SERVER: &str = "https://chat.openrelay.pl";

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let default_server =
                std::env::var("RELAY_SERVER").unwrap_or_else(|_| DEFAULT_SERVER.to_string());

            // Runs in the page context before any app JS. serde_json::to_string
            // safely quotes the value so it can't break out of the string.
            let init = format!(
                "window.__RELAY_SERVER__ = localStorage.getItem('relay_server') || {};",
                serde_json::to_string(&default_server).unwrap()
            );

            WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title(concat!("Open Relay v", env!("CARGO_PKG_VERSION")))
                .inner_size(1100.0, 780.0)
                .min_inner_size(400.0, 560.0)
                .initialization_script(init.as_str())
                .build()?;

            // Check for a newer signed build shortly after launch (so the
            // window is up first), then once a day for long-running sessions.
            // A dedicated thread keeps the async check off the UI event loop.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(6));
                loop {
                    tauri::async_runtime::block_on(check_for_update(handle.clone()));
                    std::thread::sleep(Duration::from_secs(24 * 60 * 60));
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the Open Relay desktop app");
}

// Ask the GitHub release manifest whether a newer signed build exists. If so,
// prompt the user; on accept, download, install, and relaunch. Every failure
// path (offline, no update, declined) is silent by design, so a flaky network
// never nags the user with error dialogs.
async fn check_for_update(app: tauri::AppHandle) {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(_) => return,
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        _ => return,
    };

    let msg = format!(
        "Open Relay {} is available (you have {}). Install it and restart now?",
        update.version, update.current_version
    );
    let accepted = app
        .dialog()
        .message(msg)
        .title("Update available")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install".into(),
            "Later".into(),
        ))
        .blocking_show();

    if accepted && update.download_and_install(|_, _| {}, || {}).await.is_ok() {
        app.restart();
    }
}
