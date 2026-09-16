"""Own-process-tree CPU boundary, independent of observational monitoring.

Linux subreaping retains orphan descendants (including setsid children). Usage
includes waited children and live descendants. A lost execution receipt keeps
its full reservation; neither a restart nor lost usage creates free CPU.
"""

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import sys
import time

from kinetic_agents.core.storage import atomic


def envelope(total):
    if total <= 0:
        raise ValueError("positive CPU total required")
    return {
        "total": total,
        "science": total * 0.90,
        "remote": total * 0.95,
        "local": total * 0.05,
        "version": "approved-A-90-5-5-v1",
    }


class NamespaceSafetyError(RuntimeError):
    """Proc visibility or signal identity cannot support safe CPU enforcement."""


_SAFE_NAMESPACE_REASONS = frozenset(
    {
        "procfs lacks a usable NSpid identity",
        "procfs and syscall PID identity cannot be reconciled",
        "legacy SIGCHLD ABI is not qualified on this platform",
        "self identity disappeared during qualification",
        "cross-namespace signaling requires verified pidfd support",
        "legacy cleanup requires default SIGCHLD, never an auto-reaper",
        "legacy cleanup rejects inherited SIGCHLD handler/SA_NOCLDWAIT",
        "waitid identity pins require a dedicated single-threaded wrapper",
        "kernel child identity qualification is unavailable",
        "legacy cleanup requires an enabled Linux subreaper",
        "owned descendant has no signal namespace mapping",
        "owned descendant proc identity changed",
        "procfs exposes no task list for an owned process",
        "procfs task/children capability is unavailable",
        "pidfd does not identify the sampled owned process",
        "pidfd readiness does not establish a valid process handle",
        "PID was reused before ownership could be pinned",
        "supervisor identity changed",
        "supervisor proc visibility lost",
        "ambiguous local PID in owned descendant tree",
        "legacy child enumeration disagrees with direct-parent identity",
        "ECHILD: PID is not our unreaped direct child; no signal sent",
        "waitid child pin was lost before signaling",
        "only this supervisor may own the signal tree",
        "owned descendants could not be reaped within cleanup bound",
        "dedicated supervisor must start without existing live children",
        "cannot safely account or signal descendants in this proc namespace",
        "cleanup incomplete; full execution reservation preserved",
    }
)


def _error_reason(error):
    """Only fixed internal diagnostics; never interpolate command/env/OS text."""
    if isinstance(error, NamespaceSafetyError):
        return (
            str(error)
            if str(error) in _SAFE_NAMESPACE_REASONS
            else "unclassified namespace safety failure"
        )
    return None


def _pidfd_exited(fd):
    """Kernel terminal readiness, never invalid-descriptor/unknown events."""
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(mask & (select.POLLNVAL | select.POLLERR) for _, mask in events):
        raise NamespaceSafetyError("pidfd readiness does not establish a valid process handle")
    return any(mask & (select.POLLIN | select.POLLHUP) for _, mask in events)


def _status(path):
    return {
        key: value.strip()
        for line in path.read_text().splitlines()
        if ":" in line
        for key, value in (line.split(":", 1),)
    }


def namespace_identity(proc=Path("/proc")):
    """Distinguish procfs-view PIDs from this caller's syscall PID namespace."""
    own = _status(proc / "self/status")
    try:
        proc_pid = int(own["Pid"])
        chain = (
            [int(value) for value in own["NSpid"].split()]
            if "NSpid" in own
            else [proc_pid] if proc_pid == os.getpid() else []
        )
    except (KeyError, ValueError):
        raise NamespaceSafetyError("procfs lacks a usable NSpid identity") from None
    if not chain or chain[0] != proc_pid or chain[-1] != os.getpid():
        raise NamespaceSafetyError("procfs and syscall PID identity cannot be reconciled")
    return {
        "proc_pid": proc_pid,
        "signal_pid": os.getpid(),
        "namespace_depth": len(chain) - 1,
        "nspid": chain,
        "mapped_proc_namespace": proc_pid != os.getpid(),
    }


