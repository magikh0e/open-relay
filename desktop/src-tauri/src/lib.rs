use tauri::{WebviewUrl, WebviewWindowBuilder};

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
const DEFAULT_SERVER: &str = "https://openrelay.pl";

pub fn run() {
    tauri::Builder::default()
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
                .title("Open Relay")
                .inner_size(1100.0, 780.0)
                .min_inner_size(400.0, 560.0)
                .initialization_script(&init)
                .build()?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the Open Relay desktop app");
}
