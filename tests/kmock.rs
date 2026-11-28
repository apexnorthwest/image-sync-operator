use std::path::Path;
use std::process::Command;

#[test]
fn kmock() {
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/kmock/run_tests.py");
    let status = Command::new("python3")
        .arg(&script)
        .arg("--no-build")
        .env(
            "IMAGE_SYNC_OPERATOR_BIN",
            env!("CARGO_BIN_EXE_image-sync-operator"),
        )
        .status()
        .expect("failed to start the KMock test runner (python3 is required)");

    assert!(status.success(), "KMock test runner failed with {status}");
}
