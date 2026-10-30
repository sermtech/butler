//! The desktop shell's DECISIONS, with no I/O (docs/desktop-app.md 4.2).
//!
//! Everything the app decides -- which permission to grant, where a link
//! goes, which folder is Butler, what the status is, what Quit does when the
//! daemon hangs, what the launcher's output means -- is a plain function
//! here, so `cargo test` proves it without a window, a daemon or a network.
//! `main.rs` and `daemon.rs` only carry the results out.

use std::path::{Path, PathBuf};

/// The LOCAL listener's port (DA-5). `BUTLER_APP_PORT` overrides it for a
/// scratch daemon in a manual test only.
pub const DEFAULT_PORT: u16 = 8141;

/// Where Tauri serves the app's own pages on Windows (`app/ui`).
pub const LOCAL_PAGES_ORIGIN: &str = "http://tauri.localhost";

/// How long a launch may take to answer health (DA-8).
pub const START_TIMEOUT_SECS: u64 = 60;

/// How long Quit waits for the daemon to stop before killing it (DA-11).
pub const QUIT_TIMEOUT_SECS: u64 = 15;

/// Seconds between health polls while Butler is up (the tray status line).
pub const POLL_SECS: u64 = 5;

/// Consecutive failed polls before a running Butler counts as stopped. One
/// miss is not enough: `POST /api/restart` re-executes the daemon in place,
/// and the page reconnects by itself; swapping it for the Stopped page on a
/// single slow answer would interrupt exactly that.
pub const RUNNING_MISSES: u32 = 3;

/// Lines of `butler.log` the Stopped page shows (section 4.3).
pub const LOG_TAIL_LINES: usize = 20;

pub fn butler_origin(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// The origin (`scheme://host[:port]`) of an absolute http(s) URL, or None
/// for anything else. Strict on purpose: it is the input to the microphone
/// grant, so `http://127.0.0.1:8141@evil.example/` and
/// `http://127.0.0.1:8141.evil.example/` must not look like Butler.
pub fn origin_of(url: &str) -> Option<String> {
    let (scheme, rest) = url.split_once("://")?;
    let scheme = scheme.to_ascii_lowercase();
    let default_port = match scheme.as_str() {
        "http" => "80",
        "https" => "443",
        _ => return None,
    };
    let end = rest.find(['/', '?', '#', '\\']).unwrap_or(rest.len());
    let authority = &rest[..end];
    // No userinfo, and no IPv6 literal: Butler is 127.0.0.1, and a form this
    // function does not parse must never compare equal to it.
    if authority.contains(['@', '[', ']']) {
        return None;
    }
    let (host, port) = match authority.split_once(':') {
        Some((h, p)) => {
            if p.is_empty() || !p.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            (h, Some(p))
        }
        None => (authority, None),
    };
    if host.is_empty() {
        return None;
    }
    let host = host.to_ascii_lowercase();
    match port {
        Some(p) if p != default_port => Some(format!("{scheme}://{host}:{p}")),
        _ => Some(format!("{scheme}://{host}")),
    }
}

/// A permission a page asked the webview for. Only the kinds the decision
/// distinguishes are named; every other WebView2 kind arrives as `Other`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Permission {
    Microphone,
    Camera,
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    Allow,
    Deny,
}

