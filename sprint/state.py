"""session state for ps/kill - tracks running sprint agents"""
import os, json, time, signal, pathlib, psutil

SESSION_DIR = pathlib.Path.home() / ".sprint" / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

def _sess_file(sid: str):
    return SESSION_DIR / f"{sid}.json"

def register_session(task: str, model: str, pid: int = None) -> str:
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

def unregister_session(sid: str):
    try:
        _sess_file(sid).unlink()
    except:
        pass

def list_sessions():
    sessions = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            # check alive
            pid = d.get("pid")
            alive = False
            try:
                os.kill(pid, 0)
                alive = True
            except:
                alive = False
            # stale if dead > 1h
            if not alive and time.time() - d.get("started",0) > 3600:
                f.unlink(missing_ok=True)
                continue
            d["alive"] = alive
            sessions.append(d)
        except:
            f.unlink(missing_ok=True)
    return sorted(sessions, key=lambda x: x["started"], reverse=True)

def kill_session(sid_or_pid: str) -> str:
    # try as id
    f = _sess_file(sid_or_pid)
    if f.exists():
        try:
            d = json.loads(f.read_text())
            os.kill(d["pid"], signal.SIGTERM)
            time.sleep(0.3)
            # force if still
            try:
                os.kill(d["pid"], 0)
                os.kill(d["pid"], signal.SIGKILL)
            except:
                pass
            f.unlink(missing_ok=True)
            return f"killed {sid_or_pid} (pid {d['pid']})"
        except Exception as e:
            return f"err killing {sid_or_pid}: {e}"
    # try as pid
    try:
        pid = int(sid_or_pid)
        os.kill(pid, signal.SIGTERM)
        return f"killed pid {pid}"
    except Exception as e:
        return f"not found: {sid_or_pid} ({e})"
