//! Butler's desktop shell (docs/desktop-app.md D2/D3): one window on the
//! LOCAL listener, a tray menu, and three local pages. It starts or attaches
//! to the daemon through `scripts\launch.py` and never re-implements it.
//!
//! One CONTROLLER thread owns every slow step (finding Butler, launching,
//! waiting, polling, quitting) and takes the tray's and the pages' requests
//! from a channel, so two of them never race. The main thread only draws.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod daemon;
mod guard;
mod job;
mod logic;

use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_notification::NotificationExt;

use crate::job::Job;
use crate::logic::{
    Event, LaunchResult, Machine, NavAction, QuitAction, Status, LOCAL_PAGES_ORIGIN,
};

const MAIN: &str = "main";

/// The repo this binary was built from (AC-551's first place to look).
fn built_from() -> PathBuf {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    manifest.parent().and_then(Path::parent).expect("app/src-tauri sits two below the repo").to_path_buf()
}

enum Cmd {
    Restart,
    Quit,
}

/// What the local pages show (`page_info`).
#[derive(Clone, Default, serde::Serialize)]
struct PageInfo {
    repo: String,
    not_set_up: String,
    launcher_output: String,
    log_path: String,
    log_tail: String,
    note: String,
    /// Butler's origin, for the Phone link page's way back.
    butler: String,
}

struct TrayItems {
    status: MenuItem<Wry>,
    phone: MenuItem<Wry>,
    restart: MenuItem<Wry>,
}

struct Shared {
    port: u16,
    test_port: bool,
    origin: String,
    machine: Mutex<Machine>,
    page: Mutex<PageInfo>,
    tx: Mutex<Sender<Cmd>>,
    tray: OnceLock<TrayItems>,
    job: Job,
}

// ------------------------------------------------------------ page IPC

#[tauri::command]
fn page_info(shared: State<'_, Arc<Shared>>) -> PageInfo {
    shared.page.lock().unwrap().clone()
}

/// Restart (Stopped page) and Try again (Not set up page).
#[tauri::command]
fn restart_butler(shared: State<'_, Arc<Shared>>) -> Result<(), String> {
    shared.tx.lock().unwrap().send(Cmd::Restart).map_err(|e| e.to_string())
}

/// What `/api/phonelink` answered: its status and the daemon's pairing
/// document as text, rendered by `ui/phonelink.js`.
#[derive(serde::Serialize)]
struct PhoneAnswer {
    status: u16,
    body: String,
}

/// The Phone link page (DA-15, DA-16): read, or set up. Asked on the LOCAL
/// listener from Rust, so the page needs no network permission, and off the
/// main thread, because set-up runs `tailscale serve`.
#[tauri::command]
async fn phone_link(shared: State<'_, Arc<Shared>>, setup: bool) -> Result<PhoneAnswer, String> {
    let port = shared.port;
    tauri::async_runtime::spawn_blocking(move || daemon::phone_link(port, setup))
        .await
        .map_err(|e| e.to_string())?
        .map(|(status, body)| PhoneAnswer { status, body })
}

// ------------------------------------------------------------ drawing