/// DA-13, AC-548: the microphone, for Butler's TOP document on exactly its
/// origin; nothing else, for anyone.
///
/// `in_frame` is true for a request raised through an iframe. Those are all
/// denied, because the URI cannot tell them apart: when a page delegates
/// with `<iframe allow="microphone">`, Chromium asks on behalf of the TOP
/// origin, and WebView2 reports `http://127.0.0.1:<port>/` for a request
/// that came from an iframe of another origin [MEASURED 2026-10-07, probe
/// build against a scratch daemon]. Butler's page has no iframes.
pub fn permission_decision(
    request_uri: &str,
    kind: Permission,
    allowed_origin: &str,
    in_frame: bool,
) -> Decision {
    let from_butler = origin_of(request_uri).as_deref() == Some(allowed_origin);
    if kind == Permission::Microphone && from_butler && !in_frame {
        Decision::Allow
    } else {
        Decision::Deny
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NavAction {
    /// Load it in the Butler window.
    Stay,
    /// Refuse it here and hand it to the default browser (DA-14).
    OpenInBrowser,
    /// Refuse it and open nothing (a non-web scheme).
    Block,
}

/// DA-14, AC-549: Butler's origin and the app's own pages stay in the
/// window; any other web address leaves for the default browser; any other
/// scheme (`file:`, `javascript:`, `ms-settings:`, ...) goes nowhere.
pub fn navigation(url: &str, butler: &str) -> NavAction {
    if url == "about:blank" {
        return NavAction::Stay;
    }
    match origin_of(url) {
        Some(o) if o == butler || o == LOCAL_PAGES_ORIGIN => NavAction::Stay,
        Some(_) => NavAction::OpenInBrowser,
        None => NavAction::Block,
    }
}

/// A window the page asked to open (`target=_blank`, `window.open`). Never
/// inside the app: a web address goes to the default browser, even Butler's
/// own, since a second Butler window is not a thing this app has.
pub fn new_window(url: &str) -> NavAction {
    match origin_of(url) {
        Some(_) => NavAction::OpenInBrowser,
        None => NavAction::Block,
    }
}

// ---------------------------------------------------------------- finder

/// AC-551's search order: the folder the app was built from, then the
/// usual places.
pub fn candidates(built_from: Option<&Path>, user_profile: Option<&Path>) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(p) = built_from {
        out.push(p.to_path_buf());
    }
    if let Some(home) = user_profile {
        out.push(home.join("Butler"));
    }
    out.push(PathBuf::from(r"C:\PROJECTS\butler"));
    out.push(PathBuf::from(r"E:\PROJECTS\butler"));
    out
}

/// A Butler checkout has `daemon\butlerd.py`.
pub fn is_checkout(dir: &Path) -> bool {
    dir.join("daemon").join("butlerd.py").is_file()
}

/// The remembered folder if it is still a checkout, else the first
/// candidate that is. None means: ask the user (once).
pub fn find_checkout(saved: Option<&Path>, candidates: &[PathBuf]) -> Option<PathBuf> {
    saved
        .into_iter()
        .chain(candidates.iter().map(PathBuf::as_path))
        .find(|p| is_checkout(p))
        .map(Path::to_path_buf)
}

/// The folder the user picked, or the refusal naming it (AC-551).
pub fn validate_choice(dir: &Path) -> Result<PathBuf, String> {
    if is_checkout(dir) {
        Ok(dir.to_path_buf())
    } else {
        Err(format!(
            "{} is not a Butler folder: it has no daemon\\butlerd.py.",
            dir.display()
        ))
    }
}

// --------------------------------------------------------- status machine

/// The tray's status line and the page the window shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Starting,
    Running,
    Stopped,
    NotSetUp,
}

