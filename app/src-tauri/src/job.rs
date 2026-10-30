//! The app's Windows job object (DA-8): every process `launch.py` starts,
//! the daemon included, lives in it, so the daemon cannot outlive a crashed
//! app in a half state (`KILL_ON_JOB_CLOSE`), and Quit's escalation can end
//! exactly what the app started (DA-11) -- even a daemon that re-executed
//! itself through `/api/restart` and so has a new pid.
//!
//! Nested under nothing of Butler's: the daemon makes its own kill-on-close
//! job for its children (`daemon/childproc.py`), which Windows nests inside
//! this one, and its terminals open through `wt.exe`, outside both.

use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::process::{Child, Command};

use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

const CREATE_SUSPENDED: u32 = 0x0000_0004;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct Job(HANDLE);

// The handle is a kernel object usable from any thread; the app holds it for
// its whole life and the OS closes it at exit (which is the point).
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

fn err(what: &str, e: windows::core::Error) -> std::io::Error {
    std::io::Error::other(format!("{what}: {e}"))
}

impl Job {
    pub fn new() -> std::io::Result<Job> {
        unsafe {
            let h = CreateJobObjectW(None, None).map_err(|e| err("CreateJobObject", e))?;
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
            .map_err(|e| err("SetInformationJobObject", e))?;
            Ok(Job(h))
        }
    }

    /// Spawn `cmd` SUSPENDED and without a console window (these flags
    /// REPLACE any `cmd` had), put it in the job, then let it run: no
    /// instruction of it runs outside the job, so neither can its children.
    pub fn spawn(&self, cmd: &mut Command) -> std::io::Result<Child> {
        let mut child = cmd.creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW).spawn()?;
        let placed = unsafe {
            AssignProcessToJobObject(self.0, HANDLE(child.as_raw_handle()))
                .map_err(|e| err("AssignProcessToJobObject", e))
                .and_then(|()| resume(child.id()))
        };
        if let Err(e) = placed {
            let _ = child.kill();
            return Err(e);
        }
        Ok(child)
    }

    /// How many processes of the app's are alive.
    pub fn active(&self) -> std::io::Result<u32> {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        unsafe {
            QueryInformationJobObject(
                Some(self.0),
                JobObjectBasicAccountingInformation,
                &mut info as *mut _ as *mut core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                None,
            )
            .map_err(|e| err("QueryInformationJobObject", e))?;
        }
        Ok(info.ActiveProcesses)
    }

    /// End every process in the job (Quit's escalation).
    pub fn terminate(&self) -> std::io::Result<()> {
        unsafe { TerminateJobObject(self.0, 1).map_err(|e| err("TerminateJobObject", e)) }
    }
}

/// Resume the one thread of a process created suspended.
unsafe fn resume(pid: u32) -> std::io::Result<()> {
    let snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        .map_err(|e| err("CreateToolhelp32Snapshot", e))?;
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    let mut resumed = 0;
    let mut more = Thread32First(snap, &mut entry).is_ok();
    while more {
        if entry.th32OwnerProcessID == pid {
            let t = OpenThread(THREAD_SUSPEND_RESUME, false, entry.th32ThreadID)
                .map_err(|e| err("OpenThread", e))?;
            let r = ResumeThread(t);
            let _ = CloseHandle(t);
            if r == u32::MAX {
                let _ = CloseHandle(snap);
                return Err(std::io::Error::last_os_error());
            }
            resumed += 1;
        }
        more = Thread32Next(snap, &mut entry).is_ok();
    }
    let _ = CloseHandle(snap);
    if resumed == 0 {
        return Err(std::io::Error::other(format!("no thread of pid {pid} to resume")));
    }
    Ok(())
}
