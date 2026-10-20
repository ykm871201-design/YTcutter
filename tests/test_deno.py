"""deno.exe 의 CPU 종류 확인: 이 PC 에서 실행할 수 없는 deno 는 실행 전에 건너뛴다."""
import os

from app import downloader
from app.downloader import deno_problem, exe_machine, find_deno

X86, X64, ARM64 = 0x014C, 0x8664, 0xAA64


def _pe(path, machine):
    head = bytearray(b"MZ" + b"\0" * 62)
    head[0x3C:0x40] = (64).to_bytes(4, "little")
    path.write_bytes(bytes(head) + b"PE\0\0" + machine.to_bytes(2, "little") + b"\0" * 32)
    return path


def test_exe_machine(tmp_path):
    assert exe_machine(_pe(tmp_path / "a.exe", ARM64)) == ARM64
    assert exe_machine(_pe(tmp_path / "b.exe", X64)) == X64
    (tmp_path / "elf").write_bytes(b"\x7fELF" + b"\0" * 100)
    assert exe_machine(tmp_path / "elf") is None
    (tmp_path / "short").write_bytes(b"MZ")
    assert exe_machine(tmp_path / "short") is None
    assert exe_machine(tmp_path / "없음.exe") is None


def test_deno_problem(tmp_path):
    arm = _pe(tmp_path / "arm.exe", ARM64)
    x64 = _pe(tmp_path / "x64.exe", X64)
    x86 = _pe(tmp_path / "x86.exe", X86)
    assert "ARM64" in deno_problem(arm, native=X64)   # 이번 오류: x64 PC 에 ARM64 deno
    assert deno_problem(x64, native=X64) is None
    assert deno_problem(x86, native=X64) is None
    assert deno_problem(arm, native=ARM64) is None
    assert deno_problem(x64, native=ARM64) is None    # ARM64 Windows 11 은 x64 에뮬레이션
    assert deno_problem(x64, native=X86) is not None
    (tmp_path / "text.exe").write_text("not an exe")
    assert deno_problem(tmp_path / "text.exe", native=X64) is not None


def test_find_deno_skips_unusable(tmp_path, monkeypatch):
    app_dir, path_dir = tmp_path / "app", tmp_path / "bin"
    exe = "deno.exe" if os.name == "nt" else "deno"
    for d in (app_dir, path_dir):
        d.mkdir()
    _pe(app_dir / exe, ARM64)
    _pe(path_dir / exe, X64)
    monkeypatch.setattr("app.utils.app_base_dir", lambda: app_dir)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(downloader, "deno_problem",
                        lambda p, native=None: deno_problem(p, native=X64))

    deno, skipped = find_deno()
    assert deno == str(path_dir / exe)                # 프로그램 폴더의 ARM64 deno 는 건너뛰고 PATH 의 것
    assert len(skipped) == 1 and "ARM64" in skipped[0]
    assert downloader._js_runtimes() == {"deno": {"path": deno}, "node": {}}

    (path_dir / exe).unlink()
    assert find_deno()[0] is None
    assert downloader._js_runtimes() == {"node": {}}   # 맞지 않는 deno 를 yt-dlp 가 다시 찾지 않게