impl Status {
    pub fn label(self) -> &'static str {
        match self {
            Status::Starting => "Starting...",
            Status::Running => "Running",
            Status::Stopped => "Stopped",
            Status::NotSetUp => "Not set up",
        }
    }

    /// The tray's Phone link asks the running daemon (DA-15), so it is
    /// offered only while Butler answers.
    pub fn can_link_phone(self) -> bool {
        self == Status::Running
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Event {
    /// The app is starting the daemon (first launch, Restart, Try again).
    LaunchBegun,
    /// `launch.py` refused: the data root is missing or not migrated.
    LaunchRefused,
    /// `launch.py` failed in any other way.
    LaunchFailed,
    Healthy,
    Unhealthy,
    /// `START_TIMEOUT_SECS` passed since `LaunchBegun` without `Healthy`.
    StartTimedOut,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Machine {
    pub status: Status,
    misses: u32,
}

impl Machine {
    pub fn new() -> Self {
        Machine { status: Status::Starting, misses: 0 }
    }

    /// Apply one event; returns the new status when it CHANGED, so the
    /// caller redraws the tray and the window only on a transition.
    pub fn on(&mut self, ev: Event) -> Option<Status> {
        let before = self.status;
        match ev {
            Event::LaunchBegun => self.set(Status::Starting),
            Event::LaunchRefused => self.set(Status::NotSetUp),
            Event::LaunchFailed => self.set(Status::Stopped),
            // Healthy from anywhere: a Butler started by start.bat while the
            // app showed Stopped is still the one Butler.
            Event::Healthy => self.set(Status::Running),
            Event::Unhealthy => {
                if self.status == Status::Running {
                    self.misses += 1;
                    if self.misses >= RUNNING_MISSES {
                        self.set(Status::Stopped);
                    }
                }
            }
            Event::StartTimedOut => {
                if self.status == Status::Starting {
                    self.set(Status::Stopped);
                }
            }
        }
        (self.status != before).then_some(self.status)
    }

    fn set(&mut self, s: Status) {
        self.status = s;
        self.misses = 0;
    }
}

impl Default for Machine {
    fn default() -> Self {
        Self::new()
    }
}

// ------------------------------------------------------------------- quit

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuitAction {
    /// Nothing answered and nothing of the app's was alive: nothing to stop.
    NotRunning,
    /// It stopped by itself after `POST /api/shutdown`.
    Done,
    /// Something the app started is still alive after the wait: end the
    /// app's job object (every process `launch.py` started, including a
    /// daemon that re-executed itself through `/api/restart`, whose pid
    /// changed).
    KillOwn,
    /// Butler still answers, but the app did not start it (start.bat did):
    /// the app holds no process of it and says so.
    CannotKill,
}

/// Before asking: is there anything to stop at all?
pub fn quit_needed(health_answers: bool, own_alive: bool) -> bool {
    health_answers || own_alive
}

/// After `POST /api/shutdown` and up to `QUIT_TIMEOUT_SECS` of waiting
/// (DA-11's escalation, the one documented fallback). The wait ends early
/// as soon as this says `Done`.
pub fn after_wait(health_answers: bool, own_alive: bool) -> QuitAction {
    match (health_answers, own_alive) {
        (false, false) => QuitAction::Done,
        (_, true) => QuitAction::KillOwn,
        (true, false) => QuitAction::CannotKill,
    }
}

/// The notification Quit shows (AC-545: "says which happened").
pub fn quit_message(action: QuitAction) -> String {
    match action {
        QuitAction::NotRunning => "Butler was not running.".to_string(),
        QuitAction::Done => "Butler stopped.".to_string(),
        QuitAction::KillOwn => format!(
            "Butler did not stop within {QUIT_TIMEOUT_SECS} s, so the processes              this app started were ended."
        ),
        QuitAction::CannotKill => format!(
            "Butler did not stop within {QUIT_TIMEOUT_SECS} s. It was not started              by this app, so it is still running; stop it with stop.bat."
        ),
    }
}

/// `BUTLER_APP_PORT` (a manual test against a scratch daemon), else 8141.
/// The bool says the override is set: the app then never runs `launch.py`,
/// which always starts on 8141 and clears 8140-8142 -- the live Butler.
pub fn app_port(env: Option<&str>) -> Result<(u16, bool), String> {
    match env {
        None => Ok((DEFAULT_PORT, false)),
        Some(v) => v
            .trim()
            .parse::<u16>()
            .ok()
            .filter(|p| *p != 0)
            .map(|p| (p, true))
            .ok_or_else(|| format!("BUTLER_APP_PORT={v:?} is not a port number")),
    }
}

// --------------------------------------------------------------- launcher

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LaunchResult {
    Started { pid: u32 },
    NotSetUp { message: String },
    Failed { output: String },
}

/// The words `scripts/launch.py` `_refuse` prints. A CONTRACT with it: the
/// Not set up page shows launch.py's own message, so the app never spells
/// the migrate command itself (DA-9).
pub const REFUSAL_MARKER: &str = "is missing or not migrated";

/// What `launch.py --detached` meant: exit 0 with `Butler daemon pid N`, its
/// refusal (exit 1 and `REFUSAL_MARKER`), or anything else as a failure with
/// everything it printed.
pub fn classify_launch(exit_ok: bool, stdout: &str, stderr: &str) -> LaunchResult {
    if exit_ok {
        let pid = stdout
            .lines()
            .find_map(|l| l.trim().strip_prefix("Butler daemon pid "))
            .and_then(|p| p.trim().parse().ok());
        if let Some(pid) = pid {
            return LaunchResult::Started { pid };
        }
    } else if stdout.contains(REFUSAL_MARKER) {
        return LaunchResult::NotSetUp { message: stdout.trim().to_string() };
    }
    let output = format!("{}\n{}", stdout.trim(), stderr.trim()).trim().to_string();
    LaunchResult::Failed { output }
}

/// How long the Phone link page waits for `/api/phonelink`: set-up runs
/// `tailscale serve` (30 s at most) between two reads of Tailscale's state.
pub const PHONE_LINK_TIMEOUT_SECS: u64 = 120;

/// A raw HTTP/1.1 answer read to the end (`Connection: close`) split into
/// its status code and body. A chunked answer is refused rather than shown
/// with its chunk sizes in it (fail fast); the daemon's JSON answers carry a
/// Content-Length.
pub fn split_http(raw: &[u8]) -> Result<(u16, String), String> {
    let text = String::from_utf8_lossy(raw);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or_else(|| format!("not an HTTP answer: {:?}", text.chars().take(80).collect::<String>()))?;
    let code = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| format!("no status code in {:?}", head.lines().next().unwrap_or("")))?;
    if head.to_ascii_lowercase().contains("transfer-encoding: chunked") {
        return Err("a chunked HTTP answer, which this app does not read".into());
    }
    Ok((code, body.to_string()))
}

/// The last `n` lines of a log.
pub fn tail(text: &str, n: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}

// ================================================================== tests

#[cfg(test)]
mod tests {
    use super::*;

    const BUTLER: &str = "http://127.0.0.1:8141";

    // ---- origin

    #[test]
    fn origin_of_strips_path_query_and_default_port() {
        assert_eq!(origin_of("http://127.0.0.1:8141/").as_deref(), Some(BUTLER));
        assert_eq!(origin_of("http://127.0.0.1:8141").as_deref(), Some(BUTLER));
        assert_eq!(origin_of("HTTP://127.0.0.1:8141/x?y#z").as_deref(), Some(BUTLER));
        assert_eq!(origin_of("http://Example.com:80/a").as_deref(), Some("http://example.com"));
        assert_eq!(origin_of("https://example.com:443").as_deref(), Some("https://example.com"));
        assert_eq!(origin_of("http://tauri.localhost/starting.html").as_deref(),
                   Some(LOCAL_PAGES_ORIGIN));
    }

    #[test]
    fn origin_of_refuses_lookalikes_and_other_schemes() {
        assert_eq!(origin_of("http://127.0.0.1:8141@evil.example/"), None);
        assert_eq!(origin_of("http://127.0.0.1:8141.evil.example/"), None);
        assert_eq!(origin_of("http://:8141/"), None);
        assert_eq!(origin_of("http:///"), None);
        assert_eq!(origin_of("file:///C:/x"), None);
        assert_eq!(origin_of("javascript:alert(1)"), None);
        assert_eq!(origin_of("data:text/html,x"), None);
        assert_eq!(origin_of(""), None);
    }

    // ---- permission (DA-13, AC-548): the aimed tests of the mutation check

    #[test]
    fn microphone_allowed_for_butler_origin_only() {
        for uri in ["http://127.0.0.1:8141/", "http://127.0.0.1:8141/index.html?x=1"] {
            assert_eq!(permission_decision(uri, Permission::Microphone, BUTLER, false), Decision::Allow);
        }
    }

    #[test]
    fn microphone_denied_for_every_other_origin() {
        for uri in [
            "http://127.0.0.1:8140/",
            "https://127.0.0.1:8141/",
            "http://localhost:8141/",
            "http://127.0.0.1:8142/",
            "http://127.0.0.1:8141@evil.example/",
            "http://127.0.0.1:8141.evil.example/",
            "http://tauri.localhost/starting.html",
            "https://evil.example/",
            "about:blank",
            "",
        ] {
            assert_eq!(permission_decision(uri, Permission::Microphone, BUTLER, false), Decision::Deny,
                       "{uri}");
        }
    }

    #[test]
    fn every_other_permission_denied_even_for_butler() {
        for kind in [Permission::Camera, Permission::Other] {
            assert_eq!(permission_decision("http://127.0.0.1:8141/", kind, BUTLER, false), Decision::Deny,
                       "{kind:?}");
        }
    }

    #[test]
    fn requests_through_a_frame_are_denied_even_as_butler() {
        // A delegated iframe's request arrives with the TOP origin's URI.
        for kind in [Permission::Microphone, Permission::Camera, Permission::Other] {
            assert_eq!(permission_decision("http://127.0.0.1:8141/", kind, BUTLER, true),
                       Decision::Deny, "{kind:?}");
        }
    }

    // ---- navigation (DA-14, AC-549)

    #[test]
    fn butler_and_local_pages_stay_in_the_window() {
        assert_eq!(navigation("http://127.0.0.1:8141/", BUTLER), NavAction::Stay);
        assert_eq!(navigation("http://tauri.localhost/stopped.html", BUTLER), NavAction::Stay);
        assert_eq!(navigation("about:blank", BUTLER), NavAction::Stay);
    }

    #[test]
    fn other_web_addresses_leave_for_the_browser() {
        assert_eq!(navigation("https://github.com/x", BUTLER), NavAction::OpenInBrowser);
        assert_eq!(navigation("https://127.0.0.1:8140/", BUTLER), NavAction::OpenInBrowser);
        assert_eq!(navigation("http://127.0.0.1:8141@evil.example/", BUTLER), NavAction::Block);
    }

    #[test]
    fn non_web_schemes_go_nowhere() {
        for url in ["file:///C:/Windows/win.ini", "javascript:alert(1)", "ms-settings:privacy"] {
            assert_eq!(navigation(url, BUTLER), NavAction::Block, "{url}");
        }
    }

    #[test]
    fn new_windows_never_open_inside_the_app() {
        assert_eq!(new_window("http://127.0.0.1:8141/x"), NavAction::OpenInBrowser);
        assert_eq!(new_window("https://example.com/"), NavAction::OpenInBrowser);
        assert_eq!(new_window("file:///C:/x"), NavAction::Block);
    }

    // ---- finder (AC-551)

    fn scratch(name: &str, checkout: bool) -> PathBuf {
        let d = std::env::temp_dir().join(format!("butler-app-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("daemon")).unwrap();
        if checkout {
            std::fs::write(d.join("daemon").join("butlerd.py"), "").unwrap();
        }
        d
    }

    #[test]
    fn candidates_in_spec_order() {
        let c = candidates(Some(Path::new(r"D:\src\butler")), Some(Path::new(r"C:\Users\u")));
        assert_eq!(c, vec![
            PathBuf::from(r"D:\src\butler"),
            PathBuf::from(r"C:\Users\u\Butler"),
            PathBuf::from(r"C:\PROJECTS\butler"),
            PathBuf::from(r"E:\PROJECTS\butler"),
        ]);
    }

    #[test]
    fn finder_takes_saved_then_first_valid_candidate() {
        let good1 = scratch("good1", true);
        let good2 = scratch("good2", true);
        let bad = scratch("bad", false);
        let list = vec![bad.clone(), good1.clone(), good2.clone()];
        assert_eq!(find_checkout(None, &list), Some(good1.clone()));
        assert_eq!(find_checkout(Some(&good2), &list), Some(good2.clone()));
        // A remembered folder that stopped being a checkout is skipped.
        assert_eq!(find_checkout(Some(&bad), &list), Some(good1.clone()));
        assert_eq!(find_checkout(Some(&bad), std::slice::from_ref(&bad)), None);
        for d in [good1, good2, bad] {
            std::fs::remove_dir_all(d).unwrap();
        }
    }

    #[test]
    fn a_folder_without_butlerd_is_refused_naming_it() {
        let bad = scratch("pick-bad", false);
        let err = validate_choice(&bad).unwrap_err();
        assert!(err.contains(&bad.display().to_string()), "{err}");
        assert!(err.contains(r"daemon\butlerd.py"), "{err}");
        let good = scratch("pick-good", true);
        assert_eq!(validate_choice(&good), Ok(good.clone()));
        for d in [good, bad] {
            std::fs::remove_dir_all(d).unwrap();
        }
    }

    // ---- status machine (AC-544, AC-550)

    #[test]
    fn launch_then_healthy_is_running() {
        let mut m = Machine::new();
        assert_eq!(m.on(Event::LaunchBegun), None);
        assert_eq!(m.on(Event::Unhealthy), None);
        assert_eq!(m.on(Event::Healthy), Some(Status::Running));
        assert_eq!(m.on(Event::Healthy), None);
    }

    #[test]
    fn start_timeout_is_stopped_but_only_while_starting() {
        let mut m = Machine::new();
        assert_eq!(m.on(Event::StartTimedOut), Some(Status::Stopped));
        let mut r = Machine::new();
        r.on(Event::Healthy);
        assert_eq!(r.on(Event::StartTimedOut), None);
        assert_eq!(r.status, Status::Running);
    }

    #[test]
    fn refusal_is_not_set_up_and_failure_is_stopped() {
        let mut m = Machine::new();
        assert_eq!(m.on(Event::LaunchRefused), Some(Status::NotSetUp));
        assert_eq!(m.on(Event::LaunchBegun), Some(Status::Starting));
        assert_eq!(m.on(Event::LaunchFailed), Some(Status::Stopped));
    }

    #[test]
    fn running_survives_fewer_than_three_misses() {
        let mut m = Machine::new();
        m.on(Event::Healthy);
        for _ in 0..RUNNING_MISSES - 1 {
            assert_eq!(m.on(Event::Unhealthy), None);
        }
        assert_eq!(m.on(Event::Healthy), None);
        // the count restarted: two more misses are still not enough
        for _ in 0..RUNNING_MISSES - 1 {
            assert_eq!(m.on(Event::Unhealthy), None);
        }
        assert_eq!(m.on(Event::Unhealthy), Some(Status::Stopped));
    }

    #[test]
    fn a_butler_started_elsewhere_brings_stopped_back_to_running() {
        let mut m = Machine::new();
        m.on(Event::LaunchFailed);
        assert_eq!(m.on(Event::Unhealthy), None);
        assert_eq!(m.on(Event::Healthy), Some(Status::Running));
    }

    // ---- quit (DA-11, AC-545)

    #[test]
    fn quit_is_needed_when_anything_answers_or_lives() {
        assert!(!quit_needed(false, false));
        assert!(quit_needed(true, false));
        assert!(quit_needed(false, true));
    }

    #[test]
    fn quit_escalates_only_when_it_did_not_stop() {
        assert_eq!(after_wait(false, false), QuitAction::Done);
        assert_eq!(after_wait(false, true), QuitAction::KillOwn);
        assert_eq!(after_wait(true, true), QuitAction::KillOwn);
        assert_eq!(after_wait(true, false), QuitAction::CannotKill);
    }

    #[test]
    fn quit_messages_say_which_happened() {
        assert_eq!(quit_message(QuitAction::Done), "Butler stopped.");
        assert!(quit_message(QuitAction::KillOwn).contains("were ended"));
        assert!(quit_message(QuitAction::CannotKill).contains("stop.bat"));
        assert!(quit_message(QuitAction::NotRunning).contains("not running"));
    }

    #[test]
    fn port_override_is_strict() {
        assert_eq!(app_port(None), Ok((8141, false)));
        assert_eq!(app_port(Some("18141")), Ok((18141, true)));
        assert!(app_port(Some("0")).is_err());
        assert!(app_port(Some("abc")).is_err());
        assert!(app_port(Some("70000")).is_err());
    }

    // ---- launcher output

    #[test]
    fn launch_started_reads_the_pid() {
        let out = "Starting Butler over TLS on 0.0.0.0:8140 (log: x) ...\nButler daemon pid 4242\n";
        assert_eq!(classify_launch(true, out, ""), LaunchResult::Started { pid: 4242 });
    }

    #[test]
    fn launch_refusal_keeps_launch_py_message_verbatim() {
        let out = "\n  Butler's data root \"E:\\Butler-Data\" is missing or not migrated yet.\n  \
                   Preview:  x --dry-run\n  Migrate:  x\n\n";
        match classify_launch(false, out, "") {
            LaunchResult::NotSetUp { message } => {
                assert!(message.starts_with("Butler's data root"));
                assert!(message.contains("Migrate:  x"));
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn anything_else_is_a_failure_with_its_output() {
        assert_eq!(classify_launch(false, "", "Traceback: boom"),
                   LaunchResult::Failed { output: "Traceback: boom".into() });
        // exit 0 without the pid line is not a start (fail fast)
        assert!(matches!(classify_launch(true, "hello", ""), LaunchResult::Failed { .. }));
    }

    #[test]
    fn phone_link_is_offered_only_while_butler_runs() {
        assert!(Status::Running.can_link_phone());
        for s in [Status::Starting, Status::Stopped, Status::NotSetUp] {
            assert!(!s.can_link_phone(), "{s:?}");
        }
    }

    #[test]
    fn split_http_returns_status_and_body() {
        let raw = b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n\
                    Content-Length: 13\r\n\r\n{\"ok\": false}";
        assert_eq!(split_http(raw), Ok((403, "{\"ok\": false}".to_string())));
        assert!(split_http(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello").is_err());
        assert!(split_http(b"garbage").is_err());
        assert!(split_http(b"HTTP/1.1 xyz\r\n\r\n").is_err());
    }

    #[test]
    fn tail_keeps_the_last_lines() {
        let log: String = (1..=30).map(|i| format!("line {i}\n")).collect();
        let t = tail(&log, LOG_TAIL_LINES);
        assert_eq!(t.lines().count(), 20);
        assert!(t.starts_with("line 11"));
        assert!(t.ends_with("line 30"));
        assert_eq!(tail("a\nb", 20), "a\nb");
    }
}
