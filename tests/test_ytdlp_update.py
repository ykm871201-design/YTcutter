"""yt-dlp 업데이트: 버전 비교 · 휠 설치 · 다음 실행 시 등록(activate) 검증 (네트워크 없이)."""
import hashlib
import io
import json
import sys
import zipfile

import pytest

from app import VERSION, ytdlp_update
from app.ytdlp_update import UpdateFailed, UpdateResult, ejs_requirement, version_tuple


def _wheel(files: dict[str, str]) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def _install(root, version="2099.1.2"):
    data, sha = _wheel({"yt_dlp/__init__.py": "", "yt_dlp/version.py": f"__version__ = '{version}'\n"})
    ejs, ejs_sha = _wheel({"yt_dlp_ejs/__init__.py": ""})
    return ytdlp_update.install_wheels(root, version, [("yt_dlp.whl", data, sha),
                                                       ("yt_dlp_ejs.whl", ejs, ejs_sha)])


@pytest.fixture
def clean_path(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(ytdlp_update, "active_version", None)


def test_version_tuple():
    assert version_tuple("2026.08.19") == version_tuple("2026.8.19")
    assert version_tuple("2026.10.3") > version_tuple("2026.9.30")
    assert UpdateResult("2026.08.19", "2026.9.1").updated
    assert not UpdateResult("2026.08.19", "2026.8.19").updated


def test_ejs_requirement():
    assert ejs_requirement(['yt-dlp-ejs==0.8.0; extra == "default"', 'yt-dlp-ejs==0.8.0; extra == "pin"']) == "0.8.0"
    assert ejs_requirement(["yt-dlp-ejs; extra == 'default'"]) == ""
    assert ejs_requirement(['brotli; extra == "default"']) is None
    assert ejs_requirement(None) is None


def test_install_and_activate(tmp_path, clean_path):
    root = tmp_path / "yt-dlp"
    (root / "2000.1.1" / "yt_dlp").mkdir(parents=True)   # 이전 업데이트
    site = _install(root)
    assert (site / "yt_dlp" / "version.py").is_file()
    assert (site / "yt_dlp_ejs" / "__init__.py").is_file()
    assert not (root / "2099.1.2.part").exists()

    assert ytdlp_update.activate(root) == "2099.1.2"
    assert sys.path[0] == str(site)
    assert ytdlp_update.active_version == "2099.1.2"
    assert not (root / "2000.1.1").exists()   # 이전 업데이트는 정리


def test_activate_ignores_update_from_other_app_version(tmp_path, clean_path):
    root = tmp_path / "yt-dlp"
    _install(root)
    state = root / "current.json"
    state.write_text(json.dumps({"version": "2099.1.2", "app_version": VERSION + ".old"}))
    assert ytdlp_update.activate(root) is None
    assert not root.exists()


def test_activate_without_update(tmp_path, clean_path):
    before = list(sys.path)
    assert ytdlp_update.activate(tmp_path / "yt-dlp") is None
    assert sys.path == before


def test_install_rejects_bad_checksum(tmp_path):
    data, _ = _wheel({"yt_dlp/__init__.py": ""})
    with pytest.raises(UpdateFailed):
        ytdlp_update.install_wheels(tmp_path, "2099.1.2", [("yt_dlp.whl", data, "0" * 64)])
    assert not (tmp_path / "2099.1.2").exists()
    assert not (tmp_path / "current.json").exists()


def test_install_rejects_path_outside(tmp_path):
    data, sha = _wheel({"../evil.py": ""})
    with pytest.raises(UpdateFailed):
        ytdlp_update.install_wheels(tmp_path / "root", "2099.1.2", [("bad.whl", data, sha)])
    assert not (tmp_path / "evil.py").exists()


def test_already_latest_skips_download(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ytdlp_update, "current_version", lambda: "2099.01.02")
    monkeypatch.setattr(ytdlp_update, "_pypi", lambda *a: {"info": {"version": "2099.1.2"}})
    res = ytdlp_update.run_update(root=tmp_path)
    assert not res.updated
    assert not any(tmp_path.iterdir())


def test_frozen_update_downloads_wheels(tmp_path, monkeypatch, clean_path):
    data, sha = _wheel({"yt_dlp/__init__.py": "", "yt_dlp/version.py": "__version__ = '2099.01.02'\n"})
    ejs, ejs_sha = _wheel({"yt_dlp_ejs/__init__.py": ""})
    meta = {
        "yt-dlp": {"info": {"version": "2099.1.2", "requires_python": ">=3.10",
                            "requires_dist": ['yt-dlp-ejs==9.9.9; extra == "default"']},
                   "urls": [{"packagetype": "sdist", "filename": "yt_dlp-2099.1.2.tar.gz"},
                            {"packagetype": "bdist_wheel", "filename": "yt_dlp-2099.1.2-py3-none-any.whl",
                             "url": "u/yt", "digests": {"sha256": sha}}]},
        "yt-dlp-ejs": {"info": {"version": "9.9.9"},
                       "urls": [{"packagetype": "bdist_wheel", "filename": "yt_dlp_ejs-9.9.9-py3-none-any.whl",
                                 "url": "u/ejs", "digests": {"sha256": ejs_sha}}]},
    }
    asked = []

    def fake_pypi(name, version, compat):
        asked.append((name, version))
        return meta[name]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ytdlp_update, "current_version", lambda: "2026.08.19")
    monkeypatch.setattr(ytdlp_update, "_pypi", fake_pypi)
    monkeypatch.setattr(ytdlp_update, "_get", lambda url, compat, timeout=30: {"u/yt": data, "u/ejs": ejs}[url])
    logs = []
    res = ytdlp_update.run_update(on_log=logs.append, root=tmp_path)
    assert (res.before, res.after, res.updated) == ("2026.08.19", "2099.1.2", True)
    assert asked == [("yt-dlp", ""), ("yt-dlp-ejs", "9.9.9")]
    assert any("yt_dlp_ejs-9.9.9" in s for s in logs)
    assert ytdlp_update.activate(tmp_path) == "2099.1.2"


def test_network_error_is_friendly(tmp_path, monkeypatch):
    def offline(*a, **k):
        raise OSError("<urlopen error [Errno -3] Temporary failure in name resolution>")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ytdlp_update, "current_version", lambda: "2026.08.19")
    monkeypatch.setattr(ytdlp_update, "_get", offline)
    with pytest.raises(UpdateFailed) as e:
        ytdlp_update.run_update(root=tmp_path)
    assert "인터넷 연결" in e.value.message
