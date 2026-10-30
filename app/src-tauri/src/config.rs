//! `%APPDATA%\Butler\app.json`: the Butler folder the app found or was told
//! (AC-551). The only thing the app remembers; everything else is asked of
//! the daemon or `scripts\launch.py`.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Serialize, Deserialize)]
struct AppJson {
    butler: PathBuf,
}

fn file() -> Result<PathBuf, String> {
    let appdata = std::env::var_os("APPDATA").ok_or("APPDATA is not set")?;
    Ok(PathBuf::from(appdata).join("Butler").join("app.json"))
}

/// The remembered folder, None when nothing is remembered yet. A file that
/// exists but does not parse is an error, not a silent fresh start.
pub fn load() -> Result<Option<PathBuf>, String> {
    let path = file()?;
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("cannot read {}: {e}", path.display())),
    };
    let parsed: AppJson = serde_json::from_str(&text)
        .map_err(|e| format!("{} is not valid: {e}", path.display()))?;
    Ok(Some(parsed.butler))
}

pub fn save(butler: &std::path::Path) -> Result<(), String> {
    let path = file()?;
    let dir = path.parent().expect("app.json has a parent folder");
    std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let text = serde_json::to_string_pretty(&AppJson { butler: butler.to_path_buf() })
        .expect("a path serialises");
    std::fs::write(&path, text).map_err(|e| format!("cannot write {}: {e}", path.display()))
}
