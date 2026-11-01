use std::{
    fs::{self, OpenOptions},
    io::Write,
    net::TcpListener,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{path::BaseDirectory, Manager};

#[cfg(target_os = "windows")]
struct BackendProcess(Mutex<Option<Child>>);

#[cfg(target_os = "windows")]
fn shutdown_backend(child: &mut Child) {
    // Let FastAPI run its lifespan cleanup before the bounded hard-kill fallback.
    if let Some(stdin) = child.stdin.as_mut() {
        let _ = stdin.write_all(b"shutdown\n");
        let _ = stdin.flush();
    }

    let deadline = Instant::now() + Duration::from_secs(12);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(100)),
            Ok(None) | Err(_) => break,
        }
    }

    let _ = child.kill();
    let _ = child.wait();
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            // Debug builds use the separately started development API.
            // Release Windows bundles start their packaged, loopback-only service here.
            #[cfg(target_os = "windows")]
            if !cfg!(debug_assertions) {
                let backend_path = app
                    .path()
                    .resolve("resources/pcs-api/pcs-api.exe", BaseDirectory::Resource)?;
                if !backend_path.is_file() {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::NotFound,
                        format!("Bundled local API executable is missing: {}", backend_path.display()),
                    )
                    .into());
                }

                // Never silently connect the UI to another process using our API port.
                let listener = TcpListener::bind(("127.0.0.1", 8765)).map_err(|error| {
                    std::io::Error::new(
                        error.kind(),
                        format!("Cannot start Personal Clipper Studio: 127.0.0.1:8765 is unavailable ({error}). Close the other local service and retry."),
                    )
                })?;
                drop(listener);

                let log_dir = app.path().app_log_dir()?;
                fs::create_dir_all(&log_dir)?;
                let stdout = OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(log_dir.join("backend.log"))?;
                let stderr = stdout.try_clone()?;

                let mut command = Command::new(&backend_path);
                command
                    .arg("--port")
                    .arg("8765")
                    .stdin(Stdio::piped())
                    .stdout(Stdio::from(stdout))
                    .stderr(Stdio::from(stderr));

                // The backend must not open an extra console window on Windows.
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);

                let child = command.spawn()?;
                app.manage(BackendProcess(Mutex::new(Some(child))));
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Personal Clipper Studio");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            #[cfg(target_os = "windows")]
            if let Some(state) = app_handle.try_state::<BackendProcess>() {
                let mut guard = state.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                if let Some(mut child) = guard.take() {
                    shutdown_backend(&mut child);
                }
            }
        }
    });
}
