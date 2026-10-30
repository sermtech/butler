//! Talking to Butler: its health and shutdown routes on the LOCAL listener,
//! and `scripts\launch.py`, the ONE launcher (DA-9). Nothing here decides;
//! `logic.rs` does.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use crate::job::Job;
use crate::logic::{classify_launch, split_http, LaunchResult, PHONE_LINK_TIMEOUT_SECS};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);
/// Every local route also needs this header (docs/messaging.md 6.5 RV-1,
/// AC-586): a web page cannot send it cross-site, the app does.
const LOCAL_HEADER: &str = "X-Butler-Local: 1\r\n";

/// The status code of a bodyless request to 127.0.0.1:`port`. A plain
/// socket, not an HTTP client crate: two fixed routes on loopback.
fn request(port: u16, method: &str, path: &str) -> std::io::Result<u16> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, HTTP_TIMEOUT)?;
    s.set_read_timeout(Some(HTTP_TIMEOUT))?;
    s.set_write_timeout(Some(HTTP_TIMEOUT))?;
    write!(
        s,
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{LOCAL_HEADER}\
         Content-Length: 0\r\nConnection: close\r\n\r\n"
    )?;
    let mut head = Vec::new();
    let mut buf = [0u8; 256];
    while !head.windows(2).any(|w| w == b"\r\n") {
        let n = s.read(&mut buf)?;
        if n == 0 {
            break;
        }
        head.extend_from_slice(&buf[..n]);
    }
    let line = String::from_utf8_lossy(&head);
    line.split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| std::io::Error::other(format!("not an HTTP answer: {line:?}")))
}

/// `GET /api/health` answered 200.
pub fn healthy(port: u16) -> bool {
    matches!(request(port, "GET", "/api/health"), Ok(200))
}

/// `POST /api/shutdown` (DA-11). Accepted only on the LOCAL listener.
pub fn post_shutdown(port: u16) -> std::io::Result<u16> {
    request(port, "POST", "/api/shutdown")
}

/// `GET` (read) or `POST` (set up) `/api/phonelink` on the LOCAL listener,
/// so a set-up is this machine's (DA-16, AC-541): the status code and the
/// daemon's pairing document, unparsed (the page renders it).
pub fn phone_link(port: u16, setup: bool) -> Result<(u16, String), String> {
    let io = |e: std::io::Error| format!("Butler did not answer on port {port}: {e}");
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, HTTP_TIMEOUT).map_err(io)?;
    s.set_read_timeout(Some(Duration::from_secs(PHONE_LINK_TIMEOUT_SECS))).map_err(io)?;
    s.set_write_timeout(Some(HTTP_TIMEOUT)).map_err(io)?;
    let method = if setup { "POST" } else { "GET" };
    write!(
        s,
        "{method} /api/phonelink HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{LOCAL_HEADER}\
         Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    .map_err(io)?;
    let mut raw = Vec::new();
    s.read_to_end(&mut raw).map_err(io)?;
    split_http(&raw)
}

fn python(repo: &Path) -> PathBuf {
    repo.join(".venv").join("Scripts").join("python.exe")
}

/// Run `launch.py --detached` inside `job` (no console window: `Job::spawn`
/// sets the flags), and say
/// what it meant. It returns once the daemon is spawned; the daemon itself
/// stays in the job (DA-8).
pub fn launch(repo: &Path, job: &Job) -> LaunchResult {
    let py = python(repo);
    let mut cmd = Command::new(&py);
    cmd.args(["-X", "utf8"])
        .arg(repo.join("scripts").join("launch.py"))
        .arg("--detached")
        .current_dir(repo)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = match job.spawn(&mut cmd) {
        Ok(c) => c,
        Err(e) => {
            return LaunchResult::Failed { output: format!("could not run {}: {e}", py.display()) }
        }
    };
    match child.wait_with_output() {
        Ok(out) => classify_launch(
            out.status.success(),
            &String::from_utf8_lossy(&out.stdout),
            &String::from_utf8_lossy(&out.stderr),
        ),
        Err(e) => LaunchResult::Failed { output: format!("launch.py did not finish: {e}") },
    }
}

/// Where `butler.log` is, ASKED of `launch.py` (its `data_root`, then
/// `daemon.paths`), never worked out here (DA-9).
pub fn log_path(repo: &Path) -> Result<PathBuf, String> {
    const ASK: &str = "import os, sys; sys.path.insert(0, sys.argv[1]); import launch; \
        env = dict(os.environ); env['BUTLER_DATA'] = launch.data_root(env); \
        print(launch.paths.resolve(env=env, check=False).logs / 'butler.log')";
    let out = Command::new(python(repo))
        .args(["-X", "utf8", "-c", ASK])
        .arg(repo.join("scripts"))
        .current_dir(repo)
        .stdin(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map_err(|e| format!("could not ask launch.py for the log path: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "launch.py could not name the log: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(PathBuf::from(String::from_utf8_lossy(&out.stdout).trim()))
}