fn show_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN) {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn navigate(app: &AppHandle, url: &str) {
    let w = app.get_webview_window(MAIN).expect("the main window exists");
    w.navigate(url.parse().expect("a valid URL")).expect("navigate the main window");
}

fn local_page(name: &str) -> String {
    format!("{LOCAL_PAGES_ORIGIN}/{name}")
}

/// Redraw the tray and the window for a NEW status.
fn draw(app: &AppHandle, shared: &Shared, status: Status) {
    if let Some(t) = shared.tray.get() {
        let _ = t.status.set_text(status.label());
        let _ = t.phone.set_enabled(status.can_link_phone());
        let _ = t.restart.set_enabled(status != Status::Starting);
    }
    match status {
        Status::Running => navigate(app, &format!("{}/", shared.origin)),
        Status::Starting => navigate(app, &local_page("starting.html")),
        Status::NotSetUp => navigate(app, &local_page("notsetup.html")),
        Status::Stopped => {
            refresh_log_tail(shared);
            navigate(app, &local_page("stopped.html"));
        }
    }
}

fn apply(app: &AppHandle, shared: &Shared, ev: Event) {
    let changed = shared.machine.lock().unwrap().on(ev);
    if let Some(status) = changed {
        draw(app, shared, status);
    }
}

fn refresh_log_tail(shared: &Shared) {
    let mut page = shared.page.lock().unwrap();
    if page.log_path.is_empty() {
        return;
    }
    page.log_tail = match std::fs::read(&page.log_path) {
        Ok(bytes) => logic::tail(&String::from_utf8_lossy(&bytes), logic::LOG_TAIL_LINES),
        Err(e) => format!("(could not read {}: {e})", page.log_path),
    };
}

// ------------------------------------------------------------ controller

fn find_repo(app: &AppHandle) -> Result<PathBuf, String> {
    let saved = config::load()?;
    let profile = std::env::var_os("USERPROFILE").map(PathBuf::from);
    let built = built_from();
    let list = logic::candidates(Some(&built), profile.as_deref());
    if let Some(found) = logic::find_checkout(saved.as_deref(), &list) {
        if saved.as_deref() != Some(found.as_path()) {
            config::save(&found)?;
        }
        return Ok(found);
    }
    // Asked ONCE (AC-551); a wrong answer is refused naming the folder.
    let picked = app
        .dialog()
        .file()
        .set_title("Where is Butler?")
        .blocking_pick_folder()
        .ok_or("No Butler folder was chosen.")?
        .into_path()
        .map_err(|e| e.to_string())?;
    let repo = logic::validate_choice(&picked)?;
    config::save(&repo)?;
    Ok(repo)
}

/// Wait up to `limit` for `done`, polling every `step`; a Quit arriving
/// meanwhile is handed back so it is never lost.
fn wait_for(rx: &Receiver<Cmd>, limit: Duration, step: Duration, done: impl Fn() -> bool)
    -> (bool, Option<Cmd>) {
    let start = Instant::now();
    loop {
        if done() {
            return (true, None);
        }
        if start.elapsed() >= limit {
            return (false, None);
        }
        match rx.recv_timeout(step) {
            Ok(Cmd::Quit) => return (false, Some(Cmd::Quit)),
            Ok(Cmd::Restart) | Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => return (false, None),
        }
    }
}

/// DA-8, AC-544: attach to a healthy Butler, else start one and wait.
fn start_or_attach(app: &AppHandle, shared: &Shared, repo: &Path, rx: &Receiver<Cmd>, force: bool)
    -> Option<Cmd> {
    if !force && daemon::healthy(shared.port) {
        apply(app, shared, Event::Healthy);
        return None;
    }
    {
        let mut page = shared.page.lock().unwrap();
        page.launcher_output.clear();
        page.note.clear();
    }
    if shared.test_port {
        shared.page.lock().unwrap().note = format!(
            "BUTLER_APP_PORT is set to {} (a test run), so this app does not start Butler: \
             launch.py always starts it on 8141 and clears 8140-8142.",
            shared.port
        );
        apply(app, shared, Event::LaunchFailed);
        return None;
    }
    apply(app, shared, Event::LaunchBegun);
    match daemon::launch(repo, &shared.job) {
        LaunchResult::NotSetUp { message } => {
            shared.page.lock().unwrap().not_set_up = message;
            apply(app, shared, Event::LaunchRefused);
            None
        }
        LaunchResult::Failed { output } => {
            shared.page.lock().unwrap().launcher_output = output;
            apply(app, shared, Event::LaunchFailed);
            None
        }
        LaunchResult::Started { .. } => {
            let limit = Duration::from_secs(logic::START_TIMEOUT_SECS);
            let port = shared.port;
            let (ok, cmd) = wait_for(rx, limit, Duration::from_secs(1), || daemon::healthy(port));
            if cmd.is_some() {
                return cmd;
            }
            apply(app, shared, if ok { Event::Healthy } else { Event::StartTimedOut });
            None
        }
    }
}

fn own_alive(shared: &Shared) -> bool {
    shared.job.active().expect("query the app's job object") > 0
}

/// Ask Butler to stop and wait up to 15 s; end the app's own processes if
/// they outlive that (DA-11). Shared by Quit and Restart.
fn stop(shared: &Shared, rx: &Receiver<Cmd>) -> QuitAction {
    let port = shared.port;
    if !logic::quit_needed(daemon::healthy(port), own_alive(shared)) {
        return QuitAction::NotRunning;
    }
    // An error here means nothing answered; the wait below sees the truth.
    let _ = daemon::post_shutdown(port);
    let limit = Duration::from_secs(logic::QUIT_TIMEOUT_SECS);
    let _ = wait_for(rx, limit, Duration::from_millis(500), || {
        logic::after_wait(daemon::healthy(port), own_alive(shared)) == QuitAction::Done
    });
    let action = logic::after_wait(daemon::healthy(port), own_alive(shared));
    if action == QuitAction::KillOwn {
        shared.job.terminate().expect("end the app's job object");
    }
    action
}

fn quit(app: &AppHandle, shared: &Shared, rx: &Receiver<Cmd>) {
    let action = stop(shared, rx);
    tell(app, logic::quit_message(action));
    app.exit(0);
}

fn controller(app: AppHandle, shared: Arc<Shared>, rx: Receiver<Cmd>, guard_ready: Receiver<Result<(), String>>) {
    let fail = |msg: String| {
        app.dialog().message(msg).title("Butler").kind(MessageDialogKind::Error).blocking_show();
        app.exit(1);
    };
    match guard_ready.recv() {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return fail(format!("The microphone gate could not be installed: {e}")),
        Err(_) => return fail("The microphone gate never reported back.".into()),
    }
    let repo = match find_repo(&app) {
        Ok(r) => r,
        Err(e) => return fail(e),
    };
    {
        let mut page = shared.page.lock().unwrap();
        page.repo = repo.display().to_string();
        page.log_path = match daemon::log_path(&repo) {
            Ok(p) => p.display().to_string(),
            Err(e) => {
                page.log_tail = e;
                String::new()
            }
        };
    }
    let mut pending = start_or_attach(&app, &shared, &repo, &rx, false);
    loop {
        let cmd = match pending.take() {
            Some(c) => Ok(c),
            None => rx.recv_timeout(Duration::from_secs(logic::POLL_SECS)),
        };
        match cmd {
            Ok(Cmd::Quit) => return quit(&app, &shared, &rx),
            Ok(Cmd::Restart) => {
                apply(&app, &shared, Event::LaunchBegun);
                stop(&shared, &rx);
                pending = start_or_attach(&app, &shared, &repo, &rx, true);
            }
            Err(RecvTimeoutError::Timeout) => {
                let ev = if daemon::healthy(shared.port) { Event::Healthy } else { Event::Unhealthy };
                apply(&app, &shared, ev);
            }
            Err(RecvTimeoutError::Disconnected) => return,
        }
    }
}

// ------------------------------------------------------------ setup

/// A failure the user must see: a release build has no console.
fn tell(app: &AppHandle, text: String) {
    let _ = app.notification().builder().title("Butler").body(text).show();
}

fn open_in_browser(app: &AppHandle, url: &str) {
    if let Err(e) = tauri_plugin_opener::open_url(url, None::<&str>) {
        tell(app, format!("Could not open {url} in the browser: {e}"));
    }
}

fn build_tray(app: &AppHandle, shared: &Arc<Shared>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Butler", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "status", Status::Starting.label(), false, None::<&str>)?;
    let enabled = app.autolaunch().is_enabled().expect("read the autostart setting");
    let autostart =
        CheckMenuItem::with_id(app, "autostart", "Start with Windows", true, enabled, None::<&str>)?;
    let phone = MenuItem::with_id(app, "phone", "Phone link", false, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart Butler", false, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Butler", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[
        &open,
        &status,
        &PredefinedMenuItem::separator(app)?,
        &autostart,
        &phone,
        &PredefinedMenuItem::separator(app)?,
        &restart,
        &quit,
    ])?;
    let _ = shared.tray.set(TrayItems { status, phone, restart });
    let shared = shared.clone();
    TrayIconBuilder::with_id("butler")
        .icon(app.default_window_icon().expect("the bundle icon").clone())
        .tooltip("Butler")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_window(tray.app_handle());
            }
        })
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => show_window(app),
            "phone" => {
                navigate(app, &local_page("phonelink.html"));
                show_window(app);
            }
            "autostart" => {
                let al = app.autolaunch();
                let on = al.is_enabled().expect("read the autostart setting");
                let res = if on { al.disable() } else { al.enable() };
                if let Err(e) = res {
                    tell(app, format!("Could not change Start with Windows: {e}"));
                }
                let now = al.is_enabled().expect("read the autostart setting");
                let _ = autostart.set_checked(now);
            }
            "restart" => {
                let _ = shared.tx.lock().unwrap().send(Cmd::Restart);
            }
            "quit" => {
                let _ = shared.tx.lock().unwrap().send(Cmd::Quit);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn main() {
    let (port, test_port) =
        logic::app_port(std::env::var("BUTLER_APP_PORT").ok().as_deref()).unwrap_or_else(|e| panic!("{e}"));
    let autostarted = std::env::args().any(|a| a == "--autostart");
    let (tx, rx) = mpsc::channel();
    let shared = Arc::new(Shared {
        port,
        test_port,
        origin: logic::butler_origin(port),
        machine: Mutex::new(Machine::new()),
        page: Mutex::new(PageInfo { butler: logic::butler_origin(port), ..PageInfo::default() }),
        tx: Mutex::new(tx),
        tray: OnceLock::new(),
        job: Job::new().expect("create the app's job object"),
    });

    let app = tauri::Builder::default()
        // FIRST, so a second launch never gets as far as a window (DA-7).
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_window(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--autostart"])))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(shared.clone())
        .invoke_handler(tauri::generate_handler![page_info, restart_butler, phone_link])
        .setup(move |app| {
            let handle = app.handle().clone();
            build_tray(&handle, &shared)?;
            let butler = shared.origin.clone();
            let (nav_app, popup_app) = (handle.clone(), handle.clone());
            let window = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("starting.html".into()))
                .title("Butler")
                .inner_size(1180.0, 820.0)
                .min_inner_size(420.0, 360.0)
                // DA-12: started at login, it stays in the tray.
                .visible(!autostarted)
                .on_navigation(move |url| match logic::navigation(url.as_str(), &butler) {
                    NavAction::Stay => true,
                    NavAction::OpenInBrowser => {
                        open_in_browser(&nav_app, url.as_str());
                        false
                    }
                    NavAction::Block => false,
                })
                .on_new_window(move |url, _features| {
                    if logic::new_window(url.as_str()) == NavAction::OpenInBrowser {
                        open_in_browser(&popup_app, url.as_str());
                    }
                    NewWindowResponse::Deny
                })
                .build()?;
            // DA-10: closing hides; only Quit stops Butler.
            let w = window.clone();
            window.on_window_event(move |e| {
                if let WindowEvent::CloseRequested { api, .. } = e {
                    api.prevent_close();
                    let _ = w.hide();
                }
            });
            let (ready_tx, ready_rx) = mpsc::channel();
            let allowed = shared.origin.clone();
            window.with_webview(move |pw| {
                let r = guard::install(&pw.controller(), allowed).map_err(|e| e.to_string());
                let _ = ready_tx.send(r);
            })?;
            let shared = shared.clone();
            std::thread::spawn(move || controller(handle, shared, rx, ready_rx));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("build the Butler app");

    app.run(|_app, event| {
        // Only an explicit exit (Quit, a fatal error) ends the app; the
        // last window hiding does not.
        if let RunEvent::ExitRequested { api, code: None, .. } = event {
            api.prevent_exit();
        }
    });
}