def _sigchld_action():
    """Read glibc Linux x86-64 sigaction, including hidden SA_NOCLDWAIT.

    The qualified CentOS7 fallback target uses this ABI. Other architectures
    retain pidfd mode; we do not guess a legacy sigaction layout there.
    """
    if (
        os.uname().sysname != "Linux"
        or os.uname().machine not in ("x86_64", "amd64")
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise NamespaceSafetyError("legacy SIGCHLD ABI is not qualified on this platform")

    class Action(ctypes.Structure):
        _fields_ = [
            ("handler", ctypes.c_void_p),
            ("mask", ctypes.c_ulong * 16),
            ("flags", ctypes.c_int),
            ("restorer", ctypes.c_void_p),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    result = Action()
    if libc.sigaction(signal.SIGCHLD, None, ctypes.byref(result)) != 0:
        raise OSError(ctypes.get_errno(), "cannot qualify inherited SIGCHLD action")
    return result.handler, result.flags


class ProcessTree:
    """Own descendants only, with proc-view traversal and verified pidfd signals.

    ``/proc`` can be mounted from an ancestor PID namespace. Never index it by a
    local Popen.pid, or send signals to its directory names. Descendant ownership
    is established through task/children and PPid in the SAME proc view; only
    then is NSpid used to translate to local syscall PIDs.
    """

    def __init__(self, proc=Path("/proc")):
        self.proc = Path(proc)
        self.identity = namespace_identity(self.proc)
        self.root = self.identity["signal_pid"]
        self.hz = os.sysconf("SC_CLK_TCK")
        self.handles = {}
        self.signal_mode = "verified_pidfd"
        row = self._row(self.identity["proc_pid"])
        self.root_start = row["start"]
        # Fail BEFORE spawning if namespace translation, children visibility or
        # Linux pidfd signaling is not supported in this deployment.
        self._children(self.identity["proc_pid"])
        try:
            fd = self._pidfd(row)
            if fd is None:
                raise NamespaceSafetyError("self identity disappeared during qualification")
            try:
                signal.pidfd_send_signal(fd, 0)
            finally:
                os.close(fd)
        except (AttributeError, NotImplementedError):
            self._legacy_fallback()
        except OSError as exc:
            if exc.errno not in (errno.ENOSYS, errno.EINVAL):
                raise
            self._legacy_fallback()

    def _legacy_fallback(self):
        if self.identity["mapped_proc_namespace"]:
            raise NamespaceSafetyError("cross-namespace signaling requires verified pidfd support")
        self._assert_waitid_environment()
        self.signal_mode = "waitid_direct_children"

    def _assert_waitid_environment(self, *, require_subreaper=False):
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise NamespaceSafetyError(
                "legacy cleanup requires default SIGCHLD, never an auto-reaper"
            )
        handler, flags = _sigchld_action()
        if handler is not None or flags & 2:  # Linux SA_NOCLDWAIT
            raise NamespaceSafetyError(
                "legacy cleanup rejects inherited SIGCHLD handler/SA_NOCLDWAIT"
            )
        tasks = [path for path in (self.proc / "self/task").iterdir() if path.name.isdigit()]
        if len(tasks) != 1:
            raise NamespaceSafetyError(
                "waitid identity pins require a dedicated single-threaded wrapper"
            )
        if not all(
            hasattr(os, name) for name in ("waitid", "WNOWAIT", "WNOHANG", "WEXITED", "P_PID")
        ):
            raise NamespaceSafetyError("kernel child identity qualification is unavailable")
        if require_subreaper:
            enabled = ctypes.c_int()
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(37, ctypes.byref(enabled), 0, 0, 0) != 0 or enabled.value != 1:
                raise NamespaceSafetyError("legacy cleanup requires an enabled Linux subreaper")

    def _row(self, proc_pid):
        path = self.proc / str(proc_pid)
        text = (path / "stat").read_text()
        fields = text.rsplit(")", 1)[1].split()
        row_pid = int(text.split("(", 1)[0].strip())
        status = _status(path / "status")
        try:
            chain = (
                [int(value) for value in status["NSpid"].split()]
                if "NSpid" in status
                else [proc_pid] if not self.identity["mapped_proc_namespace"] else []
            )
            local = chain[self.identity["namespace_depth"]]
        except (KeyError, ValueError, IndexError):
            # procfs reads are not an atomic snapshot. If the kernel has now
            # removed this exact proc entry, let the caller handle a departed
            # task, rather than misclassifying teardown text as a live mapping.
            (path / "status").stat()
            raise NamespaceSafetyError("owned descendant has no signal namespace mapping") from None
        if (
            row_pid != proc_pid
            or int(status["Pid"]) != proc_pid
            or not chain
            or chain[0] != proc_pid
            or local <= 0
        ):
            (path / "status").stat()
            raise NamespaceSafetyError("owned descendant proc identity changed")
        return {
            "proc_pid": proc_pid,
            "signal_pid": local,
            "proc_parent": int(fields[1]),
            "start": fields[19],
            "cpu": sum(float(fields[i]) for i in (11, 12, 13, 14)) / self.hz,
        }

    def _children(self, proc_pid):
        # Children may be created by any thread, not just the group leader.
        children = set()
        tasks = [
            task for task in (self.proc / str(proc_pid) / "task").iterdir() if task.name.isdigit()
        ]
        if not tasks:
            # Empty task iteration also races with final process removal. A
            # STILL PRESENT process with no visible tasks remains fail-closed.
            (self.proc / str(proc_pid) / "status").stat()
            raise NamespaceSafetyError("procfs exposes no task list for an owned process")
        for task in tasks:
            if not task.name.isdigit():
                continue
            try:
                children.update(int(value) for value in (task / "children").read_text().split())
            except FileNotFoundError:
                # A thread may exit between listing and reading its children.
                # Root children are reparented to its surviving thread/subreaper.
                if not task.exists():
                    continue
                raise NamespaceSafetyError(
                    "procfs task/children capability is unavailable"
                ) from None
        return children

    def _pidfd(self, row):
        fd = None
        try:
            fd = os.pidfd_open(row["signal_pid"])
            info = _status(self.proc / "self/fdinfo" / str(fd))
            # A short-lived owned grandchild can be reaped by its parent after
            # pidfd_open but before fdinfo. Linux then reports Pid/NSpid=-1.
            # This is not PID reuse: require terminal readiness of THAT pinned
            # descriptor before discarding it. Never infer exit from -1 alone,
            # from another PID mapping, or from an invalid file descriptor.
            if info.get("Pid") == "-1" and _pidfd_exited(fd):
                os.close(fd)
                return None
            chain = (
                [int(value) for value in info["NSpid"].split()]
                if "NSpid" in info
                else (
                    [int(info.get("Pid", "-1"))]
                    if not self.identity["mapped_proc_namespace"]
                    else []
                )
            )
            if (
                int(info.get("Pid", "-1")) != row["proc_pid"]
                or len(chain) <= self.identity["namespace_depth"]
                or chain[self.identity["namespace_depth"]] != row["signal_pid"]
            ):
                raise NamespaceSafetyError("pidfd does not identify the sampled owned process")
            current = self._row(row["proc_pid"])
            if current["start"] != row["start"] or current["signal_pid"] != row["signal_pid"]:
                raise NamespaceSafetyError("PID was reused before ownership could be pinned")
            return fd
        except (FileNotFoundError, ProcessLookupError):
            if fd is not None:
                os.close(fd)
            return None
        except OSError as error:
            if fd is not None:
                os.close(fd)
            # Some kernels detach the TGID during reaping just before removing
            # the proc entry, so pidfd_open can return EINVAL instead of ESRCH.
            # EINVAL itself is NOT exit proof or a reason to relax namespace
            # qualification. Ignore it only when this exact proc entry is gone.
            if error.errno == errno.EINVAL:
                try:
                    (self.proc / str(row["proc_pid"]) / "status").stat()
                except FileNotFoundError:
                    return None
            raise
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise

    def snapshot(self):
        root = self._row(self.identity["proc_pid"])
        if root["start"] != self.root_start or root["signal_pid"] != self.root:
            raise NamespaceSafetyError("supervisor identity changed")
        root["parent"] = 0
        rows = {self.root: root}
        queue = [root]
        seen = {root["proc_pid"]}
        for parent in queue:
            try:
                children = self._children(parent["proc_pid"])
            except (FileNotFoundError, ProcessLookupError):
                if parent["signal_pid"] == self.root:
                    raise NamespaceSafetyError("supervisor proc visibility lost") from None
                continue
            for proc_pid in children:
                if proc_pid in seen:
                    continue
                try:
                    row = self._row(proc_pid)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                # Reparenting raced with enumeration. Revisit through the actual
                # parent/subreaper on the next sample; do not infer ownership.
                if row["proc_parent"] != parent["proc_pid"]:
                    continue
                if row["signal_pid"] in rows:
                    raise NamespaceSafetyError("ambiguous local PID in owned descendant tree")
                row["parent"] = parent["signal_pid"]
                seen.add(proc_pid)
                rows[row["signal_pid"]] = row
                queue.append(row)
        self._capture(rows)
        return rows

    def _capture(self, rows):
        if self.signal_mode == "waitid_direct_children":
            return  # Kernel waitid pins will be taken only for direct children.
        for key, fd in list(self.handles.items()):
            if _pidfd_exited(fd):
                os.close(fd)
                del self.handles[key]
        for pid, row in rows.items():
            if pid == self.root:
                continue
            key = (pid, row["start"])
            if key not in self.handles:
                fd = self._pidfd(row)
                if fd is not None:
                    self.handles[key] = fd

    def kill_known(self):
        if self.signal_mode == "waitid_direct_children":
            return self._kill_waitable_children()
        killed = []
        for (pid, _), fd in self.handles.items():
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        return killed

    def _kill_waitable_children(self):
        """Safe legacy fallback, NOT starttime-check-then-kill.

        Successful waitid(P_PID,...WNOWAIT), including None for a live child,
        proves the PID belongs to this parent's unreaped child. A live child or
        its zombie cannot be recycled. With one thread and no SIGCHLD reaper,
        there is no reap between this kernel proof and kill(). Grandchildren
        are NEVER killed from proc data: kill parents first, then require a new
        waitid proof after Linux reparents their children to this subreaper.
        """
        self._assert_waitid_environment(require_subreaper=True)
        killed = []
        for proc_pid in self._children(self.identity["proc_pid"]):
            try:
                row = self._row(proc_pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            pid = row["signal_pid"]
            if row["proc_parent"] != self.identity["proc_pid"] or pid == self.root:
                raise NamespaceSafetyError(
                    "legacy child enumeration disagrees with direct-parent identity"
                )
            try:
                ready = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                raise NamespaceSafetyError(
                    "ECHILD: PID is not our unreaped direct child; no signal sent"
                ) from None
            if ready is not None:
                continue  # Zombie is still pinned; the caller reaps it later.
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                # A default-SIGCHLD unreaped child cannot disappear/recycle here.
                raise NamespaceSafetyError("waitid child pin was lost before signaling") from None
        return killed

    def close(self):
        for fd in self.handles.values():
            os.close(fd)
        self.handles.clear()


def proc_table():
    """Compatibility read: local-PID-keyed OWN tree, not a global process list."""
    tree = ProcessTree()
    try:
        return tree.snapshot()
    finally:
        tree.close()


def descendants(rows, root):
    found = {root}
    while True:
        new = {pid for pid, row in rows.items() if row["parent"] in found}
        if new <= found:
            return found
        found |= new


def tree_cpu(rows, root):
    return sum(rows[p]["cpu"] for p in descendants(rows, root) if p in rows)


def kill_descendants(root):
    if root != os.getpid():
        raise NamespaceSafetyError("only this supervisor may own the signal tree")
    tree = ProcessTree()
    try:
        tree.snapshot()
        return sorted(tree.kill_known())
    finally:
        tree.close()


def _cleanup(tree, child, timeout=3.0):
    """Bounded kill/reap; never call an unbounded child.wait on mapping failure."""
    until = time.monotonic() + timeout
    seen_error = None
    while True:
        try:
            tree.snapshot()
        except (OSError, NamespaceSafetyError) as exc:
            seen_error = exc
        try:
            tree.kill_known()  # Existing pidfds stay safe even if proc visibility fails.
        except (OSError, NamespaceSafetyError) as exc:
            seen_error = exc
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return seen_error
            if not pid:
                break
            if child is not None and pid == child.pid:
                child.returncode = os.waitstatus_to_exitcode(status)
        if time.monotonic() >= until:
            return seen_error or NamespaceSafetyError(
                "owned descendants could not be reaped within cleanup bound"
            )
        time.sleep(0.02)


def run(command, receipt, limit, cores, deadline, terminal_file=None, *, execution_binding=None):
    receipt = Path(receipt)
    if receipt.exists():
        raise PermissionError("execution receipt cannot be reused")
    if execution_binding is not None:
        # Optional trusted-host provenance. This is NOT a sandbox or authority
        # to run model-written code on the host. Legacy callers are unchanged.
        from kinetic_agents.team_execution import verify_dispatch

        execution_binding = verify_dispatch(
            execution_binding, command, receipt, limit, deadline, claim=True
        )
    tree = None
    try:
        tree = ProcessTree()
        initial = tree.snapshot()
        if len(initial) != 1:
            tree.close()
            raise NamespaceSafetyError(
                "dedicated supervisor must start without existing live children"
            )
    except (OSError, AttributeError, NamespaceSafetyError) as exc:
        if tree is not None:
            tree.close()
        atomic(
            receipt,
            {
                "status": "QUALIFICATION_FAILED",
                "reason": "proc_namespace_unqualified",
                "reserved_seconds": limit,
                "cpu_seconds": 0.0,
                "pid": os.getpid(),
                "qualification_error": type(exc).__name__,
                "qualification_error_reason": _error_reason(exc),
                "child_started": False,
                **(
                    {"execution_binding": execution_binding}
                    if execution_binding is not None
                    else {}
                ),
            },
        )
        raise NamespaceSafetyError(
            "cannot safely account or signal descendants in this proc namespace"
        ) from exc
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        tree.close()
        raise OSError(ctypes.get_errno(), "subreaper unavailable")
    allowed = sorted(os.sched_getaffinity(0))[:cores]
    if not allowed:
        raise RuntimeError("no CPUs allowed")
    os.sched_setaffinity(0, allowed)
    # This trusted wrapper enforces the aggregate limit itself. Do not inherit
    # the RPC's short soft limit as a lifetime limit for the scientific broker.
    _, inherited_hard = resource.getrlimit(resource.RLIMIT_CPU)
    resource.setrlimit(resource.RLIMIT_CPU, (inherited_hard, inherited_hard))
    root = os.getpid()
    started = time.time()
    high = 0.0
    baseline_tree = tree_cpu(initial, root)
    own_before = resource.getrusage(resource.RUSAGE_SELF)
    kids_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    baseline_usage = (
        own_before.ru_utime + own_before.ru_stime + kids_before.ru_utime + kids_before.ru_stime
    )
    row = {
        "pid": root,
        "started": started,
        "reserved_seconds": limit,
        "cpu_seconds": 0.0,
        "status": "ACTIVE",
        "cores": len(allowed),
        "proc_namespace": tree.identity,
        "signal_identity": tree.signal_mode,
    }
    if execution_binding is not None:
        row["execution_binding"] = execution_binding
    atomic(receipt, row)  # Persist the full ceiling BEFORE child creation.
    child = None
    reason = "child_exit"
    failure = None
    # Never SIGKILL this accounting owner to stop science. A trusted sibling
    # stop receipt or scheduler SIGTERM asks it to reap children, then settle.
    stop_file = receipt.with_suffix(".stop")
    interrupted = []
    old_handlers = {}

    def request_stop(signum, _frame):
        interrupted.append(signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, request_stop)
        child = subprocess.Popen(command)
        # Popen's unreaped direct child is an authoritative local PID even if
        # proc visibility fails immediately afterwards. Retain a safe cleanup
        # handle before the first descendant sample.
        if tree.signal_mode == "verified_pidfd":
            tree.handles[(child.pid, "direct-Popen-child")] = os.pidfd_open(child.pid)
        while True:
            high = max(high, tree_cpu(tree.snapshot(), root) - baseline_tree)
            row.update(cpu_seconds=high, updated=time.time(), child_pid=child.pid)
            atomic(receipt, row)
            if interrupted or stop_file.exists():
                reason = "stop_requested"
                break
            if high >= limit - max(4.0, cores * 2):
                reason = "cpu_limit"
                break
            if time.time() >= deadline:
                reason = "deadline"
                break
            if terminal_file and Path(terminal_file).exists():
                reason = "terminal_artifacts"
                break
            if child.poll() is not None:
                break
            time.sleep(0.25)
    except BaseException as exc:
        failure = exc
        reason = "supervisor_error"
    finally:
        cleanup_error = _cleanup(tree, child)
        tree.close()
        own = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        actual = max(
            0.0, own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime - baseline_usage
        )
        row.update(
            status="FAILED_UNCERTAIN" if cleanup_error else "SETTLED",
            cpu_seconds=max(high, actual),
            reason=reason,
            finished=time.time(),
            returncode=child.returncode if child else None,
        )
        if cleanup_error:
            row.update(
                cleanup_error=type(cleanup_error).__name__,
                cleanup_error_reason=_error_reason(cleanup_error),
                full_reservation_preserved=True,
            )
        if failure is not None:
            row["supervisor_error"] = type(failure).__name__
            row["supervisor_error_reason"] = _error_reason(failure)
        atomic(receipt, row)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    if failure is not None:
        raise failure
    if cleanup_error:
        raise NamespaceSafetyError(
            "cleanup incomplete; full execution reservation preserved"
        ) from cleanup_error
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--limit", type=float, required=True)
    p.add_argument("--cores", type=int, required=True)
    p.add_argument("--deadline", type=float, required=True)
    p.add_argument("--terminal-file")
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    row = run(command, a.receipt, a.limit, a.cores, a.deadline, a.terminal_file)
    sys.exit(row["returncode"] if row["reason"] == "child_exit" and row["returncode"] >= 0 else 0)


if __name__ == "__main__":
    main()
