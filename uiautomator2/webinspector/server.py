import base64
import io
import json
import logging
import os
import pathlib
import re
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET
from urllib.parse import urlparse

import uiautomator2 as u2

logger = logging.getLogger(__name__)

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

HERE = pathlib.Path(__file__).parent


def _parse_bounds(bounds_str: str) -> Dict[str, int]:
    match = _BOUNDS_RE.fullmatch(bounds_str)
    if not match:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _node_to_dict(node: ET.Element) -> Dict[str, Any]:
    bounds = _parse_bounds(node.get("bounds", ""))
    children = [_node_to_dict(child) for child in node]
    return {
        "tag": node.tag,
        "text": node.get("text", ""),
        "resource-id": node.get("resource-id", ""),
        "class": node.get("class", ""),
        "package": node.get("package", ""),
        "content-desc": node.get("content-desc", ""),
        "checkable": node.get("checkable") == "true",
        "checked": node.get("checked") == "true",
        "clickable": node.get("clickable") == "true",
        "enabled": node.get("enabled") == "true",
        "focusable": node.get("focusable") == "true",
        "focused": node.get("focused") == "true",
        "scrollable": node.get("scrollable") == "true",
        "long-clickable": node.get("long-clickable") == "true",
        "password": node.get("password") == "true",
        "selected": node.get("selected") == "true",
        "displayed": node.get("displayed") == "true",
        "bounds": bounds,
        "children": children,
        "child_count": len(children),
    }


def _build_node_list(items: list, depth: int = 0) -> list:
    result = []
    for item in items:
        result.append({"data": item, "depth": depth})
        result.extend(_build_node_list(item.get("children", []), depth + 1))
    return result


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return b"{}"
    return handler.rfile.read(length)


class InspectorHandler(BaseHTTPRequestHandler):
    _device: Optional[u2.Device] = None

    def log_message(self, format, *args):
        logger.debug(format, *args)

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_png(self, png_bytes: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(png_bytes)))
        self.end_headers()
        self.wfile.write(png_bytes)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_device(self) -> u2.Device:
        if InspectorHandler._device is None:
            InspectorHandler._device = u2.connect()
        return InspectorHandler._device

    def _handle_api_get(self, path: str):
        try:
            d = self._get_device()
            if path == "/api/info":
                self._send_json(d.info)
            elif path == "/api/current":
                self._send_json(d.app_current())
            elif path == "/api/screenshot":
                img = d.screenshot()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                self._send_png(buf.getvalue())
            elif path == "/api/hierarchy":
                xml_str = d.dump_hierarchy()
                root = ET.fromstring(xml_str.encode("utf-8"))
                tree = _node_to_dict(root)
                flat = _build_node_list(tree.get("children", []))
                self._send_json({"tree": tree, "flat": flat})
            elif path == "/api/refresh":
                info = d.info
                current = d.app_current()
                img = d.screenshot()
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                screenshot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                xml_str = d.dump_hierarchy()
                root = ET.fromstring(xml_str.encode("utf-8"))
                tree = _node_to_dict(root)
                flat = _build_node_list(tree.get("children", []))
                self._send_json({
                    "info": info,
                    "current": current,
                    "screenshot": screenshot_b64,
                    "hierarchy": {"tree": tree, "flat": flat},
                })
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.error("API error: %s", traceback.format_exc())
            self._send_json({"error": str(e)}, 500)

    def _handle_api_post(self, path: str):
        try:
            d = self._get_device()
            body = json.loads(_read_body(self))
            if path == "/api/click":
                x, y = body["x"], body["y"]
                d.click(x, y)
                self._send_json({"ok": True, "x": x, "y": y})
            elif path == "/api/click-element":
                bounds = body.get("bounds")
                cx = bounds["x"] + bounds["width"] // 2
                cy = bounds["y"] + bounds["height"] // 2
                d.click(cx, cy)
                self._send_json({"ok": True, "x": cx, "y": cy})
            elif path == "/api/double-click":
                x, y = body["x"], body["y"]
                d.double_click(x, y)
                self._send_json({"ok": True, "x": x, "y": y})
            elif path == "/api/long-click":
                x, y = body["x"], body["y"]
                d.long_click(x, y)
                self._send_json({"ok": True, "x": x, "y": y})
            elif path == "/api/input":
                text = body.get("text", "")
                d.send_keys(text, clear=body.get("clear", False))
                self._send_json({"ok": True, "text": text})
            elif path == "/api/clear":
                d.clear_text()
                self._send_json({"ok": True})
            elif path == "/api/swipe":
                fx, fy = body["fx"], body["fy"]
                tx, ty = body["tx"], body["ty"]
                d.swipe(fx, fy, tx, ty)
                self._send_json({"ok": True})
            elif path == "/api/back":
                d.press("back")
                self._send_json({"ok": True})
            elif path == "/api/home":
                d.press("home")
                self._send_json({"ok": True})
            elif path == "/api/keyevent":
                d.press(body.get("key", "enter"))
                self._send_json({"ok": True})
            elif path == "/api/shell":
                result = d.shell(body.get("cmd", "")).output
                self._send_json({"ok": True, "output": result})
            elif path == "/api/app-start":
                d.app_start(body["package"])
                self._send_json({"ok": True})
            elif path == "/api/app-stop":
                d.app_stop(body["package"])
                self._send_json({"ok": True})
            elif path == "/api/screenshot-area":
                x, y, w, h = body["x"], body["y"], body["w"], body["h"]
                img = d.screenshot()
                crop = img.crop((x, y, x + w, y + h))
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
                self._send_png(buf.getvalue())
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.error("API error: %s", traceback.format_exc())
            self._send_json({"error": str(e)}, 500)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/"):
            self._handle_api_get(path)
        elif path == "" or path == "/":
            html_path = HERE / "static" / "index.html"
            if html_path.exists():
                self._send_html(html_path.read_text(encoding="utf-8"))
            else:
                self._send_html("<h1>Inspector</h1><p>index.html not found</p>")
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/"):
            self._handle_api_post(path)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def run_inspector(host: str = "127.0.0.1", port: int = 18888):
    server = HTTPServer((host, port), InspectorHandler)
    print(f"Web Inspector started at http://{host}:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.shutdown()
