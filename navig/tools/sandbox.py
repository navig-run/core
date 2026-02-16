"""
NAVIG Sandboxed Execution Environment

Docker-based command isolation for safe execution of untrusted commands.
Features:
- Container isolation per execution
- Resource limits (CPU, memory, disk)
- Network isolation options
- Timeout enforcement
- Output capture and streaming
"""
import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class SandboxStatus(Enum):
    """Status of sandbox execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"
    KILLED = "killed"


@dataclass
class SandboxConfig:
    """Configuration for sandbox environment."""
    
    # Resource limits
    memory_limit: str = "512m"       # Memory limit (e.g., "512m", "1g")
    cpu_limit: float = 1.0           # CPU cores limit
    disk_limit: str = "1g"           # Disk space limit
    
    # Timeouts
    execution_timeout: int = 300     # Max execution time in seconds
    startup_timeout: int = 30        # Container startup timeout
    
    # Network
    network_enabled: bool = False    # Allow network access
    network_mode: str = "none"       # "none", "bridge", "host"
    
    # Security
    read_only_root: bool = True      # Read-only root filesystem
    no_new_privileges: bool = True   # Prevent privilege escalation
    cap_drop: List[str] = field(default_factory=lambda: ["ALL"])
    
    # Image settings
    default_image: str = "python:3.11-slim"  # Default container image
    allowed_images: List[str] = field(default_factory=lambda: [
        "python:3.11-slim",
        "python:3.12-slim",
        "node:20-slim",
        "ubuntu:22.04",
        "alpine:latest",
    ])
    
    # Paths
    workspace_mount: Optional[str] = None  # Host path to mount as workspace
    output_dir: Optional[str] = None       # Directory for output files
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SandboxConfig':
        """Create config from dictionary."""
        return cls(
            memory_limit=data.get("memory_limit", "512m"),
            cpu_limit=data.get("cpu_limit", 1.0),
            disk_limit=data.get("disk_limit", "1g"),
            execution_timeout=data.get("execution_timeout", 300),
            startup_timeout=data.get("startup_timeout", 30),
            network_enabled=data.get("network_enabled", False),
            network_mode=data.get("network_mode", "none"),
            read_only_root=data.get("read_only_root", True),
            no_new_privileges=data.get("no_new_privileges", True),
            cap_drop=data.get("cap_drop", ["ALL"]),
            default_image=data.get("default_image", "python:3.11-slim"),
            allowed_images=data.get("allowed_images", []),
        )


@dataclass
class SandboxResult:
    """Result of sandboxed execution."""
    execution_id: str
    status: SandboxStatus
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    execution_time: float = 0.0
    container_id: Optional[str] = None
    error: Optional[str] = None
    output_files: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execution_time": self.execution_time,
            "container_id": self.container_id,
            "error": self.error,
            "output_files": self.output_files,
        }


class DockerSandbox:
    """
    Docker-based sandbox for isolated command execution.
    
    Usage:
        sandbox = DockerSandbox()
        result = await sandbox.execute("python script.py", image="python:3.11-slim")
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        """
        Initialize Docker sandbox.
        
        Args:
            config: Sandbox configuration
        """
        self.config = config or SandboxConfig()
        self._docker_available: Optional[bool] = None
    
    async def is_available(self) -> bool:
        """Check if Docker is available."""
        if self._docker_available is not None:
            return self._docker_available
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False
        
        return self._docker_available
    
    async def execute(
        self,
        command: str,
        image: Optional[str] = None,
        working_dir: str = "/workspace",
        env: Optional[Dict[str, str]] = None,
        files: Optional[Dict[str, str]] = None,  # filename -> content
        timeout: Optional[int] = None,
    ) -> SandboxResult:
        """
        Execute a command in a sandboxed container.
        
        Args:
            command: Command to execute
            image: Docker image to use
            working_dir: Working directory in container
            env: Environment variables
            files: Files to create in workspace (name -> content)
            timeout: Execution timeout override
            
        Returns:
            SandboxResult with execution details
        """
        import uuid
        execution_id = str(uuid.uuid4())[:8]
        
        # Check Docker availability
        if not await self.is_available():
            return SandboxResult(
                execution_id=execution_id,
                status=SandboxStatus.ERROR,
                error="Docker is not available",
            )
        
        # Validate image
        image = image or self.config.default_image
        if self.config.allowed_images and image not in self.config.allowed_images:
            return SandboxResult(
                execution_id=execution_id,
                status=SandboxStatus.ERROR,
                error=f"Image '{image}' not in allowed list",
            )
        
        # Create temporary workspace
        temp_dir = tempfile.mkdtemp(prefix=f"navig-sandbox-{execution_id}-")
        
        try:
            # Write files to workspace
            if files:
                for filename, content in files.items():
                    filepath = Path(temp_dir) / filename
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                    filepath.write_text(content)
            
            # Build docker run command
            docker_cmd = self._build_docker_command(
                execution_id=execution_id,
                image=image,
                command=command,
                workspace_path=temp_dir,
                working_dir=working_dir,
                env=env,
            )
            
            logger.debug(f"Sandbox command: {' '.join(docker_cmd)}")
            
            # Execute with timeout
            start_time = datetime.now()
            exec_timeout = timeout or self.config.execution_timeout
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    *docker_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=exec_timeout,
                    )
                    
                    execution_time = (datetime.now() - start_time).total_seconds()
                    
                    return SandboxResult(
                        execution_id=execution_id,
                        status=SandboxStatus.COMPLETED,
                        exit_code=proc.returncode,
                        stdout=stdout.decode("utf-8", errors="replace"),
                        stderr=stderr.decode("utf-8", errors="replace"),
                        execution_time=execution_time,
                        container_id=f"navig-sandbox-{execution_id}",
                    )
                    
                except asyncio.TimeoutError:
                    # Kill the container
                    await self._kill_container(execution_id)
                    
                    return SandboxResult(
                        execution_id=execution_id,
                        status=SandboxStatus.TIMEOUT,
                        error=f"Execution timed out after {exec_timeout}s",
                        execution_time=exec_timeout,
                        container_id=f"navig-sandbox-{execution_id}",
                    )
                    
            except Exception as e:
                return SandboxResult(
                    execution_id=execution_id,
                    status=SandboxStatus.ERROR,
                    error=str(e),
                )
                
        finally:
            # Cleanup temporary workspace
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp dir: {e}")
            
            # Cleanup container (in case it's still running)
            await self._cleanup_container(execution_id)
    
    def _build_docker_command(
        self,
        execution_id: str,
        image: str,
        command: str,
        workspace_path: str,
        working_dir: str,
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Build the docker run command with security options."""
        cmd = [
            "docker", "run",
            "--rm",  # Remove container after execution
            "--name", f"navig-sandbox-{execution_id}",
            
            # Resource limits
            "--memory", self.config.memory_limit,
            "--cpus", str(self.config.cpu_limit),
            
            # Security options
            "--security-opt", "no-new-privileges:true" if self.config.no_new_privileges else "no-new-privileges:false",
        ]
        
        # Read-only root
        if self.config.read_only_root:
            cmd.extend(["--read-only"])
            # Need tmpfs for temp files
            cmd.extend(["--tmpfs", "/tmp:size=100M"])
        
        # Drop capabilities
        for cap in self.config.cap_drop:
            cmd.extend(["--cap-drop", cap])
        
        # Network
        if not self.config.network_enabled:
            cmd.extend(["--network", "none"])
        elif self.config.network_mode != "bridge":
            cmd.extend(["--network", self.config.network_mode])
        
        # Mount workspace
        cmd.extend([
            "-v", f"{workspace_path}:{working_dir}",
            "-w", working_dir,
        ])
        
        # Environment variables
        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])
        
        # Image and command
        cmd.append(image)
        cmd.extend(["sh", "-c", command])
        
        return cmd
    
    async def _kill_container(self, execution_id: str):
        """Kill a running container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", f"navig-sandbox-{execution_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            logger.warning(f"Failed to kill container: {e}")
    
    async def _cleanup_container(self, execution_id: str):
        """Remove a container if it exists."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", f"navig-sandbox-{execution_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass  # Container might not exist
    
    async def execute_script(
        self,
        script_content: str,
        language: str = "python",
        **kwargs,
    ) -> SandboxResult:
        """
        Execute a script in sandboxed environment.
        
        Args:
            script_content: Script source code
            language: Programming language ("python", "node", "bash")
            **kwargs: Additional arguments for execute()
            
        Returns:
            SandboxResult
        """
        # Determine image and command based on language
        if language == "python":
            image = "python:3.11-slim"
            filename = "script.py"
            command = "python script.py"
        elif language in ("node", "javascript"):
            image = "node:20-slim"
            filename = "script.js"
            command = "node script.js"
        elif language in ("bash", "sh"):
            image = "alpine:latest"
            filename = "script.sh"
            command = "sh script.sh"
        else:
            return SandboxResult(
                execution_id="error",
                status=SandboxStatus.ERROR,
                error=f"Unsupported language: {language}",
            )
        
        return await self.execute(
            command=command,
            image=kwargs.get("image", image),
            files={filename: script_content},
            **{k: v for k, v in kwargs.items() if k != "image"},
        )


# Convenience function
async def sandboxed_execute(
    command: str,
    config: Optional[SandboxConfig] = None,
    **kwargs,
) -> SandboxResult:
    """
    Execute a command in a sandboxed environment.
    
    Args:
        command: Command to execute
        config: Sandbox configuration
        **kwargs: Additional arguments
        
    Returns:
        SandboxResult
    """
    sandbox = DockerSandbox(config)
    return await sandbox.execute(command, **kwargs)


def is_sandbox_available() -> bool:
    """Check if Docker sandbox is available (sync check)."""
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
