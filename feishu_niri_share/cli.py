"""Build, configure and restore a user-level ScreenCast portal backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
OUTPUT_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
SCREENCAST_KEY = re.compile(r"^\s*org\.freedesktop\.impl\.portal\.ScreenCast\s*=")


def user_paths() -> dict[str, Path]:
    """Return XDG paths managed by this project for the current user."""
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    prefix = home / ".local"
    return {
        "portal_config": config / "xdg-desktop-portal/niri-portals.conf",
        "wlr_config": config / "xdg-desktop-portal-wlr/config",
        "binary": prefix / "libexec/xdg-desktop-portal-wlr",
        "dbus_service": data / "dbus-1/services/org.freedesktop.impl.portal.desktop.wlr.service",
        "portal_descriptor": data / "xdg-desktop-portal/portals/wlr.portal",
        "state": state / "feishu-niri-share",
        "prefix": prefix,
        "data": data,
    }


def sha256(path: Path) -> str | None:
    """Hash a managed file, or return None when it is absent."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portal_with_wlr(original: str) -> str:
    """Select wlr for ScreenCast while preserving unrelated portal settings."""
    lines = original.splitlines(keepends=True)
    result: list[str] = []
    in_preferred = False
    preferred_found = False
    key_written = False
    for line in lines:
        if line.lstrip().startswith("[") and "]" in line:
            if in_preferred and not key_written:
                result.append("org.freedesktop.impl.portal.ScreenCast=wlr;\n")
                key_written = True
            in_preferred = line.strip() == "[preferred]"
            if in_preferred:
                preferred_found = True
        if in_preferred and SCREENCAST_KEY.match(line):
            if not key_written:
                result.append("org.freedesktop.impl.portal.ScreenCast=wlr;\n")
                key_written = True
            continue
        result.append(line)
    if in_preferred and not key_written:
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append("org.freedesktop.impl.portal.ScreenCast=wlr;\n")
    if not preferred_found:
        return "[preferred]\ndefault=gnome;gtk;\norg.freedesktop.impl.portal.ScreenCast=wlr;\n" + original
    return "".join(result)


def wlr_configuration(output: str, fps: int) -> str:
    """Create the portal backend's explicit monitor capture configuration."""
    if not OUTPUT_NAME.fullmatch(output):
        raise ValueError("显示器名只能包含字母、数字、点、下划线和连字符")
    if not 1 <= fps <= 120:
        raise ValueError("帧率必须在 1 到 120 之间")
    return f"[screencast]\noutput_name={output}\nchooser_type=none\nmax_fps={fps}\n"


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a config file atomically after creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(content)
        temp_name = temp.name
    os.chmod(temp_name, 0o644)
    os.replace(temp_name, path)


