//! The microphone gate (DA-13, AC-548): the ONLY thing between a page in the
//! window and the microphone, because the app grants it with no prompt.
//!
//! WebView2's `PermissionRequested`, registered directly, and NOT Tauri
//! 2.12's `on_permission_request`: Tauri's hook hands over only the KIND
//! (tauri-runtime-wry 2.12.1 `lib.rs:4892`, wry 0.57.0 `webview2/mod.rs:532`
//! read 2026-10-07), not the URI. WebView2's args carry the URI, and every
//! iframe gets its OWN handler (`FrameCreated`, nested frames included) so a
//! request raised through a frame is told apart from the top document's --
//! the URI alone cannot do that (see `logic::permission_decision`).
//! `logic::permission_decision` decides both.
//!
//! Every request gets an explicit Allow or Deny -- never "default", which
//! would show WebView2's own prompt -- and none is saved in the profile, so
//! every later request comes back through here.

use webview2_com::Microsoft::Web::WebView2::Win32::{
    ICoreWebView2Controller, ICoreWebView2Frame, ICoreWebView2Frame3, ICoreWebView2Frame7,
    ICoreWebView2PermissionRequestedEventArgs, ICoreWebView2PermissionRequestedEventArgs3,
    ICoreWebView2_4, COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_CAMERA,
    COREWEBVIEW2_PERMISSION_KIND_MICROPHONE, COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    COREWEBVIEW2_PERMISSION_STATE_DENY,
};
use webview2_com::{
    take_pwstr, FrameChildFrameCreatedEventHandler, FrameCreatedEventHandler,
    FramePermissionRequestedEventHandler, PermissionRequestedEventHandler,
};
use windows::core::{Interface, PWSTR};

use crate::logic::{permission_decision, Decision, Permission};

fn kind_of(k: COREWEBVIEW2_PERMISSION_KIND) -> Permission {
    match k {
        COREWEBVIEW2_PERMISSION_KIND_MICROPHONE => Permission::Microphone,
        COREWEBVIEW2_PERMISSION_KIND_CAMERA => Permission::Camera,
        _ => Permission::Other,
    }
}

/// Decide one request and answer it. The state FIRST: if anything after it
/// fails, the answer already stands.
unsafe fn answer(
    args: &ICoreWebView2PermissionRequestedEventArgs,
    allowed_origin: &str,
    in_frame: bool,
) -> windows::core::Result<()> {
    let mut uri = PWSTR::null();
    args.Uri(&mut uri)?;
    let uri = take_pwstr(uri);
    let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
    args.PermissionKind(&mut kind)?;
    let state = match permission_decision(&uri, kind_of(kind), allowed_origin, in_frame) {
        Decision::Allow => COREWEBVIEW2_PERMISSION_STATE_ALLOW,
        Decision::Deny => COREWEBVIEW2_PERMISSION_STATE_DENY,
    };
    args.SetState(state)?;
    args.cast::<ICoreWebView2PermissionRequestedEventArgs3>()?.SetSavesInProfile(false)
}

/// Give `frame`, and every frame created inside it, its own handler; mark
/// each request handled so it never reaches the top document's handler.
unsafe fn guard_frame(frame: ICoreWebView2Frame, allowed: String) -> windows::core::Result<()> {
    let mut token = 0i64;
    let origin = allowed.clone();
    frame.cast::<ICoreWebView2Frame3>()?.add_PermissionRequested(
        &FramePermissionRequestedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()) };
            answer(&args.cast()?, &origin, true)?;
            args.SetHandled(true)
        })),
        &mut token,
    )?;
    frame.cast::<ICoreWebView2Frame7>()?.add_FrameCreated(
        &FrameChildFrameCreatedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else { return Ok(()) };
            guard_frame(args.Frame()?, allowed.clone())
        })),
        &mut token,
    )
}

/// Register the gate on the window's WebView2. Must complete BEFORE the
/// window navigates to Butler (main.rs waits for it).
pub fn install(controller: &ICoreWebView2Controller, allowed_origin: String) -> windows::core::Result<()> {
    unsafe {
        let core = controller.CoreWebView2()?;
        let mut token = 0i64;
        let top = allowed_origin.clone();
        core.add_PermissionRequested(
            &PermissionRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                answer(&args, &top, false)
            })),
            &mut token,
        )?;
        core.cast::<ICoreWebView2_4>()?.add_FrameCreated(
            &FrameCreatedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                guard_frame(args.Frame()?, allowed_origin.clone())
            })),
            &mut token,
        )?;
    }
    Ok(())
}
