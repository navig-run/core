import subprocess
import json
import threading
import logging
import os
from typing import Dict, Any, Optional

class PluginInstance:
    def __init__(self, name: str, entrypoint: str, cwd: str):
        self.name = name
        self.entrypoint = entrypoint
        self.cwd = cwd
        self.process: Optional[subprocess.Popen] = None
        self.requests: Dict[str, Any] = {}
        self.lock = threading.Lock()
        self.logger = logging.getLogger(f"navig.plugins.{name}")

    def start(self):
        cmd = self.entrypoint.split()
        self.logger.info(f"Starting plugin process: {cmd}")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.cwd,
            bufsize=1
        )
        # Start reading stdout in a separate thread
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        while self.process and self.process.poll() is None:
            line = self.process.stdout.readline()
            if line:
                try:
                    message = json.loads(line)
                    self._handle_message(message)
                except json.JSONDecodeError:
                    self.logger.warning(f"Received non-JSON from plugin: {line.strip()}")

    def _read_stderr(self):
        while self.process and self.process.poll() is None:
            line = self.process.stderr.readline()
            if line:
                self.logger.error(f"STDERR: {line.strip()}")

    def _handle_message(self, message: Dict[str, Any]):
        msg_id = message.get("id")
        if msg_id in self.requests:
            # It's a response to our request
            future = self.requests.pop(msg_id)
            if "error" in message:
                future.set_exception(Exception(message["error"]))
            else:
                future.set_result(message.get("result"))
        else:
            # It's a notification or request from the plugin (not implemented yet)
            pass

    def send_request(self, method: str, params: Dict[str, Any] = None) -> Any:
        import uuid
        import concurrent.futures
        
        req_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id
        }
        
        future = concurrent.futures.Future()
        with self.lock:
            self.requests[req_id] = future
            if self.process and self.process.stdin:
                self.process.stdin.write(json.dumps(payload) + "\n")
                self.process.stdin.flush()
            else:
                raise RuntimeError("Plugin process not running")
        
        return future.result(timeout=10) # 10s timeout

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

class PluginManager:
    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        self.plugins: Dict[str, PluginInstance] = {}

    def discover_and_load(self):
        if not os.path.exists(self.plugin_dir):
            return
            
        for d in os.listdir(self.plugin_dir):
            plugin_path = os.path.join(self.plugin_dir, d)
            manifest_path = os.path.join(plugin_path, "navig.plugin.json")
            if os.path.exists(manifest_path):
                self._load_plugin(plugin_path, manifest_path)

    def _load_plugin(self, path: str, manifest_path: str):
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            name = manifest['name']
            entrypoint = manifest['entrypoint']
            
            instance = PluginInstance(name, entrypoint, path)
            instance.start()
            self.plugins[name] = instance
            print(f"🔌 Plugin loaded: {name}")
            
        except Exception as e:
            print(f"❌ Failed to load plugin at {path}: {e}")

    def execute_skill(self, plugin_name: str, method: str, params: Dict[str, Any]):
        if plugin_name in self.plugins:
            return self.plugins[plugin_name].send_request(method, params)
        raise ValueError(f"Plugin {plugin_name} not found")