def run(*args: str) -> None:
    """Run a required build command and raise on failure."""
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def restart_portal() -> None:
    """Ask the user systemd instance to reload portal configuration."""
    completed = subprocess.run(
        ["systemctl", "--user", "restart", "xdg-desktop-portal.service"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        print("提示：门户未能自动重启，请在桌面终端运行 systemctl --user restart xdg-desktop-portal.service", file=sys.stderr)
        if completed.stderr:
            print(completed.stderr.strip(), file=sys.stderr)


def snapshot(paths: dict[str, Path]) -> dict:
    """Save originals before installation so uninstall can restore them."""
    state_dir = paths["state"]
    state_dir.mkdir(parents=True)
    records = {}
    for key in ("portal_config", "wlr_config", "binary", "dbus_service", "portal_descriptor"):
        target = paths[key]
        existed = target.is_file()
        if existed:
            shutil.copy2(target, state_dir / f"{key}.original")
        records[key] = {"existed": existed, "installed_sha256": None}
    return {"version": 1, "files": records}


def save_state(paths: dict[str, Path], state: dict) -> None:
    write_text_atomic(paths["state"] / "state.json", json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def restore(paths: dict[str, Path], state: dict, *, check_hash: bool) -> list[str]:
    """Restore original files; leave user-edited installed files untouched."""
    skipped = []
    for key, record in state["files"].items():
        target = paths[key]
        installed_hash = record["installed_sha256"]
        if check_hash and installed_hash is not None and sha256(target) != installed_hash:
            skipped.append(str(target))
            continue
        if record["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths["state"] / f"{key}.original", target)
        else:
            target.unlink(missing_ok=True)
    return skipped


def install(output: str, fps: int) -> None:
    """Build bundled backend, install it to ~/.local and route niri ScreenCast."""
    config = wlr_configuration(output, fps)
    paths = user_paths()
    if paths["state"].exists():
        raise RuntimeError(f"已有安装状态：{paths['state']}；请先运行 uninstall")
    for program in ("meson", "ninja", "pkg-config", "systemctl"):
        if shutil.which(program) is None:
            raise RuntimeError(f"缺少构建或管理命令：{program}")
    source = PACKAGE_ROOT / "backend"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    build = cache / "feishu-niri-share/build"
    if not (source / "meson.build").is_file():
        raise RuntimeError("缺少包内 backend/meson.build；项目源码不完整")
    build.parent.mkdir(parents=True, exist_ok=True)
    if build.exists():
        run("meson", "setup", "--reconfigure", str(build), str(source),
            f"--prefix={paths['prefix']}", f"--datadir={paths['data']}",
            "-Dsystemd=disabled", "-Dman-pages=disabled")
    else:
        run("meson", "setup", str(build), str(source),
            f"--prefix={paths['prefix']}", f"--datadir={paths['data']}",
            "-Dsystemd=disabled", "-Dman-pages=disabled")
    run("meson", "compile", "-C", str(build))

    state = snapshot(paths)
    save_state(paths, state)
    try:
        run("meson", "install", "-C", str(build))
        original = paths["portal_config"].read_text(encoding="utf-8") if paths["portal_config"].exists() else ""
        write_text_atomic(paths["portal_config"], portal_with_wlr(original))
        write_text_atomic(paths["wlr_config"], config)
        for key in state["files"]:
            state["files"][key]["installed_sha256"] = sha256(paths[key])
        save_state(paths, state)
    except Exception:
        restore(paths, state, check_hash=False)
        shutil.rmtree(paths["state"])
        raise
    restart_portal()
    print(f"安装完成：niri ScreenCast → wlr → PipeWire → 飞书；显示器 {output}，{fps} FPS")


def uninstall() -> None:
    """Restore backed-up files when they have not changed since installation."""
    paths = user_paths()
    state_file = paths["state"] / "state.json"
    if not state_file.is_file():
        raise RuntimeError(f"没有安装记录：{state_file}")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    skipped = restore(paths, state, check_hash=True)
    if skipped:
        print("以下文件安装后被修改，已保留，请手动处理：", *skipped, sep="\n", file=sys.stderr)
        print(f"备份与状态保留在 {paths['state']}", file=sys.stderr)
    else:
        shutil.rmtree(paths["state"])
    restart_portal()
    print("已恢复安装前的用户级配置和文件")


def status() -> None:
    """Print read-only checks for the bundled backend and current user config."""
    paths = user_paths()
    portal = paths["portal_config"].read_text(encoding="utf-8") if paths["portal_config"].is_file() else ""
    print("后端程序：", paths["binary"], "存在" if paths["binary"].is_file() else "缺失")
    print("Portal 描述：", paths["portal_descriptor"], "存在" if paths["portal_descriptor"].is_file() else "缺失")
    wlr_selected = any(SCREENCAST_KEY.match(line) and line.split("=", 1)[1].strip().rstrip(";") == "wlr"
                       for line in portal.splitlines())
    print("niri ScreenCast：", "wlr" if wlr_selected else "未配置")
    print("采集配置：", paths["wlr_config"])
    if paths["wlr_config"].is_file():
        print(paths["wlr_config"].read_text(encoding="utf-8").strip())
    print("安装记录：", "存在" if (paths["state"] / "state.json").is_file() else "不存在（可能为手动配置）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="飞书会议的 niri 整屏共享后端")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="构建并安装用户级共享后端")
    install_parser.add_argument("--output", required=True, help="niri 显示器名，例如 eDP-1")
    install_parser.add_argument("--fps", type=int, default=30, help="最高帧率，默认 30")
    commands.add_parser("status", help="只读检查当前配置")
    commands.add_parser("uninstall", help="恢复安装前的文件与配置")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install(args.output, args.fps)
        elif args.command == "status":
            status()
        else:
            uninstall()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0
