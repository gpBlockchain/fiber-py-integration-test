"""
A simple HTTP reverse proxy for CKB RPC that can be programmatically
toggled between forwarding and blocking modes.

When blocked, the proxy shuts down its listening socket so that clients
receive "Connection refused" errors — the same class of transient network
error that testnet.ckbapp.dev produced in issue #1189.

Additionally supports ``reject_next(n)`` which accepts TCP connections
but immediately closes them without sending any HTTP response, simulating
the "Connection reset by peer" error from the original issue.

Specific JSON-RPC methods can also be blocked with configurable error
codes/messages, which is useful for simulating permanent CKB RPC failures.
"""

import http.server
import json
import socket
import struct
import threading
import urllib.request
import logging

logger = logging.getLogger(__name__)


class CkbRpcProxy:
    """HTTP reverse proxy sitting between Fiber nodes and CKB RPC.

    Usage:
        proxy = CkbRpcProxy("http://127.0.0.1:8114")
        proxy.start()           # pick a random free port
        print(proxy.url)        # e.g. "http://127.0.0.1:54321"
        proxy.block()           # stop accepting connections
        proxy.unblock()         # resume forwarding
        proxy.reject_next(3)    # next 3 requests get connection-reset
        proxy.stop()            # tear down
    """

    def __init__(self, target_url: str):
        self.target_url = target_url
        self._server = None
        self._thread = None
        self.port = None
        self.url = None
        self._reject_remaining = 0
        self._method_errors = {}
        self._block_after = None  # block after N forwarded requests
        self._forwarded_count = 0
        self._forwarded_total = 0
        self._notify_method = None
        self._notify_event = threading.Event()
        self._auto_block_method = None
        self._auto_blocked = False
        self._lock = threading.Lock()

    def reject_next(self, n: int):
        """Make the next *n* requests get an immediate connection reset.

        The proxy stays listening (port is open), but closes the TCP
        connection without sending any HTTP response.  After *n* requests
        are rejected, normal forwarding resumes automatically.
        """
        with self._lock:
            self._reject_remaining = n
        logger.info("CKB RPC proxy will reject next %d requests", n)

    def block_methods(self, methods):
        """Block specific JSON-RPC methods by name.

        Requests with a matching ``method`` field receive an HTTP 200
        response containing a JSON-RPC error object instead of being
        forwarded. All other RPC methods are forwarded normally.
        """
        with self._lock:
            self._method_errors = {
                method: (-1, "CKB RPC proxy: method blocked") for method in methods
            }
        logger.info("CKB RPC proxy blocking methods: %s", methods)

    def block_method_with_error(
        self, method, code=-1, message="CKB RPC proxy: method blocked"
    ):
        """Block one JSON-RPC method with a specific JSON-RPC error."""
        with self._lock:
            self._method_errors[method] = (code, message)
        logger.info(
            "CKB RPC proxy blocking method '%s' with error %s: %s",
            method,
            code,
            message,
        )

    def unblock_methods(self):
        """Clear method-level blocking; resume forwarding all methods."""
        with self._lock:
            self._method_errors.clear()
        logger.info("CKB RPC proxy unblocked all methods")

    def block_after(self, n: int):
        """Allow exactly *n* more requests through, then auto-block.

        After *n* requests have been forwarded, all subsequent requests
        get an immediate connection reset (TCP RST) until ``resume()``
        is called.
        """
        with self._lock:
            self._block_after = n
            self._forwarded_count = 0
        logger.info("CKB RPC proxy will block after %d requests", n)

    def resume(self):
        """Clear block_after state; resume normal forwarding."""
        with self._lock:
            self._block_after = None
        logger.info("CKB RPC proxy resumed normal forwarding")

    def notify_on_method(self, method_name: str):
        """Set up notification: when *method_name* is seen, set event.

        The request is still forwarded normally, but the event is set
        so the test thread can react (e.g. call ``block()``).
        """
        with self._lock:
            self._notify_method = method_name
            self._notify_event.clear()
        logger.info("CKB RPC proxy will notify on method '%s'", method_name)

    def wait_for_method(self, timeout: float = 30) -> bool:
        """Block until the notify method is seen. Returns True if seen."""
        return self._notify_event.wait(timeout=timeout)

    def auto_block_on_method(self, method_name: str):
        """When *method_name* is first seen, immediately close the listening
        socket from within the handler thread.

        This produces "Connection refused" for ALL subsequent TCP connections
        — the same error mode as ``block()``, but triggered deterministically
        without timing dependencies.  The current request gets a TCP RST,
        and ALL later requests fail at the TCP level.

        Call ``unblock()`` to restart the proxy afterwards.
        """
        with self._lock:
            self._auto_block_method = method_name
            self._auto_blocked = False
        logger.info("CKB RPC proxy will auto-block on method '%s'", method_name)

    @property
    def auto_blocked(self) -> bool:
        with self._lock:
            return self._auto_blocked

    def start(self, port: int = 0):
        """Start (or restart) the proxy on *port* (0 = auto-assign)."""
        proxy_ref = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            @staticmethod
            def _send_rst(conn):
                """Send TCP RST (Connection reset by peer) instead of graceful FIN."""
                try:
                    conn.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_LINGER,
                        struct.pack("ii", 1, 0),
                    )
                    conn.close()
                except Exception:
                    pass

            @staticmethod
            def _parse_rpc(body):
                """Parse JSON-RPC body, returning (method, id)."""
                try:
                    rpc = json.loads(body)
                    return rpc.get("method", ""), rpc.get("id", 0)
                except Exception:
                    return "", 0

            def do_POST(self):
                # --- Pre-body check: reject_next (no need to read body) ---
                with proxy_ref._lock:
                    if proxy_ref._reject_remaining > 0:
                        proxy_ref._reject_remaining -= 1
                        logger.info(
                            "CKB RPC proxy REJECTING request (%d remaining)",
                            proxy_ref._reject_remaining,
                        )
                        self._send_rst(self.connection)
                        return

                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                method, req_id = self._parse_rpc(body)

                # --- auto_block: close listening socket on trigger method ---
                with proxy_ref._lock:
                    if proxy_ref._auto_blocked:
                        self._send_rst(self.connection)
                        return
                    if (
                        proxy_ref._auto_block_method
                        and method == proxy_ref._auto_block_method
                    ):
                        proxy_ref._auto_block_method = None
                        proxy_ref._auto_blocked = True
                        logger.info(
                            "CKB RPC proxy AUTO-BLOCK triggered by '%s' "
                            "— closing listening socket",
                            method,
                        )
                        try:
                            proxy_ref._server.socket.close()
                        except Exception:
                            pass
                        self._send_rst(self.connection)
                        return

                # --- Method errors: return JSON-RPC error for specific methods ---
                with proxy_ref._lock:
                    method_error = proxy_ref._method_errors.get(method)
                    if method_error is not None:
                        code, message = method_error
                        logger.info(
                            "CKB RPC proxy BLOCKING method '%s' with error %s: %s",
                            method,
                            code,
                            message,
                        )
                        err_body = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": code,
                                    "message": message,
                                },
                            }
                        ).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(err_body)
                        return

                # --- block_after: RST after N forwarded requests ---
                with proxy_ref._lock:
                    if proxy_ref._block_after is not None:
                        if proxy_ref._forwarded_count >= proxy_ref._block_after:
                            logger.info(
                                "CKB RPC proxy BLOCKED request #%d (method=%s, limit=%d)",
                                proxy_ref._forwarded_count + 1,
                                method,
                                proxy_ref._block_after,
                            )
                            self._send_rst(self.connection)
                            return
                        proxy_ref._forwarded_count += 1

                # --- Notify trigger (one-shot) ---
                with proxy_ref._lock:
                    if proxy_ref._notify_method and method == proxy_ref._notify_method:
                        proxy_ref._notify_method = None
                        proxy_ref._notify_event.set()
                        logger.info("CKB RPC proxy TRIGGER: saw method '%s'", method)
                    proxy_ref._forwarded_total += 1
                    _cnt = proxy_ref._forwarded_total

                logger.info("CKB RPC proxy forwarding #%d: %s", _cnt, method or "?")

                # --- Forward to upstream CKB RPC ---
                try:
                    req = urllib.request.Request(
                        proxy_ref.target_url,
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        resp_body = resp.read()
                        self.send_response(resp.status)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(resp_body)
                except Exception as e:
                    logger.debug("Proxy forward error: %s", e)
                    self.send_error(502, "Proxy forwarding error")

            def log_message(self, format, *args):
                pass  # suppress per-request logs

        self._server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
        actual_port = self._server.server_address[1]
        self.port = actual_port
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("CKB RPC proxy started on %s → %s", self.url, self.target_url)

    def block(self):
        """Stop accepting connections — clients will get *Connection refused*."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            if self._thread:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None
            logger.info("CKB RPC proxy BLOCKED (port %s closed)", self.port)

    def unblock(self):
        """Re-open the proxy on the same port."""
        with self._lock:
            self._auto_blocked = False
            self._auto_block_method = None
        if self._server is not None:
            # Server object exists but socket may have been closed by auto_block
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            if self._thread:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None
        self.start(port=self.port)
        logger.info("CKB RPC proxy UNBLOCKED (port %s re-opened)", self.port)

    def stop(self):
        """Tear everything down."""
        self.block()
        self.port = None
        self.url = None
