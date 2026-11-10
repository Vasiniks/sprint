"""session state for ps/kill - tracks running sprint agents"""
import json
import os
import pathlib
import signal
import time

SESSION_DIR = pathlib.Path.home() / ".sprint" / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _sess_file(sid: str) -> pathlib.Path:
    return SESSION_DIR / f"{sid}.json"


def register_session(task: str, model: str, pid: int | None = None) -> str:
    sid = f"{int(time.time()*1000)%1000000:06d}"
    pid = pid or os.getpid()
    data = {
        "id": sid,
        "pid": pid,
        "task": task[:120],
        "model": model,
        "started": time.time(),
        "cwd": str(pathlib.Path.cwd()),
    }
    _sess_file(sid).write_text(json.dumps(data))
    return sid


def unregister_session(sid: str) -> None:
    try:
        _sess_file(sid).unlink(missing_ok=True)
    except Exception:
        pass


def list_sessions() -> list[dict]:
    sessions: list[dict] = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            pid = d.get("pid", 0)
            alive = False
            try:
                os.kill(pid, 0)
                alive = True
            except Exception:
                alive = False
            # stale dead >1h -> clean
            if not alive and time.time() - d.get("started", 0) > 3600:
                f.unlink(missing_ok=True)
                continue
            d["alive"] = alive
            sessions.append(d)
        except Exception:
            f.unlink(missing_ok=True)
    return sorted(sessions, key=lambda x: x["started"], reverse=True)


def kill_session(sid_or_pid: str) -> str:
    f = _sess_file(sid_or_pid)
    if f.exists():
        try:
            d = json.loads(f.read_text())
            os.kill(d["pid"], signal.SIGTERM)
            time.sleep(0.3)
            try:
                os.kill(d["pid"], 0)
                os.kill(d["pid"], signal.SIGKILL)
            except Exception:
                pass
            f.unlink(missing_ok=True)
            return f"killed {sid_or_pid} (pid {d['pid']})"
        except Exception as e:
            return f"err killing {sid_or_pid}: {e}"
    try:
        pid = int(sid_or_pid)
        os.kill(pid, signal.SIGTERM)
        return f"killed pid {pid}"
    except Exception as e:
        return f"not found: {sid_or_pid} ({e})"
